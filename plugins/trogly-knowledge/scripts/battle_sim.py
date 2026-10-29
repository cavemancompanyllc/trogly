"""Whole-battle simulator: turn order, damage, ACC against RES, buffs and debuffs, cooldowns, and the boss's skills and
passives, played many times with a seeded random generator. Design and sources: apps/trogly/docs/BATTLE-SIM-DESIGN.md.

Turn order is the speed tune's (game code, checked in real fights). Damage, defence, hit types and resists come from
`battle_rules` (game code). Skill effects come from the game's own skill data in the knowledge file, with formulas and
conditions evaluated by `formula`. Whatever the simulator doesn't model is listed under `not_simulated` with a
coverage score per champion, so results never claim more than they check. Skill choice is code, not AI: the game's
auto-battle sense (no revive with nobody dead, no heal at full HP) plus optional per-champion presets. Stdlib only.
"""

from __future__ import annotations

import random
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

import battle_rules as rules
import formula
from speed_tune import ONE, aura_applies, mul, to_raw

CLAN_BOSS_REGION = 401
CLAN_BOSS_TIERS = ["Easy", "Normal", "Hard", "Brutal", "Nightmare", "UltraNightmare"]
CLAN_BOSS_AREA = "AllianceBoss"
# The Demon Lord opens with its first AoE, then the second, then the stun, and repeats (A2, A3, A1 with 3-turn
# cooldowns). Assumed from play; checked against logged fights in story #690.
CLAN_BOSS_PRIORITY = ["A2", "A3", "A1"]
DEFAULT_BOSS_TURN_LIMIT = 300
DEFAULT_RUNS = 10
MAX_RUNS = 500
MAX_TICKS = 2_000_000
TIMELINE_LIMIT = 400

HEAL_THRESHOLD = 0.8
STAT_KEYS = ("health", "attack", "defence", "speed", "resistance", "accuracy", "critical_chance", "critical_damage",
             "critical_heal", "ignore_defence")
FORMULA_STATS = {"ATK": "attack", "DEF": "defence", "HP": "health", "SPD": "speed", "ACC": "accuracy", "RES": "resistance"}

# Stat buffs and debuffs: (stat, sign). The share is the number in the status name. A percent of the champion's battle
# stat, as measured for SPD (SPEED-TUNE-DESIGN.md); assumed the same for the other stats.
STAT_STATUSES = {
    "IncreaseAttack": ("attack", 1), "DecreaseAttack": ("attack", -1),
    "IncreaseDefence": ("defence", 1), "DecreaseDefence": ("defence", -1),
    "IncreaseSpeed": ("speed", 1), "DecreaseSpeed": ("speed", -1),
    "IncreaseAccuracy": ("accuracy", 1), "DecreaseAccuracy": ("accuracy", -1),
    "IncreaseResistance": ("resistance", 1), "DecreaseResistance": ("resistance", -1),
}
# Crit buffs and debuffs add or remove points: Increase C.RATE 30% is +0.30.
POINT_STATUSES = {
    "IncreaseCriticalChance": ("critical_chance", 1), "DecreaseCriticalChance": ("critical_chance", -1),
    "IncreaseCriticalDamage": ("critical_damage", 1), "DecreaseCriticalDamage": ("critical_damage", -1),
}
SKIP_TURN = {"Stun", "Freeze", "Sleep", "Fear1", "Fear2", "Petrification"}
POISON = {"ContinuousDamage5p": 0.05, "ContinuousDamage025p": 0.025}
HP_BURN_SHARE = 0.03
HEAL_OVER_TIME = {"ContinuousHeal15p": 0.15, "ContinuousHeal075p": 0.075}

# Formula names for groups of statuses: TargetHasEffectOfKind(StatusReduceDefence_KindId) and the like.
KIND_GROUPS = {
    "Stun_KindId": ("Stun",), "Freeze_KindId": ("Freeze",), "Sleep_KindId": ("Sleep",), "Provoke_KindId": ("Provoke",),
    "Fear_KindId": ("Fear1", "Fear2"), "Shield_KindId": ("Shield",), "BlockHeal_KindId": ("BlockHeal",),
    "BlockBuffs_KindId": ("BlockBuffs",), "BlockActiveSkills_KindId": ("BlockActiveSkills",), "Mark_KindId": ("Mark",),
    "Invisible_KindId": ("Invisible",), "StatusReduceDefence_KindId": ("DecreaseDefence",),
    "StatusIncreaseAttack_KindId": ("IncreaseAttack",), "StatusReduceSpeed_KindId": ("DecreaseSpeed",),
    "ContinuousDamage_KindId": ("ContinuousDamage",), "AoEContinuousDamage_KindId": ("Burn",), "Burn": ("Burn",),
    "ContinuousDamage5p": ("ContinuousDamage5p",), "ContinuousDamage025p": ("ContinuousDamage025p",),
}

# Immunities the game describes in a boss's passive text ("Immune to [Stun], [Freeze] ..."), as status prefixes.
IMMUNITY_WORDS = {
    "[Stun]": ("Stun",), "[Freeze]": ("Freeze",), "[Sleep]": ("Sleep",), "[Decrease SPD]": ("DecreaseSpeed",),
    "[Provoke]": ("Provoke",), "[Fear]": ("Fear",), "[True Fear]": ("Fear",), "[Petrification]": ("Petrification",),
    "Turn Meter reduction": ("__ReduceStamina",), "MAX HP destruction": ("__DestroyHp",),
}

SUPPORTED_KINDS = {"Damage", "ApplyBuff", "ApplyDebuff", "Heal", "Revive", "IncreaseStamina", "ReduceStamina", "ExtraTurn",
                   "RemoveDebuff", "RemoveBuff", "ReduceCooldown", "IncreaseCooldown", "ChangeDamageMultiplier",
                   "PassiveBlockDebuff", "PassiveBlockEffect", "AddIgnoredEffects", "ActionForVisualization"}
DEAD_TARGETS = {"AllDeadAllies", "RandomDeadAlly", "RandomRevivableAlly"}
AOE_TARGETS = {"AllEnemies", "AllHeroes"}
DAMAGE_TAKEN_KINDS = {"IncreaseDamageTaken", "ReduceDamageTaken"}
# Damage taken changes a status's name implies, used only when the knowledge file lacks the status's own effect type.
FALLBACK_DAMAGE_TAKEN = {"IncreaseDamageTaken": 1, "ReduceDamageTaken": -1}
ENEMY_SKILL_TARGETS = {"AliveEnemies", "AllEnemies", "AliveEnemiesIncludeInvisible"}
ALLY_SKILL_TARGETS = {"AliveAllies", "AllAllies", "AliveAlliesExceptProducer", "AllAlliesExceptProducer"}


class BattleError(Exception):
    """A problem with the request the player or Claude can fix."""


def is_passive_skill(skill: dict[str, Any]) -> bool:
    """Passives are marked [P] in their name; internal ones (Clan Boss damage caps) have no target to pick instead."""
    effects = skill.get("effects") or []
    return ("[P]" in (skill.get("name") or "") or not skill.get("targets")
            or bool(effects) and all(e.get("passive") for e in effects))


def status_family(name: str) -> str:
    """'DecreaseDefence60' -> 'DecreaseDefence'; 'ContinuousDamage5p' -> 'ContinuousDamage'."""
    return re.sub(r"\d+p?$", "", name)


def status_share(name: str) -> float:
    """'DecreaseDefence60' -> 0.6; 'ReduceDamageTaken15' -> 0.15."""
    match = re.search(r"(\d+)p?$", name)
    return int(match.group(1)) / 100 if match else 0.0


@dataclass
class Status:
    name: str
    turns: int
    producer: "Fighter | None"
    buff: bool
    value: float = 0.0
    fresh: bool = False  # placed this turn on its own producer: doesn't count down at the end of this turn


@dataclass
class Skill:
    label: str
    name: str
    skill_id: int
    cooldown: int
    start_cooldown: int
    targets: str | None
    effects: list[dict[str, Any]]
    damage_bonus: float = 0.0
    chance_bonus: float = 0.0
    description: str = ""

    def kinds(self) -> set[str]:
        return {effect["kind"] for effect in self.effects}


@dataclass
class Fighter:
    name: str
    side: str
    slot: int
    type_id: int
    affinity: str | None
    rarity: int
    base: dict[str, float]
    stats: dict[str, float]
    skills: list[Skill]
    passives: list[dict[str, Any]]
    is_boss: bool = False
    priority: list[str] = field(default_factory=list)
    never: set[str] = field(default_factory=set)
    delays: dict[str, int] = field(default_factory=dict)
    immune: set[str] = field(default_factory=set)
    lifesteal: float = 0.0
    hp: float = 0.0
    stamina: int = 0
    cooldowns: dict[str, int] = field(default_factory=dict)
    statuses: list[Status] = field(default_factory=list)
    turns_taken: int = 0
    alive: bool = True
    damage_dealt: float = 0.0
    hp_raw: int = 0

    @property
    def max_hp(self) -> float:
        return self.stats["health"]

    def has(self, family: str) -> bool:
        return any(status_family(s.name) == family or s.name == family for s in self.statuses)

    def count_of(self, buff: bool) -> int:
        return sum(1 for s in self.statuses if s.buff == buff)

    def stat(self, key: str) -> float:
        value = self.stats[key]
        for status in self.statuses:
            family = status_family(status.name)
            if family in STAT_STATUSES and STAT_STATUSES[family][0] == key:
                value += self.stats[key] * status_share(status.name) * STAT_STATUSES[family][1]
            elif family in POINT_STATUSES and POINT_STATUSES[family][0] == key:
                value += status_share(status.name) * POINT_STATUSES[family][1]
        return max(value, 0.0)

    def speed_raw(self) -> int:
        return to_raw(self.stat("speed"))


@dataclass
class Tally:
    attempted: int = 0
    landed: int = 0
    resisted: int = 0
    missed_chance: int = 0
    blocked: int = 0


class Battle:
    """One battle. Built fresh for every run from the same fighter templates."""

    def __init__(self, sim: "Simulator", fighters: list[Fighter], seed: int, record: bool):
        self.sim = sim
        self.fighters = fighters
        self.rng = random.Random(seed)
        self.record = record
        self.timeline: list[dict[str, Any]] = []
        self.ticks = 0
        self.extra_turns: list[Fighter] = []
        self.tallies: dict[str, Tally] = {}
        self.hits: dict[str, int] = {}
        self.deaths: list[dict[str, Any]] = []

    # Turn loop (speed tune rules)

    def next_actor(self) -> Fighter:
        if self.extra_turns:
            return self.extra_turns.pop(0)
        to_turn = self.sim.to_turn
        while True:
            self.ticks += 1
            if self.ticks > MAX_TICKS:
                raise BattleError("Nobody reached a turn; check the speeds.")
            top, team_at_top = -1, 0
            for fighter in self.fighters:
                if not fighter.alive:
                    continue
                fighter.stamina += mul(fighter.speed_raw(), self.sim.by_tick)
                if fighter.stamina >= top:
                    team_at_top = team_at_top if fighter.stamina == top else 0
                    top = fighter.stamina
                    team_at_top += fighter.side == "team"
            if top >= to_turn:
                side = "team" if team_at_top else "enemy"
                actor = next(f for f in self.fighters if f.alive and f.stamina == top and f.side == side)
                actor.stamina = 0
                return actor

    def run(self, boss_turn_limit: int) -> dict[str, Any]:
        boss = next(f for f in self.fighters if f.is_boss)
        while True:
            team_alive = [f for f in self.fighters if f.side == "team" and f.alive]
            if not team_alive or not boss.alive or boss.turns_taken >= boss_turn_limit:
                break
            actor = self.next_actor()
            self.take_turn(actor)
        team = [f for f in self.fighters if f.side == "team"]
        return {
            "damage": round(sum(f.damage_dealt for f in team)),
            "by_champion": {f.name: round(f.damage_dealt) for f in team},
            "boss_turns": boss.turns_taken,
            "team_alive": sum(f.alive for f in team),
            "reached_limit": boss.turns_taken >= boss_turn_limit,
            "deaths": self.deaths,
            "tallies": self.tallies,
            "hits": self.hits,
        }

    def take_turn(self, actor: Fighter) -> None:
        actor.turns_taken += 1
        if actor.turns_taken > 1:
            for label in actor.cooldowns:
                actor.cooldowns[label] = max(actor.cooldowns[label] - 1, 0)
        entry: dict[str, Any] = {"tick": self.ticks, "who": actor.name, "turn": actor.turns_taken} if self.record else {}
        notes: list[str] = []
        self.tick_statuses(actor, notes)
        if not actor.alive:
            self.log(entry, "-", notes)
            return
        if any(status_family(s.name) in SKIP_TURN or s.name in SKIP_TURN for s in actor.statuses):
            notes.append("skips the turn (crowd control)")
            self.end_turn(actor)
            self.log(entry, "-", notes)
            return
        skill = self.choose_skill(actor)
        if skill is not None:
            self.use_skill(actor, skill, notes)
            if skill.cooldown:
                actor.cooldowns[skill.label] = skill.cooldown
        self.end_turn(actor)
        self.log(entry, f"{skill.label} {skill.name}" if skill else "-", notes)

    def log(self, entry: dict[str, Any], skill: str, notes: list[str]) -> None:
        if not self.record or len(self.timeline) >= TIMELINE_LIMIT:
            return
        entry["skill"] = skill
        entry["events"] = notes
        entry["hp"] = {f.name: round(100 * f.hp / f.max_hp) for f in self.fighters}
        self.timeline.append(entry)

    def end_turn(self, actor: Fighter) -> None:
        # A status counts down at the end of its holder's turn, except one the holder placed on itself this turn.
        kept = []
        for status in actor.statuses:
            if status.fresh:
                status.fresh = False
                kept.append(status)
                continue
            if status.turns < 0:
                kept.append(status)
                continue
            status.turns -= 1
            if status.turns > 0:
                kept.append(status)
        actor.statuses = kept

    def tick_statuses(self, actor: Fighter, notes: list[str]) -> None:
        """Poison, HP Burn, and heal over time act at the start of the holder's turn."""
        for status in list(actor.statuses):
            if status.name in POISON:
                amount = actor.max_hp * POISON[status.name]
                dealt = self.receive_fixed(actor, status.producer, amount, related_status=status.name, scales_by_hp=False)
                notes.append(f"poison {round(dealt)}")
            elif status_family(status.name) == "Burn":
                # HP Burn goes through the same status reduction (its kind, AoEContinuousDamage, has its own Stone
                # Skin factor in DamageReductionByStatusEffects).
                amount = actor.max_hp * HP_BURN_SHARE * self.status_reduction(actor, False, "AoEContinuousDamage")
                dealt = self.receive_fixed(actor, status.producer, amount, related_status=status.name, scales_by_hp=True)
                notes.append(f"HP Burn {round(dealt)}")
            elif status.name in HEAL_OVER_TIME:
                healed = self.heal(actor, actor.max_hp * HEAL_OVER_TIME[status.name])
                notes.append(f"heals {round(healed)}")
            if not actor.alive:
                return

    # Skill choice (code, not AI)

    def choose_skill(self, actor: Fighter) -> Skill | None:
        ready = [s for s in actor.skills if actor.cooldowns.get(s.label, 0) == 0]
        if actor.has("Provoke") or actor.has("BlockActiveSkills"):
            return actor.skills[0] if actor.skills else None
        order = actor.priority or default_priority(actor.skills)
        for label in order:
            skill = next((s for s in ready if s.label == label), None)
            if skill is None or label in actor.never:
                continue
            if actor.turns_taken <= actor.delays.get(label, 0):
                continue
            if not self.worth_using(actor, skill):
                continue
            return skill
        return actor.skills[0] if actor.skills else None

    def worth_using(self, actor: Fighter, skill: Skill) -> bool:
        """The auto-battle sense the simulator applies: skip a skill whose only job would do nothing right now."""
        kinds = skill.kinds() - {"ActionForVisualization"}
        allies = [f for f in self.fighters if f.side == actor.side]
        if "Revive" in kinds and kinds <= {"Revive", "Heal", "ApplyBuff", "RemoveDebuff"}:
            if all(f.alive for f in allies):
                return False
        if kinds and kinds <= {"Heal", "ApplyBuff"} and any(e["kind"] == "Heal" for e in skill.effects):
            buff_names = [s["name"] for e in skill.effects for s in e.get("statuses") or []]
            if not buff_names and all(f.hp >= f.max_hp * HEAL_THRESHOLD for f in allies if f.alive):
                return False
        if kinds and kinds <= {"RemoveDebuff"} and not any(f.count_of(False) for f in allies if f.alive):
            return False
        return True

    # Effects

    def use_skill(self, actor: Fighter, skill: Skill, notes: list[str]) -> None:
        allies = [f for f in self.fighters if f.side == actor.side]
        enemies = [f for f in self.fighters if f.side != actor.side]
        chosen = self.pick_target(actor, skill, allies, enemies)
        context = SkillContext(actor=actor, skill=skill, chosen=chosen)
        for effect in skill.effects:
            if effect.get("passive"):
                continue
            self.apply_effect(effect, context, allies, enemies, notes)

    def pick_target(self, actor: Fighter, skill: Skill, allies: list[Fighter], enemies: list[Fighter]) -> Fighter | None:
        alive_enemies = [e for e in enemies if e.alive]
        alive_allies = [a for a in allies if a.alive]
        if skill.targets in ENEMY_SKILL_TARGETS:
            if not alive_enemies:
                return None
            # Champions hit the boss. The boss picks a random living enemy (assumed; the game's AI target isn't read yet).
            return alive_enemies[0] if not actor.is_boss else self.rng.choice(alive_enemies)
        if skill.targets in ALLY_SKILL_TARGETS:
            pool = [a for a in alive_allies if not (skill.targets.endswith("ExceptProducer") and a is actor)]
            if not pool:
                return None
            if "Heal" in skill.kinds():
                return min(pool, key=lambda a: (a.hp / a.max_hp, a.slot))
            return min(pool, key=lambda a: (a.stamina, a.slot))
        if skill.targets and "Dead" in skill.targets:
            dead = [a for a in allies if not a.alive]
            return dead[0] if dead else None
        return actor

    def apply_effect(self, effect: dict[str, Any], context: "SkillContext", allies: list[Fighter], enemies: list[Fighter],
                     notes: list[str]) -> None:
        kind = effect["kind"]
        actor = context.actor
        if kind not in SUPPORTED_KINDS:
            self.sim.miss(actor, f"{context.skill.name}: {kind} effect")
            return
        if kind in ("ChangeDamageMultiplier", "PassiveBlockDebuff", "PassiveBlockEffect", "AddIgnoredEffects", "ActionForVisualization"):
            return
        targets = self.effect_targets(effect, context, allies, enemies)
        if targets is None:
            self.sim.miss(actor, f"{context.skill.name}: {kind} on {effect.get('target')}")
            return
        if effect.get("condition"):
            passing = []
            for target in targets or [None]:
                try:
                    if formula.run(effect["condition"], Resolver(self, actor, target, context)):
                        passing.append(target)
                except formula.Unsupported as missing:
                    self.sim.miss(actor, f"{context.skill.name}: condition uses {missing.name}")
                    return
                except formula.FormulaError:
                    self.sim.miss(actor, f"{context.skill.name}: condition {effect['condition']}")
                    return
            targets = [t for t in passing if t is not None]
            if not targets and kind != "ExtraTurn":
                return
            if kind == "ExtraTurn" and not passing:
                return
        chance = effect.get("chance")
        chance = None if chance is None else min(chance + context.skill.chance_bonus, 1.0)
        if kind == "Damage":
            context.relation_targets = []
            if chance is not None and self.rng.random() >= chance:
                return
            for target in targets:
                self.deal_damage(effect, context, target, notes)
        elif kind in ("ApplyBuff", "ApplyDebuff"):
            for target in targets:
                for status in effect.get("statuses") or []:
                    self.place_status(status, kind == "ApplyBuff", chance, context, target, notes)
        elif kind == "Heal":
            for target in targets:
                amount = self.value(effect, context, target)
                if amount is not None:
                    self.heal(target, amount)
        elif kind == "Revive":
            for target in targets:
                if not target.alive and not target.has("BlockRevive"):
                    share = self.value(effect, context, target)
                    target.alive = True
                    target.hp = max(1.0, share if share and share > 1 else target.max_hp * (share or 0.3))
                    target.statuses = []
                    notes.append(f"revives {target.name}")
        elif kind in ("IncreaseStamina", "ReduceStamina"):
            for target in targets:
                if kind == "ReduceStamina" and "__ReduceStamina" in target.immune:
                    continue
                if chance is not None and self.rng.random() >= chance:
                    continue
                if kind == "ReduceStamina" and self.resisted(actor, target, "Turn Meter reduction"):
                    continue
                amount = stamina_change(effect.get("multiplier"), target.stamina, self.sim.to_turn)
                if amount is None:
                    self.sim.miss(actor, f"{context.skill.name}: turn meter formula {effect.get('multiplier')}")
                    continue
                target.stamina = target.stamina + amount if kind == "IncreaseStamina" else max(target.stamina - amount, 0)
        elif kind == "ExtraTurn":
            if chance is None or self.rng.random() < chance:
                if actor not in self.extra_turns:
                    self.extra_turns.insert(0, actor)
                notes.append("extra turn")
        elif kind in ("ReduceCooldown", "IncreaseCooldown"):
            turns = int(round(self.value(effect, context, actor) or 1))
            for target in targets:
                for label, left in target.cooldowns.items():
                    if label == "A1":
                        continue
                    target.cooldowns[label] = max(left - turns, 0) if kind == "ReduceCooldown" else left + turns
        elif kind in ("RemoveDebuff", "RemoveBuff"):
            for target in targets:
                removing = [s for s in target.statuses if s.buff == (kind == "RemoveBuff")]
                limit = effect.get("count") or len(removing)
                for status in removing[:limit]:
                    target.statuses.remove(status)

    def effect_targets(self, effect: dict[str, Any], context: "SkillContext", allies: list[Fighter],
                       enemies: list[Fighter]) -> list[Fighter] | None:
        actor, chosen = context.actor, context.chosen
        target = effect.get("target")
        alive_allies = [a for a in allies if a.alive]
        alive_enemies = [e for e in enemies if e.alive]
        if target in ("Producer", "RelationProducer", "Owner", "ActiveHero"):
            group = [actor]
        elif target in ("AllAllies", "OwnerAllies"):
            group = alive_allies
        elif target == "AllEnemies":
            group = alive_enemies
        elif target == "Target":
            group = [chosen] if chosen else []
        elif target in ("RelationTarget", "RelationTargetDuplicates"):
            group = list(context.relation_targets) if context.relation_targets else ([chosen] if chosen else [])
        elif target == "RandomEnemy":
            group = [self.rng.choice(alive_enemies)] if alive_enemies else []
        elif target == "RandomAlly":
            group = [self.rng.choice(alive_allies)] if alive_allies else []
        elif target == "MostInjuredAlly":
            group = [min(alive_allies, key=lambda a: (a.hp / a.max_hp, a.slot))] if alive_allies else []
        elif target in DEAD_TARGETS:
            dead = [a for a in allies if not a.alive]
            group = dead if target == "AllDeadAllies" else ([self.rng.choice(dead)] if dead else [])
        elif target == "EnemyWithHighestStamina":
            group = [max(alive_enemies, key=lambda e: (e.stamina, -e.slot))] if alive_enemies else []
        elif target == "EnemyWithLowestStamina":
            group = [min(alive_enemies, key=lambda e: (e.stamina, e.slot))] if alive_enemies else []
        else:
            return None
        if effect.get("exclude") == "Producer":
            group = [g for g in group if g is not actor]
        elif effect.get("exclude") == "Target" and chosen is not None:
            group = [g for g in group if g is not chosen]
        return group

    def value(self, effect: dict[str, Any], context: "SkillContext", target: Fighter | None) -> float | None:
        text = effect.get("multiplier")
        if not text:
            return None
        try:
            return formula.run(text, Resolver(self, context.actor, target, context))
        except formula.Unsupported as missing:
            self.sim.miss(context.actor, f"{context.skill.name}: formula uses {missing.name}")
        except formula.FormulaError:
            self.sim.miss(context.actor, f"{context.skill.name}: formula {text}")
        return None

    # Damage

    def deal_damage(self, effect: dict[str, Any], context: "SkillContext", target: Fighter, notes: list[str]) -> None:
        actor = context.actor
        hits = max(int(effect.get("count") or 1), 1)
        base = self.value(effect, context, target)
        if base is None:
            return
        context.relation_targets.append(target)
        for _ in range(hits):
            if not target.alive:
                break
            relation = rules.element_relation(actor.affinity, target.affinity)
            hit = rules.hit_type(relation, actor.stat("critical_chance"), self.rng, self.sim.constants)
            context.last_hit = hit
            if actor.side == "team":
                self.hits[hit] = self.hits.get(hit, 0) + 1
            amount = base * self.status_multiplier(actor, target, effect, context) * rules.damage_multipliers(
                relation, hit, actor.stat("critical_damage"), context.skill.damage_bonus, 0.0,
                rules.stat_damage_modifier({}, {}, self.sim.area, actor.is_boss, target.is_boss), self.sim.constants)
            amount *= rules.defence_factor(target.stat("defence"), 0.0, -actor.stats.get("ignore_defence", 0.0))
            amount *= self.status_reduction(target, effect.get("target") in AOE_TARGETS, "Damage")
            amount *= rules.damage_roll(self.rng)
            scales = "TRG_HP" in (effect.get("multiplier") or "") or "TRG_B_HP" in (effect.get("multiplier") or "")
            amount = self.passive_damage(actor, target, amount, effect, scales_by_hp=scales)
            dealt = self.receive(target, actor, amount)
            if actor.lifesteal and dealt > 0:
                self.heal(actor, dealt * actor.lifesteal)
            if hit == rules.CRITICAL:
                notes.append(f"crit {target.name} {round(dealt)}")
            else:
                notes.append(f"hits {target.name} {round(dealt)}")

    def status_multiplier(self, actor: Fighter, target: Fighter, effect: dict[str, Any], context: "SkillContext") -> float:
        """StatusChangeDamageMultiplierProcessor (0x182DDA400): every status on the target or the attacker whose own
        effect is a damage multiplier (Weaken, Strengthen, ...) multiplies the hit's base value by its formula when its
        condition holds, so several such statuses multiply. Block Damage skips them; here it leaves nothing to multiply."""
        factor = 1.0
        for holder, other in ((target, actor), (actor, target)):
            for status in holder.statuses:
                factor *= self.one_status_multiplier(status, holder, other, actor, effect, context)
        return factor

    def one_status_multiplier(self, status: Status, holder: Fighter, other: Fighter, actor: Fighter, effect: dict[str, Any],
                              context: "SkillContext") -> float:
        definition = self.sim.status_types.get(status.name)
        if definition is None:
            return self.sim.fallback_multiplier(status, holder is not actor)
        own = definition.get("effect") or {}
        if own.get("kind") not in rules.STATUS_MULTIPLIER_KINDS or not own.get("multiplier"):
            return 1.0
        kinds = (own.get("relation") or {}).get("kinds") or []
        if kinds and effect["kind"] not in kinds:
            return 1.0
        resolver = Resolver(self, holder, other, context, related_effect=effect, related_producer=actor,
                            owner_produces=holder is actor)
        condition = own.get("condition")
        try:
            if not condition:
                # Without a condition, a damage-taken status changes only the damage its holder takes.
                if own["kind"] not in DAMAGE_TAKEN_KINDS:
                    self.sim.miss(holder, f"{status.name}: {own['kind']} without a condition")
                    return 1.0
                if holder is actor:
                    return 1.0
            elif not formula.run(condition, resolver):
                return 1.0
            return formula.run(own["multiplier"], resolver)
        except (formula.Unsupported, formula.FormulaError) as error:
            self.sim.miss(holder, f"{status.name}: formula uses {getattr(error, 'name', None) or 'unreadable text'}")
            return self.sim.fallback_multiplier(status, holder is not actor)

    def status_reduction(self, target: Fighter, aoe: bool, kind: str) -> float:
        factor, missing = rules.status_reduction({s.name for s in target.statuses}, aoe, kind, self.sim.status_factors)
        for name in missing:
            self.sim.miss(target, f"{name}: damage factor isn't in the knowledge file")
        return factor

    def passive_damage(self, actor: Fighter, target: Fighter, amount: float, effect: dict[str, Any] | None,
                       scales_by_hp: bool, related_status: str | None = None) -> float:
        """Passive ChangeDamageMultiplier effects: the dealer's on damage it deals (conditions that name
        isOwnerProduceRelatedEffect), then the target's on damage it takes (the rest)."""
        for owner, outgoing in ((actor, True), (target, False)):
            if owner is None:
                continue
            for passive in owner.passives:
                if passive["kind"] != "ChangeDamageMultiplier" or not passive.get("multiplier"):
                    continue
                condition = passive.get("condition") or "1"
                if ("isOwnerProduceRelatedEffect" in condition) != outgoing:
                    continue
                resolver = Resolver(self, owner, target if outgoing else actor, None, damage=amount, related_effect=effect,
                                    related_status=related_status, scales_by_hp=scales_by_hp,
                                    related_producer=actor, owner_produces=outgoing)
                try:
                    if formula.run(condition, resolver):
                        amount += formula.run(passive["multiplier"], resolver)
                except formula.Unsupported as missing:
                    self.sim.miss(owner, f"passive {passive.get('skill_name')}: uses {missing.name}")
                except formula.FormulaError:
                    self.sim.miss(owner, f"passive {passive.get('skill_name')}: formula")
        return max(amount, 0.0)

    def receive_fixed(self, target: Fighter, producer: Fighter | None, amount: float, related_status: str,
                      scales_by_hp: bool) -> float:
        """Damage over time: no DEF, no hit type, no variance; the target's damage caps still apply."""
        amount = self.passive_damage(producer, target, amount, None, scales_by_hp=scales_by_hp, related_status=related_status) \
            if producer is not None else amount
        return self.receive(target, producer, amount)

    def receive(self, target: Fighter, dealer: Fighter | None, amount: float) -> float:
        if target.has("BlockDamage"):
            return 0.0
        shield = next((s for s in target.statuses if s.name == "Shield"), None)
        if shield and shield.value > 0:
            absorbed = min(shield.value, amount)
            shield.value -= absorbed
            amount -= absorbed
            if shield.value <= 0:
                target.statuses.remove(shield)
        protector = next((s for s in target.statuses if status_family(s.name) == "ShareDamage" and s.producer and s.producer.alive
                          and s.producer is not target), None)
        if protector is not None:
            shared = amount * status_share(protector.name)
            amount -= shared
            self.reduce_hp(protector.producer, dealer, shared)
        return self.reduce_hp(target, dealer, amount)

    def reduce_hp(self, target: Fighter, dealer: Fighter | None, amount: float) -> float:
        if amount <= 0 or not target.alive:
            return 0.0
        floor = 1.0 if target.has("Unkillable") else 0.0
        if target.is_boss:
            floor = 0.0
        before = target.hp
        target.hp = max(target.hp - amount, floor)
        # Damage counts in full against the Clan Boss: the score is damage dealt, not HP lost.
        dealt = amount if target.is_boss else before - target.hp
        if dealer is not None and dealer.side != target.side:
            dealer.damage_dealt += dealt
        if target.hp <= 0 and not target.is_boss:
            self.die(target, dealer)
        if target.is_boss:
            target.hp = target.max_hp
        return dealt

    def die(self, target: Fighter, dealer: Fighter | None) -> None:
        revive = next((s for s in target.statuses if status_family(s.name) == "ReviveOnDeath"), None)
        if revive is not None:
            target.statuses = []
            target.hp = target.max_hp * 0.3
            return
        target.alive = False
        target.hp = 0.0
        target.statuses = []
        target.stamina = 0
        self.deaths.append({"champion": target.name, "boss_turn": self.boss_turns(), "by": dealer.name if dealer else None})

    def boss_turns(self) -> int:
        return next((f.turns_taken for f in self.fighters if f.is_boss), 0)

    def heal(self, target: Fighter, amount: float) -> float:
        if not target.alive or amount <= 0 or target.has("BlockHeal"):
            return 0.0
        before = target.hp
        target.hp = min(target.hp + amount, target.max_hp)
        return target.hp - before

    # Buffs and debuffs

    def place_status(self, status: dict[str, Any], buff: bool, chance: float | None, context: "SkillContext",
                     target: Fighter, notes: list[str]) -> None:
        name = status["name"]
        actor = context.actor
        # Debuff odds are reported for the player's team only; the boss's own debuffs aren't counted.
        tally = self.tallies.setdefault(name, Tally()) if not buff and actor.side == "team" else None
        if tally:
            tally.attempted += 1
        if chance is not None and self.rng.random() >= chance:
            if tally:
                tally.missed_chance += 1
            return
        if not buff:
            if any(name.startswith(prefix) for prefix in target.immune) or target.has("BlockDebuff"):
                if tally:
                    tally.blocked += 1
                return
            unresistable = (bool(status.get("unresistable_condition")) or target.side == actor.side
                            or "cannot be resisted" in (context.skill.description or ""))
            if not unresistable and rules.is_resisted(actor.stat("accuracy"), target.stat("resistance"), self.rng):
                if tally:
                    tally.resisted += 1
                notes.append(f"{target.name} resists {name}")
                return
            if target.count_of(False) >= self.sim.constants["max_applied_debuff_effects"]:
                return
            if tally:
                tally.landed += 1
        else:
            if target.has("BlockBuffs"):
                return
            if target.count_of(True) >= self.sim.constants["max_applied_buff_effects"]:
                return
        turns = int(status.get("turns") or 0)
        value = 0.0
        if name == "Shield":
            value = self.shield_value(context, target)
        existing = next((s for s in target.statuses if s.name == name and not (name in POISON or name == "Burn")), None)
        if existing is not None and name not in POISON:
            existing.turns = max(existing.turns, turns)
            existing.value = max(existing.value, value)
            existing.producer = actor
        else:
            target.statuses.append(Status(name, turns, actor, buff, value, fresh=(target is actor)))
        notes.append(f"{name} on {target.name}")

    def shield_value(self, context: "SkillContext", target: Fighter) -> float:
        for effect in context.skill.effects:
            if effect["kind"] == "ApplyBuff" and any(s["name"] == "Shield" for s in effect.get("statuses") or []) and effect.get("multiplier"):
                return self.value(effect, context, target) or 0.0
        return 0.0

    def resisted(self, actor: Fighter, target: Fighter, what: str) -> bool:
        tally = self.tallies.setdefault(what, Tally()) if actor.side == "team" else Tally()
        tally.attempted += 1
        if rules.is_resisted(actor.stat("accuracy"), target.stat("resistance"), self.rng):
            tally.resisted += 1
            return True
        tally.landed += 1
        return False


def stamina_change(text: str | None, current: int, full: int) -> int | None:
    if not text:
        return None
    compact = text.replace(" ", "")
    for variable, basis in (("MAX_STAMINA", full), ("TRG_STAMINA", current)):
        match = re.fullmatch(rf"(?:([\d.]+)\*)?{variable}(?:\*([\d.]+))?", compact)
        if match:
            return mul(basis, to_raw(float(match.group(1) or match.group(2) or 1)))
    return None


def default_priority(skills: list[Skill]) -> list[str]:
    """Newest cooldown skill first (A4, A3, A2), revives after the other cooldown skills, then A1."""
    revives = {s.label for s in skills if "Revive" in s.kinds()}
    return [s.label for s in sorted(skills, key=lambda s: (s.cooldown == 0, s.label in revives, -int(s.label[1:])))]


@dataclass
class SkillContext:
    actor: Fighter
    skill: Skill
    chosen: Fighter | None
    relation_targets: list[Fighter] = field(default_factory=list)
    last_hit: str | None = None


class Resolver:
    """Gives the game's formula names their values for one effect: the dealer (owner), the target, the skill."""

    def __init__(self, battle: Battle, owner: Fighter, target: Fighter | None, context: SkillContext | None,
                 damage: float | None = None, related_effect: dict[str, Any] | None = None, related_status: str | None = None,
                 scales_by_hp: bool = False, related_producer: Fighter | None = None, owner_produces: bool = True):
        self.battle = battle
        self.owner = owner
        self.target = target
        self.context = context
        self.damage = damage
        self.related_effect = related_effect
        self.related_status = related_status
        self.scales_by_hp = scales_by_hp
        self.related_producer = related_producer
        self.owner_produces = owner_produces

    def value(self, name: str) -> float:
        owner, target = self.owner, self.target
        if name in FORMULA_STATS:
            return owner.stat(FORMULA_STATS[name]) if name != "HP" else owner.max_hp
        if name.startswith("TRG_") and target is not None:
            key = name[4:]
            if key in FORMULA_STATS:
                return target.stat(FORMULA_STATS[key]) if key != "HP" else target.max_hp
            if key == "B_HP":
                return target.base.get("health", target.max_hp)
            if key == "HP_PERC":
                return 100 * target.hp / target.max_hp
            if key == "STAMINA":
                return target.stamina / ONE
            if key == "BUFF_COUNT":
                return target.count_of(True)
            if key == "DEBUFF_COUNT":
                return target.count_of(False)
        simple = {
            "CUR_HP": lambda: owner.hp,
            "HP_PERC": lambda: 100 * owner.hp / owner.max_hp,
            "MAX_STAMINA": lambda: self.battle.sim.to_turn / ONE,
            "OWNERS_TURN_NUMBER": lambda: owner.turns_taken,
            "DMG_MUL": lambda: self.damage if self.damage is not None else 0.0,
            "isCritical": lambda: float(self.context is not None and self.context.last_hit == rules.CRITICAL),
            "isOwnersTurn": lambda: 1.0,
            "relatedEffectWasDetonated": lambda: 0.0,
            "relatedEffectWasRedirected": lambda: 0.0,
            "reletionEffectScalesByTargetHp": lambda: float(self.scales_by_hp),
            "isOwnerProduceRelatedEffect": lambda: float(self.owner_produces),
            "ownerIsRelatedEffectTarget": lambda: float(not self.owner_produces),
            "REL_PRODUCER_RARITY": lambda: float(self.related_producer.rarity if self.related_producer else 0),
            "relatedEffectId": lambda: float((self.related_effect or {}).get("id") or 0),
            "relatedStatusEffectTypeId": lambda: float(status_code(self.related_status)) if self.related_status else 0.0,
            "aliveEnemiesCount": lambda: sum(1 for f in self.battle.fighters if f.side != owner.side and f.alive),
            "deadAlliesCount": lambda: sum(1 for f in self.battle.fighters if f.side == owner.side and not f.alive),
            "targetIsBoss": lambda: float(target is not None and target.is_boss),
            "heroKilledByProducer": lambda: float(target is not None and not target.alive),
        }
        if name in simple:
            return simple[name]()
        if name in KIND_GROUPS or name in POISON:
            return float(status_code(name))
        raise formula.Unsupported(name)

    def call(self, name: str, args: list[Any]) -> float:
        def has(fighter: Fighter | None, kind: Any) -> float:
            families = KIND_GROUPS.get(str(kind))
            if fighter is None or families is None:
                raise formula.Unsupported(f"{name}({kind})")
            return float(any(status_family(s.name) in families or s.name in families for s in fighter.statuses))

        if name in ("TargetHasEffectOfKind", "RelationTargetHasEffectOfKind"):
            return has(self.target, args[0])
        if name in ("ProducerHasEffectOfKind", "OwnerHasEffectOfKind"):
            return has(self.owner, args[0])
        if name == "TotalTurnLeftForEffectOfKindOnOwner":
            families = KIND_GROUPS.get(str(args[0]))
            if families is None:
                raise formula.Unsupported(f"{name}({args[0]})")
            return float(sum(max(s.turns, 0) for s in self.owner.statuses if status_family(s.name) in families or s.name in families))
        if name == "ABS":
            return abs(float(args[0]))
        raise formula.Unsupported(name)


def status_code(name: str) -> int:
    """A stable number standing in for a status kind in formula comparisons (relatedStatusEffectTypeId==X)."""
    return sum((index + 1) * ord(char) for index, char in enumerate(name))


class Simulator:
    """Builds fighters from the knowledge file and the account, runs battles, and sums them up."""

    def __init__(self, knowledge: Any, roster: dict[str, Any]):
        self.knowledge = knowledge
        self.roster = roster
        battle = knowledge.raw.get("battle") or {}
        self.by_tick = to_raw(battle.get("stamina_by_tick") or 0.07)
        self.to_turn = to_raw(battle.get("stamina_to_turn") or 100)
        self.constants, self.assumed_constants = rules.gameplay_constants(battle)
        self.status_types: dict[str, dict[str, Any]] = knowledge.raw.get("status_effects") or {}
        self.status_factors = {name: (d.get("damage_factors") or {}) for name, d in self.status_types.items()}
        self.status_types_error: str | None = knowledge.raw.get("status_effects_error")
        self.fallback_statuses: set[str] = set()
        self.area = CLAN_BOSS_AREA
        self.missing: dict[str, set[str]] = {}
        self.notes: list[str] = []

    def fallback_multiplier(self, status: Status, on_target: bool) -> float:
        """Weaken or Strengthen without its effect type in the knowledge file: the share in its name, on damage taken."""
        sign = FALLBACK_DAMAGE_TAKEN.get(status_family(status.name))
        if sign is None or not on_target:
            return 1.0
        self.fallback_statuses.add(status.name)
        return 1 + sign * status_share(status.name)

    def miss(self, fighter: Fighter, what: str) -> None:
        self.missing.setdefault(fighter.name, set()).add(what)

    # Clan Boss

    def clan_boss_stage(self, difficulty: str, affinity: str) -> dict[str, Any]:
        region = next((r for r in self.knowledge.raw.get("regions") or [] if r.get("id") == CLAN_BOSS_REGION), None)
        if region is None:
            raise BattleError("The knowledge file has no Clan Boss. Run refresh_knowledge with RAID open.")
        tier = next((t for t in CLAN_BOSS_TIERS if t.lower() == difficulty.replace(" ", "").replace("-", "").lower()), None)
        if tier is None:
            raise BattleError(f"Unknown Clan Boss difficulty '{difficulty}'. Use one of: {', '.join(CLAN_BOSS_TIERS)}.")
        stages = [s for stages in region["difficulties"].values() for s in stages]
        group = [s for s in stages if (s["number"] - 1) // 4 == CLAN_BOSS_TIERS.index(tier)]
        for stage in group:
            enemy = stage["enemies"][0]
            if (self.knowledge.types.get(enemy["type_id"]) or {}).get("affinity", "").lower() == affinity.lower():
                return {"stage": stage, "tier": tier}
        raise BattleError(f"No {affinity} Clan Boss at {tier}. Affinities: Void, Magic, Force, Spirit.")

    def boss_fighter(self, stage: dict[str, Any], stats_override: dict[str, float] | None) -> Fighter:
        enemy = stage["enemies"][0]
        boss_type = self.knowledge.types.get(enemy["type_id"]) or {}
        stats = dict(stats_override or enemy.get("stats") or {})
        if not stats:
            raise BattleError("This knowledge file has no boss stats yet (it predates the battle simulator). Run "
                              "refresh_knowledge with RAID open; the Agent must be able to read the game (run it the same "
                              "way as refresh_account).")
        for key in STAT_KEYS:
            stats.setdefault(key, 0.0)
        skills = self.skills_for(boss_type, {}, enemy.get("name") or "Boss")
        fighter = Fighter(boss_type.get("name") or "Boss", "enemy", 1, enemy["type_id"], boss_type.get("affinity"),
                          rarity_number(boss_type.get("rarity")), dict(stats), dict(stats), skills,
                          self.passives_for(boss_type), is_boss=True, priority=list(CLAN_BOSS_PRIORITY))
        fighter.immune = self.immunities(boss_type)
        fighter.base["health"] = (boss_type.get("base_stats") or {}).get("health", stats["health"]) * 15
        return fighter

    def immunities(self, champion_type: dict[str, Any]) -> set[str]:
        immune: set[str] = set()
        for skill_id in champion_type.get("skill_ids") or []:
            description = (self.knowledge.skills.get(str(skill_id)) or {}).get("description") or ""
            if "Immune to" not in description:
                continue
            for word, families in IMMUNITY_WORDS.items():
                if word in description:
                    immune.update(families)
        return immune

    # Player's team

    def team_fighters(self, team: list[dict[str, Any]]) -> list[Fighter]:
        if not team:
            raise BattleError("Give the team's champions, leader first.")
        if len(team) > 5:
            raise BattleError("A Clan Boss team has at most 5 champions.")
        members = []
        for slot, entry in enumerate(team, start=1):
            entry = entry if isinstance(entry, dict) else {"champion": str(entry)}
            members.append((slot, entry, self.roster_champion(str(entry.get("champion") or ""))))
        leader_type = self.knowledge.types.get(members[0][2]["type_id"]) or {}
        auras = [a for a in leader_type.get("leader_skills") or [] if aura_applies(a, self.area)]
        fighters = []
        for slot, entry, champion in members:
            champion_type = self.knowledge.types.get(champion["type_id"]) or {}
            calculated = champion.get("calculated_stats") or {}
            total = dict(calculated.get("precise") or calculated.get("total") or {})
            base = dict(calculated.get("base") or {})
            if "speed" not in total or "health" not in total:
                raise BattleError(f"{champion['name']} has no calculated stats. Refresh the account first.")
            for aura in auras:
                key = aura_stat(aura.get("stat"))
                if key is None or not aura_reaches(aura, champion_type):
                    continue
                total[key] = total.get(key, 0) + (aura["amount"] if aura.get("absolute") else base.get(key, 0) * aura["amount"])
            for key in STAT_KEYS:
                total.setdefault(key, 0.0)
            skills = self.skills_for(champion_type, champion.get("skill_levels") or {}, champion["name"])
            fighter = Fighter(champion["name"], "team", slot, champion["type_id"], champion_type.get("affinity"),
                              rarity_number(champion_type.get("rarity")), base, total, skills, self.passives_for(champion_type))
            fighter.priority = self.labels(fighter, entry.get("priority"))
            fighter.never = set(self.labels(fighter, entry.get("never"), add_a1=False))
            fighter.delays = {label: int(turns) for label, turns in (entry.get("delay") or {}).items()}
            fighter.lifesteal = 0.3 if any(s.get("active") and s.get("name") == "Lifesteal" for s in calculated.get("sets") or []) else 0.0
            self.flag_unmodelled(fighter, champion, champion_type)
            fighters.append(fighter)
        return fighters

    def roster_champion(self, reference: str) -> dict[str, Any]:
        key = reference.strip().lower()
        if not key:
            raise BattleError("Each team member needs a champion name or id.")
        champions = self.roster.get("champions") or []
        match = next((c for c in champions if str(c.get("id")) == key), None)
        if match:
            return match
        named = [c for c in champions if (c.get("name") or "").lower() == key] or [
            c for c in champions if key in (c.get("name") or "").lower()]
        if not named:
            raise BattleError(f"No owned champion called '{reference}'.")
        if len({c["name"] for c in named}) > 1:
            raise BattleError(f"'{reference}' matches {', '.join(sorted({c['name'] for c in named})[:8])}. Use the full name.")
        return sorted(named, key=lambda c: (-c.get("stars", 0), -c.get("level", 0), -c.get("awakening_level", 0)))[0]

    def labels(self, fighter: Fighter, requested: list[str] | None, add_a1: bool = True) -> list[str]:
        if not requested:
            return []
        known = {s.label for s in fighter.skills}
        by_name = {s.name.lower(): s.label for s in fighter.skills}
        order = []
        for item in requested:
            text = str(item).strip()
            label = text.upper() if text.upper() in known else by_name.get(text.lower())
            if not label:
                raise BattleError(f"{fighter.name} has no skill '{item}'. Skills: {', '.join(f'{s.label} {s.name}' for s in fighter.skills)}.")
            order.append(label)
        if add_a1 and "A1" not in order:
            order.append("A1")
        return order

    def skills_for(self, champion_type: dict[str, Any], levels: dict[str, Any], owner: str) -> list[Skill]:
        skills, index = [], 0
        for skill_id in champion_type.get("skill_ids") or []:
            skill = self.knowledge.skills.get(str(skill_id))
            if not skill or not skill.get("name") or is_passive_skill(skill):
                continue
            index += 1
            level = int(levels.get(str(skill_id), len(skill.get("level_bonuses") or []) + 1 if not levels else 1))
            bonuses = (skill.get("level_bonuses") or [])[: max(level - 1, 0)]
            booked = sum(int(round(b["value"])) for b in bonuses if b["type"] == "Cooldown")
            skills.append(Skill(
                f"A{index}", skill["name"], int(skill_id), max((skill.get("cooldown") or 0) - booked, 0),
                int(skill.get("start_cooldown") or 0), skill.get("targets"), skill.get("effects") or [],
                damage_bonus=sum(b["value"] for b in bonuses if b["type"] == "Damage"),
                chance_bonus=sum(b["value"] for b in bonuses if b["type"] == "EffectChance"),
                description=skill.get("description") or ""))
        return skills

    def passives_for(self, champion_type: dict[str, Any]) -> list[dict[str, Any]]:
        passives = []
        for skill_id in champion_type.get("skill_ids") or []:
            skill = self.knowledge.skills.get(str(skill_id)) or {}
            passive_skill = bool(skill) and is_passive_skill(skill)
            for effect in skill.get("effects") or []:
                if passive_skill or effect.get("passive"):
                    passives.append({**effect, "skill_name": skill.get("name")})
        return passives

    def flag_unmodelled(self, fighter: Fighter, champion: dict[str, Any], champion_type: dict[str, Any]) -> None:
        for passive in fighter.passives:
            if passive["kind"] not in ("ChangeDamageMultiplier", "PassiveBlockDebuff", "PassiveBlockEffect"):
                self.miss(fighter, f"passive {passive.get('skill_name')}: {passive['kind']}")
        for item in (champion.get("calculated_stats") or {}).get("sets") or []:
            if item.get("active") and item.get("skill_effect") and item.get("name") != "Lifesteal":
                self.miss(fighter, f"{item.get('name')} set effect")
        if champion.get("masteries"):
            self.miss(fighter, "masteries with battle effects (Warmaster, Giant Slayer, Helmsmasher, ...)")

    # Runs

    def fresh_copy(self, templates: list[Fighter]) -> list[Fighter]:
        fighters = []
        for template in templates:
            fighter = Fighter(**{**template.__dict__, "stats": dict(template.stats), "base": dict(template.base),
                                 "cooldowns": {s.label: s.start_cooldown for s in template.skills}, "statuses": [],
                                 "hp": template.stats["health"], "stamina": 0, "turns_taken": 0, "alive": True,
                                 "damage_dealt": 0.0})
            fighters.append(fighter)
        return fighters

    def coverage(self, fighter: Fighter) -> float:
        effects = [e for s in fighter.skills for e in s.effects] + fighter.passives
        if not effects:
            return 1.0
        missing = self.missing.get(fighter.name, set())
        unsupported = sum(1 for e in effects if e["kind"] not in SUPPORTED_KINDS)
        flagged = len(missing)
        return round(max(0.0, 1 - (unsupported + 0.5 * flagged) / (len(effects) + 0.5 * flagged)), 2)


def aura_stat(stat: str | None) -> str | None:
    return {"Health": "health", "Attack": "attack", "Defence": "defence", "Speed": "speed", "Resistance": "resistance",
            "Accuracy": "accuracy", "CriticalChance": "critical_chance", "CriticalDamage": "critical_damage"}.get(stat or "")


def aura_reaches(aura: dict[str, Any], champion_type: dict[str, Any]) -> bool:
    if aura.get("affinity") and champion_type.get("affinity") != aura["affinity"]:
        return False
    if aura.get("faction") and champion_type.get("faction") != aura["faction"]:
        return False
    return True


def rarity_number(rarity: str | None) -> int:
    return {"Common": 1, "Uncommon": 2, "Rare": 3, "Epic": 4, "Legendary": 5, "Mythical": 6}.get(rarity or "", 0)


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(int(round(share * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


def simulate_clan_boss(knowledge: Any, roster: dict[str, Any], team: list[dict[str, Any]], difficulty: str = "Nightmare",
                       affinity: str = "Void", runs: int = DEFAULT_RUNS, seed: int = 1,
                       boss_turn_limit: int = DEFAULT_BOSS_TURN_LIMIT, boss_stats: dict[str, float] | None = None,
                       label: str | None = None) -> dict[str, Any]:
    """Plays one team against the Clan Boss `runs` times (seeds seed..seed+runs-1) and sums up the results."""
    sim = Simulator(knowledge, roster)
    found = sim.clan_boss_stage(difficulty, affinity)
    boss = sim.boss_fighter(found["stage"], boss_stats)
    team_templates = sim.team_fighters(team)
    runs = max(1, min(int(runs or DEFAULT_RUNS), MAX_RUNS))
    results, timeline = [], []
    for run in range(runs):
        fighters = sim.fresh_copy(team_templates + [boss])
        battle = Battle(sim, fighters, seed + run, record=run == 0)
        results.append(battle.run(boss_turn_limit))
        if run == 0:
            timeline = battle.timeline
    damages = [r["damage"] for r in results]
    tallies: dict[str, Tally] = {}
    for result in results:
        for name, tally in result["tallies"].items():
            total = tallies.setdefault(name, Tally())
            for key in ("attempted", "landed", "resisted", "missed_chance", "blocked"):
                setattr(total, key, getattr(total, key) + getattr(tally, key))
    hits: dict[str, int] = {}
    for result in results:
        for name, count in result["hits"].items():
            hits[name] = hits.get(name, 0) + count
    per_champion = {f.name: round(statistics.mean(r["by_champion"][f.name] for r in results)) for f in team_templates}
    return {
        "team": label or " / ".join(f.name for f in team_templates),
        "encounter": f"Clan Boss {found['tier']} {affinity.title()}",
        "boss": {"name": boss.name, "affinity": boss.affinity, **{k: round(v, 2) for k, v in boss.stats.items() if v}},
        "runs": runs,
        "seed": seed,
        "damage": {
            "mean": round(statistics.mean(damages)),
            "min": min(damages),
            "p10": round(percentile(damages, 0.1)),
            "median": round(statistics.median(damages)),
            "p90": round(percentile(damages, 0.9)),
            "max": max(damages),
        },
        "per_run": damages,
        "boss_turns": {"mean": round(statistics.mean(r["boss_turns"] for r in results), 1),
                       "min": min(r["boss_turns"] for r in results), "max": max(r["boss_turns"] for r in results)},
        "reached_turn_limit": sum(r["reached_limit"] for r in results),
        "damage_by_champion": per_champion,
        "first_death": summarize_deaths(results),
        "debuffs": {name: {"attempted": t.attempted, "landed": t.landed, "resisted": t.resisted,
                           "chance_failed": t.missed_chance, "boss_immune": t.blocked,
                           "resist_rate": round(t.resisted / max(t.attempted - t.missed_chance - t.blocked, 1), 3)}
                    for name, t in sorted(tallies.items())},
        "hit_types": hits,
        "champions": [{
            "champion": f.name,
            "slot": f.slot,
            "affinity": f.affinity,
            "stats": {k: round(f.stats[k], 2) for k in ("health", "attack", "defence", "speed", "accuracy", "resistance",
                                                         "critical_chance", "critical_damage")},
            "skills": [{"skill": f"{s.label} {s.name}", "cooldown": s.cooldown or None} for s in f.skills],
            "priority": f.priority or default_priority(f.skills),
            "coverage": sim.coverage(f),
        } for f in team_templates],
        "timeline": timeline,
        "not_simulated": {name: sorted(items) for name, items in sorted(sim.missing.items())},
        "assumed": assumptions(sim),
    }


def summarize_deaths(results: list[dict[str, Any]]) -> dict[str, Any]:
    firsts = [r["deaths"][0] for r in results if r["deaths"]]
    if not firsts:
        return {"runs_with_a_death": 0}
    return {"runs_with_a_death": len(firsts), "earliest_boss_turn": min(d["boss_turn"] for d in firsts),
            "most_often": statistics.mode(d["champion"] for d in firsts)}


def assumptions(sim: Simulator) -> list[str]:
    notes = [
        "Turn order, damage, DEF, hit types, and resist chances follow the game's code (build 2026-10-19).",
        "The Demon Lord uses A2, A3, A1 in turn and picks a random target; the fight ends after its "
        f"{DEFAULT_BOSS_TURN_LIMIT}th turn or when the team is dead. Assumed until checked against logged fights.",
        "Stat buffs and debuffs change the stat by their share of the champion's battle stat (measured for SPD).",
        "Weaken, Strengthen, and other damage-multiplying statuses use each status's own formula from the game and "
        "multiply the hit's base value (game code). Freeze, Invisible against AoE, Stone Skin, and Petrification reduce "
        "damage as the game's DamageReductionByStatusEffects does.",
        "Poison is 5% or 2.5% of max HP and HP Burn 3% at the start of the holder's turn, before the boss's damage caps.",
        "Masteries, blessings, and set effects other than Lifesteal aren't simulated, so damage is a floor for teams that "
        "rely on Warmaster or Giant Slayer.",
    ]
    if sim.fallback_statuses:
        notes.append("This knowledge file predates the export of status effect types, so "
                     f"{', '.join(sorted(sim.fallback_statuses))} used the share in the status's name. "
                     "Run refresh_knowledge to use the game's formulas.")
    if sim.status_types_error:
        notes.append(f"The Agent couldn't read status effect types ({sim.status_types_error}).")
    if sim.assumed_constants:
        notes.append("This knowledge file predates the export of the game's hit and affinity constants; defaults are used "
                     f"for {', '.join(sim.assumed_constants)}. Run refresh_knowledge to use the game's values.")
    return notes
