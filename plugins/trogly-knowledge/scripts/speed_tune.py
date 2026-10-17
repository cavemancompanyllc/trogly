"""Turn order for a team, worked out the way the game does it: a speed tune check.

The tick rules come from the game's battle code (TickManager in the 2026-10-05 build) and its own constants
(`battle` in the knowledge file):

- every tick, each champion's turn meter grows by SPD x stamina_by_tick (0.07), in the game's 32.32 fixed point
- once the fullest turn meter reaches stamina_to_turn (100), that champion acts; a tie between the two teams goes to
  the first team (the player's) when any of its champions is at the top, and a tie within a team goes to slot order
- the acting champion's turn meter goes back to 0 when its turn starts (overflow is lost)

Skill effects on turn meter and SPD (fills, reductions, Increase/Decrease SPD, extra turns) come from the game's skill
data, with booked cooldowns from the champion's skill levels. What isn't simulated is listed in the result, so the
answer never claims more than it checks. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ONE = 1 << 32

# Increase/Decrease SPD buffs and debuffs change SPD by this share of the champion's base SPD.
SPEED_STATUSES = {"IncreaseSpeed15": 0.15, "IncreaseSpeed30": 0.30, "DecreaseSpeed15": -0.15, "DecreaseSpeed30": -0.30}

# Where an aura works: one with no area works everywhere; the game treats its three arenas as one.
ARENA_AREAS = {"Arena", "Arena3X3", "LiveArena"}

SELF_TARGETS = {"Producer", "RelationProducer", "Owner", "ActiveHero"}
SKILL_TARGET_TARGETS = {"Target", "RelationTarget", "RelationTargetDuplicates"}
ENEMY_SKILL_TARGETS = {"AliveEnemies", "AllEnemies", "AliveEnemiesIncludeInvisible"}
ALLY_SKILL_TARGETS = {"AliveAllies", "AllAllies", "AliveAlliesExceptProducer", "AllAlliesExceptProducer"}
DEAD_SKILL_TARGETS = {"DeadAllies", "DeadEnemies", "DeadAlliesIncludeBlockRevive"}

# Set effects that can change turn order mention one of these.
SET_TURN_WORDS = re.compile(r"turn meter|extra turn|SPD", re.IGNORECASE)

DEFAULT_TURNS = 12
MAX_TURNS = 60
MAX_TICKS = 100_000


class SpeedTuneError(Exception):
    """A problem with the request the player or Claude can fix."""


def to_raw(value: float) -> int:
    """The game's Fixed.FromDouble: truncates."""
    return int(value * ONE)


def mul(left: int, right: int) -> int:
    """The game's Fixed multiply: floors the 128-bit product."""
    return (left * right) >> 32


def aura_applies(aura: dict[str, Any], area: str | None) -> bool:
    """LeaderSkill.ActiveIn: no area means everywhere; arena auras work in all three arenas."""
    if not aura.get("area"):
        return True
    if not area:
        return False
    if aura["area"] in ARENA_AREAS and area in ARENA_AREAS:
        return True
    return aura["area"] == area


def stamina_amount(formula: str | None) -> tuple[str, float] | None:
    """A turn meter formula as (basis, share): '0.3*MAX_STAMINA' -> ('max', 0.3), meaning 30% of a full turn meter;
    '0.5*TRG_STAMINA' -> ('current', 0.5), half of the target's current turn meter. None when it's anything else."""
    if not formula:
        return None
    text = formula.replace(" ", "")
    for variable, basis in (("MAX_STAMINA", "max"), ("TRG_STAMINA", "current")):
        match = re.fullmatch(rf"(?:([\d.]+)\*)?{variable}(?:\*([\d.]+))?", text)
        if match:
            return basis, float(match.group(1) or match.group(2) or 1)
    return None


def describe_amount(amount: tuple[str, float]) -> str:
    basis, share = amount
    if basis == "max":
        return f"by {round(share * 100)}%"
    return "fully" if share == 1 else f"by {round(share * 100)}% of its current turn meter"


@dataclass
class Skill:
    label: str
    name: str
    cooldown: int
    base_cooldown: int
    start_cooldown: int
    targets: str | None
    effects: list[dict[str, Any]]


@dataclass
class Fighter:
    name: str
    side: str
    slot: int
    base_speed: int
    speed: int
    skills: list[Skill] = field(default_factory=list)
    priority: list[str] = field(default_factory=list)
    target: str | None = None
    stamina: int = 0
    cooldowns: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, int] = field(default_factory=dict)
    turns_taken: int = 0

    def speed_now(self) -> int:
        bonus = sum(mul(self.base_speed, to_raw(SPEED_STATUSES[name])) for name in self.statuses if name in SPEED_STATUSES)
        return max(self.speed + bonus, 0)


class SpeedTune:
    """Builds the fighters from the knowledge file and the account state, then plays out the turns."""

    def __init__(self, knowledge: Any, roster: dict[str, Any]):
        self.knowledge = knowledge
        self.roster = roster
        battle = knowledge.raw.get("battle")
        if not battle:
            raise SpeedTuneError("Trogly's knowledge file comes from an older Agent without turn meter data. Run refresh_knowledge with RAID open (after updating the Agent if that fails).")
        self.by_tick = to_raw(battle.get("stamina_by_tick") or 0.07)
        self.to_turn = to_raw(battle.get("stamina_to_turn") or 100)
        self.max_stamina = self.to_turn
        self.notes: list[str] = []
        self.not_simulated: list[str] = []

    # Setup

    def team_fighters(self, team: list[dict[str, Any]], area: str | None) -> tuple[list[Fighter], dict[str, Any] | None]:
        if not team:
            raise SpeedTuneError("Give at least one champion for the team, leader first.")
        members = []
        for slot, entry in enumerate(team, start=1):
            entry = entry if isinstance(entry, dict) else {"champion": str(entry)}
            champion = self.roster_champion(str(entry.get("champion") or ""))
            members.append((slot, entry, champion))

        leader_type = self.knowledge.types.get(members[0][2]["type_id"]) or {}
        leader = self.leader_aura(leader_type, area)

        fighters = []
        for slot, entry, champion in members:
            champion_type = self.knowledge.types.get(champion["type_id"]) or {}
            total = (champion.get("calculated_stats") or {}).get("total") or {}
            base = (champion.get("calculated_stats") or {}).get("base") or {}
            if "speed" not in total or "speed" not in base:
                raise SpeedTuneError(f"{champion['name']} has no calculated stats. Refresh the account first.")
            base_raw = to_raw(base["speed"])
            speed_raw = to_raw(entry["speed"]) if entry.get("speed") is not None else to_raw(total["speed"])
            if entry.get("speed") is not None:
                self.notes.append(f"{champion['name']}: SPD set to {entry['speed']} for this check (Trogly's own figure is {round(total['speed'])}).")
            if leader and leader["applies"] and self.aura_reaches(leader, champion_type):
                aura = leader["raw"]
                speed_raw += aura if leader["absolute"] else mul(base_raw, aura)
            fighter = Fighter(champion["name"], "team", slot, base_raw, speed_raw, target=entry.get("target"))
            fighter.skills = self.skills_for(champion, champion_type)
            fighter.priority = self.priority_for(fighter, entry.get("priority"))
            fighter.cooldowns = {skill.label: skill.start_cooldown for skill in fighter.skills}
            self.flag_unsimulated(fighter, champion, champion_type)
            fighters.append(fighter)
        return fighters, leader

    def enemy_fighters(self, enemies: list[dict[str, Any]]) -> list[Fighter]:
        fighters = []
        for slot, entry in enumerate(enemies or [], start=1):
            if not isinstance(entry, dict) or entry.get("speed") is None:
                raise SpeedTuneError("Each enemy needs a name and a speed, for example {\"name\": \"Their leader\", \"speed\": 280}.")
            raw = to_raw(float(entry["speed"]))
            fighter = Fighter(str(entry.get("name") or f"Enemy {slot}"), "enemy", slot, raw, raw)
            fighter.skills = [Skill("A1", "basic attack", 0, 0, 0, "AliveEnemies", [])]
            fighter.priority = ["A1"]
            fighters.append(fighter)
        if fighters:
            self.notes.append("Enemies only use their basic attack and don't change anyone's turn meter or SPD; their SPD is used as given (aura and buffs included or not, as you entered it).")
        return fighters

    def roster_champion(self, reference: str) -> dict[str, Any]:
        key = reference.strip().lower()
        if not key:
            raise SpeedTuneError("Each team member needs a champion name or id.")
        champions = self.roster.get("champions") or []
        match = next((c for c in champions if str(c.get("id")) == key), None)
        if match:
            return match
        named = [c for c in champions if (c.get("name") or "").lower() == key] or [
            c for c in champions if key in (c.get("name") or "").lower()]
        if not named:
            raise SpeedTuneError(f"No owned champion called '{reference}'.")
        if len({c["name"] for c in named}) > 1:
            raise SpeedTuneError(f"'{reference}' matches {', '.join(sorted({c['name'] for c in named})[:8])}. Use the full name.")
        return sorted(named, key=lambda c: (-c.get("stars", 0), -c.get("level", 0), -c.get("awakening_level", 0)))[0]

    def leader_aura(self, leader_type: dict[str, Any], area: str | None) -> dict[str, Any] | None:
        speed_auras = [a for a in leader_type.get("leader_skills") or [] if a.get("stat") == "Speed"]
        if not speed_auras:
            return None
        aura = speed_auras[0]
        applies = aura_applies(aura, area)
        amount = aura["amount"]
        text = f"SPD +{round(amount)}" if aura.get("absolute") else f"SPD +{round(amount * 100)}%"
        where = aura.get("area") or "all battles"
        scope = " for " + " ".join(filter(None, [aura.get("affinity"), aura.get("faction")])) + " allies" if aura.get("affinity") or aura.get("faction") else ""
        return {
            "leader": leader_type.get("name"),
            "aura": f"{text} in {where}{scope}",
            "applies": applies,
            "why": "works here" if applies else f"only works in {where}, not in {area or 'this content'}",
            "absolute": bool(aura.get("absolute")),
            "raw": to_raw(amount),
            "affinity": aura.get("affinity"),
            "faction": aura.get("faction"),
        }

    @staticmethod
    def aura_reaches(leader: dict[str, Any], champion_type: dict[str, Any]) -> bool:
        if leader.get("affinity") and champion_type.get("affinity") != leader["affinity"]:
            return False
        if leader.get("faction") and champion_type.get("faction") != leader["faction"]:
            return False
        return True

    def skills_for(self, champion: dict[str, Any], champion_type: dict[str, Any]) -> list[Skill]:
        levels = champion.get("skill_levels") or {}
        skills = []
        index = 0
        for skill_id in champion_type.get("skill_ids") or []:
            skill = self.knowledge.skills.get(str(skill_id))
            if not skill or not skill.get("name") or "[P]" in skill["name"]:
                continue
            index += 1
            level = int(levels.get(str(skill_id), 1))
            booked = sum(int(round(b["value"])) for b in (skill.get("level_bonuses") or [])[: max(level - 1, 0)] if b["type"] == "Cooldown")
            cooldown = max((skill.get("cooldown") or 0) - booked, 0)
            skills.append(Skill(f"A{index}", skill["name"], cooldown, skill.get("cooldown") or 0,
                                int(skill.get("start_cooldown") or 0), skill.get("targets"), skill.get("effects") or []))
        return skills

    def priority_for(self, fighter: Fighter, requested: list[str] | None) -> list[str]:
        labels = {skill.label for skill in fighter.skills}
        by_name = {skill.name.lower(): skill.label for skill in fighter.skills}
        if requested:
            order = []
            for item in requested:
                label = str(item).strip().upper() if str(item).strip().upper() in labels else by_name.get(str(item).strip().lower())
                if not label:
                    raise SpeedTuneError(f"{fighter.name} has no skill '{item}'. Skills: {', '.join(f'{s.label} {s.name}' for s in fighter.skills)}.")
                order.append(label)
            if "A1" not in order:
                order.append("A1")
            return order
        # Default: the newest cooldown skill first (A4, A3, A2), revives after other cooldown skills, then A1. Skills that
        # need a dead ally never fire here (nobody dies in this check).
        usable = [s for s in fighter.skills if s.targets not in DEAD_SKILL_TARGETS]
        revives = {s.label for s in usable if any(e["kind"] == "Revive" for e in s.effects)}
        return [s.label for s in sorted(usable, key=lambda s: (s.cooldown == 0, s.label in revives, -int(s.label[1:])))]

    def flag_unsimulated(self, fighter: Fighter, champion: dict[str, Any], champion_type: dict[str, Any]) -> None:
        for skill_id in champion_type.get("skill_ids") or []:
            skill = self.knowledge.skills.get(str(skill_id))
            if not skill or not skill.get("name") or "[P]" not in skill["name"]:
                continue
            touches = any(e["kind"] in ("IncreaseStamina", "ReduceStamina", "ExtraTurn", "ChangeStaminaMultiplier") or
                          any(s["name"] in SPEED_STATUSES for s in e.get("statuses") or []) for e in skill.get("effects") or [])
            if touches:
                self.not_simulated.append(f"{fighter.name}'s passive {skill['name']} changes turn meter, SPD, or turns.")
        for item in (champion.get("calculated_stats") or {}).get("sets") or []:
            if item.get("active") and item.get("skill_effect") and SET_TURN_WORDS.search(item.get("bonus") or ""):
                self.not_simulated.append(f"{fighter.name}'s {item.get('name')} set effect ({item.get('bonus')}).")

    # Battle

    def run(self, fighters: list[Fighter], turns: int) -> list[dict[str, Any]]:
        log = []
        ticks = 0
        priority: list[Fighter] = []
        while len(log) < turns:
            if priority:
                actor = priority.pop(0)
            else:
                actor = None
                while actor is None:
                    ticks += 1
                    if ticks > MAX_TICKS:
                        raise SpeedTuneError("Nobody reached a turn; check the speeds.")
                    top, team_at_top = -1, 0
                    for fighter in fighters:
                        fighter.stamina += mul(fighter.speed_now(), self.by_tick)
                        if fighter.stamina >= top:
                            team_at_top = team_at_top if fighter.stamina == top else 0
                            top = fighter.stamina
                            team_at_top += fighter.side == "team"
                    if top >= self.to_turn:
                        side = "team" if team_at_top else "enemy"
                        actor = next(f for f in fighters if f.stamina == top and f.side == side)
                actor.stamina = 0
            log.append(self.take_turn(actor, fighters, ticks, priority))
        return log

    def take_turn(self, actor: Fighter, fighters: list[Fighter], ticks: int, priority: list[Fighter]) -> dict[str, Any]:
        actor.turns_taken += 1
        for label in actor.cooldowns:
            actor.cooldowns[label] = max(actor.cooldowns[label] - 1, 0) if actor.turns_taken > 1 else actor.cooldowns[label]
        skill = next((s for label in actor.priority for s in actor.skills if s.label == label and actor.cooldowns.get(label, 0) == 0),
                     actor.skills[0] if actor.skills else None)
        spd_at_turn = actor.speed_now()
        effects = self.use_skill(actor, skill, fighters, priority) if skill else []
        if skill and skill.cooldown:
            actor.cooldowns[skill.label] = skill.cooldown
        for name in list(actor.statuses):
            actor.statuses[name] -= 1
            if actor.statuses[name] <= 0:
                del actor.statuses[name]
        return {
            "turn": None,
            "tick": ticks,
            "champion": actor.name,
            "side": actor.side,
            "their_turn": actor.turns_taken,
            "spd": round(spd_at_turn / ONE, 2),
            "skill": f"{skill.label} {skill.name}" if skill else "-",
            "effects": effects,
            "turn_meters_after": {f.name: round(f.stamina * 100 / self.to_turn, 1) for f in fighters},
        }

    def use_skill(self, actor: Fighter, skill: Skill, fighters: list[Fighter], priority: list[Fighter]) -> list[str]:
        allies = [f for f in fighters if f.side == actor.side]
        enemies = [f for f in fighters if f.side != actor.side]
        chosen = self.pick_target(actor, skill, allies, enemies)
        done = []
        for effect in skill.effects:
            kind = effect["kind"]
            statuses = [s for s in effect.get("statuses") or [] if s["name"] in SPEED_STATUSES]
            relevant = kind in ("IncreaseStamina", "ReduceStamina", "ExtraTurn") or statuses
            if not relevant:
                continue
            if effect.get("condition"):
                self.note_once(f"{actor.name}'s {skill.name}: {kind} only happens when {effect['condition']}; not simulated.")
                continue
            chance = effect.get("chance")
            targets = self.effect_targets(actor, effect, chosen, allies, enemies)
            if targets is None:
                self.note_once(f"{actor.name}'s {skill.name}: {kind} on {effect.get('target')} isn't simulated.")
                continue
            hostile = bool(targets) and targets[0].side != actor.side
            if chance is not None and chance < 0.999 and not hostile:
                self.note_once(f"{actor.name}'s {skill.name}: {kind} has a {round(chance * 100)}% chance; the check leaves it out.")
                continue
            if hostile:
                self.note_once("Effects on enemies (turn meter reduction, Decrease SPD) are assumed to land; ACC against RES isn't checked.")
            names = ", ".join(t.name for t in targets) or "nobody"
            if kind == "IncreaseStamina" or kind == "ReduceStamina":
                amount = stamina_amount(effect.get("multiplier"))
                if amount is None:
                    self.note_once(f"{actor.name}'s {skill.name}: turn meter formula {effect.get('multiplier')} isn't simulated.")
                    continue
                for target in targets:
                    basis = self.max_stamina if amount[0] == "max" else target.stamina
                    change = mul(basis, to_raw(amount[1]))
                    target.stamina = target.stamina + change if kind == "IncreaseStamina" else max(target.stamina - change, 0)
                word = "fills" if kind == "IncreaseStamina" else "reduces"
                done.append(f"{word} turn meter {describe_amount(amount)}: {names}")
            elif kind == "ExtraTurn":
                if actor not in priority:
                    priority.insert(0, actor)
                done.append("extra turn")
            for status in statuses:
                for target in targets:
                    target.statuses[status["name"]] = max(target.statuses.get(status["name"], 0), int(status["turns"]))
                done.append(f"{status['name']} ({status['turns']} turns): {names}")
        return done

    def pick_target(self, actor: Fighter, skill: Skill, allies: list[Fighter], enemies: list[Fighter]) -> Fighter | None:
        if skill.targets in ALLY_SKILL_TARGETS:
            pool = [a for a in allies if not (skill.targets.endswith("ExceptProducer") and a is actor)]
            if actor.target:
                named = next((a for a in pool if a.name.lower() == actor.target.lower()), None)
                if named:
                    return named
            return min(pool, key=lambda a: (a.stamina, a.slot)) if pool else None
        if skill.targets in ENEMY_SKILL_TARGETS:
            return max(enemies, key=lambda e: (e.stamina, -e.slot)) if enemies else None
        return actor if skill.targets == "Producer" else None

    def effect_targets(self, actor: Fighter, effect: dict[str, Any], chosen: Fighter | None,
                       allies: list[Fighter], enemies: list[Fighter]) -> list[Fighter] | None:
        target = effect.get("target")
        if target in SELF_TARGETS:
            group = [actor]
        elif target in ("AllAllies", "OwnerAllies"):
            group = list(allies)
        elif target == "AllEnemies":
            group = list(enemies)
        elif target in SKILL_TARGET_TARGETS:
            group = [chosen] if chosen else []
        elif target == "AllyWithLowestStamina":
            group = [min(allies, key=lambda a: (a.stamina, a.slot))]
        elif target == "AllyWithHighestStamina":
            group = [max(allies, key=lambda a: (a.stamina, -a.slot))]
        elif target == "EnemyWithHighestStamina":
            group = [max(enemies, key=lambda e: (e.stamina, -e.slot))] if enemies else []
        elif target == "EnemyWithLowestStamina":
            group = [min(enemies, key=lambda e: (e.stamina, e.slot))] if enemies else []
        else:
            return None
        if effect.get("exclude") == "Producer":
            group = [g for g in group if g is not actor]
        elif effect.get("exclude") == "Target" and chosen is not None:
            group = [g for g in group if g is not chosen]
        return group

    def note_once(self, text: str) -> None:
        if text not in self.not_simulated:
            self.not_simulated.append(text)


def speed_tune(knowledge: Any, roster: dict[str, Any], team: list[dict[str, Any]], area: str | None = "Arena",
               enemies: list[dict[str, Any]] | None = None, turns: int = DEFAULT_TURNS) -> dict[str, Any]:
    tune = SpeedTune(knowledge, roster)
    fighters, leader = tune.team_fighters(team, area or None)
    fighters += tune.enemy_fighters(enemies or [])
    turns = max(1, min(int(turns or DEFAULT_TURNS), MAX_TURNS))
    log = tune.run(fighters, turns)
    for number, entry in enumerate(log, start=1):
        entry["turn"] = number

    team_view = []
    for fighter in (f for f in fighters if f.side == "team"):
        team_view.append({
            "slot": fighter.slot,
            "champion": fighter.name,
            "battle_spd": round(fighter.speed / ONE, 2),
            "base_spd": round(fighter.base_speed / ONE),
            "skills": [
                {"skill": f"{s.label} {s.name}", "cooldown": s.cooldown or None,
                 **({"booked_from": s.base_cooldown} if s.base_cooldown != s.cooldown else {}),
                 **({"starts_on_cooldown": s.start_cooldown} if s.start_cooldown else {})}
                for s in fighter.skills],
            "priority": fighter.priority,
        })
    return {
        "area": area or "anywhere",
        "leader_aura": leader,
        "team": team_view,
        "turns": log,
        "rules": [
            "Turn meter grows by SPD x 0.07 per tick; at 100 the fullest champion acts and drops to 0 (game code).",
            "Ties between teams go to the player's team; ties within a team go to slot order (game code).",
            "Increase SPD / Decrease SPD change SPD by 15% or 30% of base SPD; buffs tick down at the end of the holder's turn, including the turn they were placed on themselves.",
            "Each champion uses the first skill in its priority list that is off cooldown; cooldowns count down at the start of the champion's turn. Without a priority list: A4, A3, A2 (revives after the others), then A1.",
            "Nobody dies, so revives only do their other effects; effects with a condition or a chance under 100% on your own team are left out.",
            "SPD is Trogly's calculated SPD (not yet checked against the game's champion screen) plus the leader's SPD aura where it works.",
        ],
        "notes": tune.notes,
        "not_simulated": tune.not_simulated,
    }
