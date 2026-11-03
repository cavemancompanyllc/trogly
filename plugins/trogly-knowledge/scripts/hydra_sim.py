"""Alliance Hydra battles on the whole-battle simulator: four heads at once, a turn limit, and the player's own focus
order. Design and status: Feature #759 (story #761); the engine is `battle_sim`.

The score is the damage the team deals to the heads. A head that reaches 0 HP is decapitated after the hit's own
passives run (Tide of Hope heals the team, Shed Head starts the neck). This first version keeps a decapitated head down
for the rest of the battle: the neck and regrowth (the game's HydraNeck variant and GrowHydraHead effect), Devour,
Hunger counters and Wrath's hit counter wait on the knowledge file's hero variants and on rules still being read from
game code, so they are listed under `not_simulated`, and strong teams' damage reads low until they land.
"""

from __future__ import annotations

import statistics
from typing import Any

import battle_sim as sim_core
from battle_sim import Battle, BattleError, Fighter, Simulator, Tally, percentile, rarity_number

HYDRA_AREA = "Hydra"
# AllianceHydraDifficultyLevel, in stage order within each season.
HYDRA_DIFFICULTIES = ["Normal", "Hard", "Brutal", "Nightmare"]
HYDRA_TEAM_SIZE = 6
# Used only when the knowledge file has no Hydra turn limit (BattleSettings.SpecificMaxTurnsInBattleByArea).
ASSUMED_TURN_LIMIT = 300
# Head skills the simulator leaves out, and why. Feast swallows a champion (Devour), which isn't modelled; a head that
# chose it would waste its turn. Death Throes targets "AllAllies except Producer", and which side that means when a
# champion's hit sets it off isn't read from the game's code yet.
SKIPPED_HEAD_SKILLS = {
    "Feast": "Devour (swallows a champion) isn't modelled",
    "Death Throes [P]": "which side it hits when a champion decapitates the head isn't read yet",
}


class HydraSimulator(Simulator):
    """Builds the heads of one Hydra season and difficulty."""

    def __init__(self, knowledge: Any, roster: dict[str, Any]):
        super().__init__(knowledge, roster)
        self.area = HYDRA_AREA

    def hydra_stage(self, difficulty: str, season: int | None) -> dict[str, Any]:
        regions = sorted((r for r in self.knowledge.raw.get("regions") or [] if r.get("area") == HYDRA_AREA),
                         key=lambda r: r["id"])
        if not regions:
            raise BattleError("The knowledge file has no Hydra. Run refresh_knowledge with RAID open.")
        if season is None:
            region = regions[-1]
        else:
            region = next((r for r in regions if r["code"].endswith(f"Season{int(season)}")), None)
            if region is None:
                raise BattleError(f"No Hydra season {season}. Seasons: 1 to {len(regions)}.")
        tier = next((t for t in HYDRA_DIFFICULTIES if t.lower() == difficulty.strip().lower()), None)
        if tier is None:
            raise BattleError(f"Unknown Hydra difficulty '{difficulty}'. Use one of: {', '.join(HYDRA_DIFFICULTIES)}.")
        stages = sorted((s for group in region["difficulties"].values() for s in group), key=lambda s: s["number"])
        index = HYDRA_DIFFICULTIES.index(tier)
        if index >= len(stages):
            raise BattleError(f"Hydra season {region['code']} has no {tier} stage.")
        return {"stage": stages[index], "tier": tier, "season": region["code"].replace("AllianceHydra", "")}

    def head_fighters(self, stage: dict[str, Any]) -> list[Fighter]:
        heads = []
        for slot, enemy in enumerate(stage["enemies"], start=1):
            head_type = self.knowledge.types.get(enemy["type_id"]) or {}
            stats = dict(enemy.get("stats") or {})
            if not stats:
                raise BattleError("This knowledge file has no Hydra head stats. Run refresh_knowledge with RAID open.")
            for key in sim_core.STAT_KEYS:
                stats.setdefault(key, 0.0)
            name = enemy.get("name") or head_type.get("name") or f"Head {slot}"
            skills = [s for s in self.skills_for(head_type, {}, name) if s.name not in SKIPPED_HEAD_SKILLS]
            passives = [p for p in self.passives_for(head_type) if p.get("skill_name") not in SKIPPED_HEAD_SKILLS]
            for skill_name, why in SKIPPED_HEAD_SKILLS.items():
                if any((self.knowledge.skills.get(str(i)) or {}).get("name") == skill_name for i in head_type.get("skill_ids") or []):
                    self.miss_named(name, f"{skill_name}: {why}")
            fighter = Fighter(name, "enemy", slot, enemy["type_id"], head_type.get("affinity"),
                              rarity_number(head_type.get("rarity")), dict(stats), dict(stats), skills, passives,
                              level=int(enemy.get("level") or 0), dies_after_hit=True)
            fighter.immune = self.immunities(head_type)
            heads.append(fighter)
        return heads

    def miss_named(self, name: str, what: str) -> None:
        self.missing.setdefault(name, set()).add(what)

    def turn_limit(self) -> tuple[int, bool]:
        """The Hydra turn limit from the knowledge file, and whether it had to be assumed."""
        limits = (self.knowledge.raw.get("battle") or {}).get("turn_limits") or {}
        if limits.get(HYDRA_AREA):
            return int(limits[HYDRA_AREA]), False
        return ASSUMED_TURN_LIMIT, True


class HydraBattle(Battle):
    """One Hydra battle: champions hit the first standing head in the focus order; heads hit random champions."""

    def __init__(self, sim: HydraSimulator, fighters: list[Fighter], seed: int, record: bool, focus: list[str]):
        super().__init__(sim, fighters, seed, record)
        self.focus = [name.lower() for name in focus]
        self.turn_count = 0
        self.decapitations: list[dict[str, Any]] = []
        self.taken: dict[str, float] = {}

    def take_turn(self, actor: Fighter) -> None:
        self.turn_count += 1
        super().take_turn(actor)

    def boss_turns(self) -> int:
        return self.turn_count

    def run(self, turn_limit: int) -> dict[str, Any]:
        start_notes: list[str] = []
        for fighter in self.fighters:
            self.fire(fighter, sim_core.BATTLE_START_PHASES, start_notes)
        while self.turn_count < turn_limit:
            if not any(f.alive for f in self.fighters if f.side == "team"):
                break
            if not any(f.alive for f in self.fighters if f.side == "enemy"):
                break
            self.take_turn(self.next_actor())
        team = [f for f in self.fighters if f.side == "team"]
        return {
            "damage": round(sum(f.damage_dealt for f in team)),
            "by_champion": {f.name: round(f.damage_dealt) for f in team},
            "by_head": {name: round(amount) for name, amount in self.taken.items()},
            "turns": self.turn_count,
            "team_alive": sum(f.alive for f in team),
            "reached_limit": self.turn_count >= turn_limit,
            "decapitations": self.decapitations,
            "deaths": self.deaths,
            "tallies": self.tallies,
            "hits": self.hits,
        }

    def pick_target(self, actor: Fighter, skill: sim_core.Skill, allies: list[Fighter], enemies: list[Fighter]) -> Fighter | None:
        if skill.targets not in sim_core.ENEMY_SKILL_TARGETS:
            return super().pick_target(actor, skill, allies, enemies)
        alive = [e for e in enemies if e.alive]
        if not alive:
            return None
        if actor.side == "enemy":
            # Which champion a head's AI picks (lowest HP, most buffs, ...) isn't read from the game yet: random.
            return self.rng.choice(alive)
        provoke = next((s for s in actor.statuses if sim_core.status_family(s.name) == "Provoke" and s.producer
                        and s.producer.alive), None)
        if provoke is not None:
            return provoke.producer
        for name in self.focus:
            head = next((e for e in alive if e.name.lower() == name), None)
            if head is not None:
                return head
        return alive[0]

    def reduce_hp(self, target: Fighter, dealer: Fighter | None, amount: float) -> float:
        dealt = super().reduce_hp(target, dealer, amount)
        if target.side == "enemy" and dealer is not None and dealer.side == "team":
            self.taken[target.name] = self.taken.get(target.name, 0.0) + dealt
        return dealt

    def die(self, target: Fighter, dealer: Fighter | None) -> None:
        if target.side == "enemy":
            self.decapitations.append({"head": target.name, "turn": self.turn_count, "by": dealer.name if dealer else None})
            target.alive = False
            target.hp = 0.0
            target.statuses = []
            target.stamina = 0
            return
        super().die(target, dealer)


def simulate_hydra(knowledge: Any, roster: dict[str, Any], team: list[dict[str, Any]], difficulty: str = "Normal",
                   season: int | None = None, focus: list[str] | None = None, runs: int = sim_core.DEFAULT_RUNS,
                   seed: int = 1, turn_limit: int | None = None, label: str | None = None) -> dict[str, Any]:
    """Plays one team against the Hydra `runs` times (seeds seed..seed+runs-1) and sums up the results."""
    sim = HydraSimulator(knowledge, roster)
    found = sim.hydra_stage(difficulty, season)
    heads = sim.head_fighters(found["stage"])
    team_templates = sim.team_fighters(team, max_size=HYDRA_TEAM_SIZE)
    limit, assumed_limit = (int(turn_limit), False) if turn_limit else sim.turn_limit()
    order = list(focus or [])
    unknown = [name for name in order if not any(h.name.lower() == name.lower() for h in heads)]
    if unknown:
        raise BattleError(f"No head called {', '.join(unknown)} here. Heads: {', '.join(h.name for h in heads)}.")
    runs = max(1, min(int(runs or sim_core.DEFAULT_RUNS), sim_core.MAX_RUNS))
    results, timeline = [], []
    for run in range(runs):
        fighters = sim.fresh_copy(team_templates + heads)
        battle = HydraBattle(sim, fighters, seed + run, record=run == 0, focus=order)
        results.append(battle.run(limit))
        if run == 0:
            timeline = battle.timeline
    damages = [r["damage"] for r in results]
    tallies: dict[str, Tally] = {}
    for result in results:
        for name, tally in result["tallies"].items():
            total = tallies.setdefault(name, Tally())
            for key in ("attempted", "landed", "resisted", "missed_chance", "blocked"):
                setattr(total, key, getattr(total, key) + getattr(tally, key))
    decapitations: dict[str, list[int]] = {}
    for result in results:
        for cut in result["decapitations"]:
            decapitations.setdefault(cut["head"], []).append(cut["turn"])
    return {
        "team": label or " / ".join(f.name for f in team_templates),
        "encounter": f"Hydra {found['season']} {found['tier']}",
        "heads": [{"head": h.name, "affinity": h.affinity, "level": h.level,
                   **{k: round(v, 2) for k, v in h.stats.items() if v}} for h in heads],
        "focus": order,
        "runs": runs,
        "seed": seed,
        "turn_limit": limit,
        "damage": {
            "mean": round(statistics.mean(damages)),
            "min": min(damages),
            "p10": round(percentile(damages, 0.1)),
            "median": round(statistics.median(damages)),
            "p90": round(percentile(damages, 0.9)),
            "max": max(damages),
        },
        "per_run": damages,
        "turns": {"mean": round(statistics.mean(r["turns"] for r in results), 1),
                  "min": min(r["turns"] for r in results), "max": max(r["turns"] for r in results)},
        "reached_turn_limit": sum(r["reached_limit"] for r in results),
        "damage_by_champion": {f.name: round(statistics.mean(r["by_champion"][f.name] for r in results))
                               for f in team_templates},
        "damage_by_head": {h.name: round(statistics.mean(r["by_head"].get(h.name, 0) for r in results)) for h in heads},
        "decapitations": {head: {"runs": len(turns), "mean_turn": round(statistics.mean(turns), 1)}
                          for head, turns in decapitations.items()},
        "first_death": sim_core.summarize_deaths(results),
        "debuffs": {name: {"attempted": t.attempted, "landed": t.landed, "resisted": t.resisted,
                           "chance_failed": t.missed_chance, "immune": t.blocked,
                           "resist_rate": round(t.resisted / max(t.attempted - t.missed_chance - t.blocked, 1), 3)}
                    for name, t in sorted(tallies.items())},
        "champions": [{
            "champion": f.name,
            "slot": f.slot,
            "affinity": f.affinity,
            "stats": {k: round(f.stats[k], 2) for k in ("health", "attack", "defence", "speed", "accuracy", "resistance",
                                                         "critical_chance", "critical_damage")},
            "skills": [{"skill": f"{s.label} {s.name}", "cooldown": s.cooldown or None} for s in f.skills],
            "priority": f.priority or sim_core.default_priority(f.skills),
            "coverage": sim.coverage(f),
        } for f in team_templates],
        "timeline": timeline,
        "not_simulated": {name: sorted(items) for name, items in sorted(sim.missing.items())},
        "assumed": hydra_assumptions(sim, assumed_limit, limit, season is None),
    }


def hydra_assumptions(sim: HydraSimulator, assumed_limit: bool, limit: int, default_season: bool) -> list[str]:
    notes = [
        "Turn order, damage, DEF, hit types, resist chances, and the heads' own passives (AoE damage cut, MAX HP "
        "damage caps, immunities) follow the game's code and data (build 2026-10-19).",
        "A decapitated head stays down for the rest of the battle: the neck, regrowth, Devour, Hunger counters and "
        "Wrath's hit counter aren't modelled yet, so long battles read low.",
        "Champions attack the first standing head in the focus order (then the first standing head); a Provoked champion "
        "attacks its provoker. Heads pick a random champion and use their skills newest-first.",
        f"The battle ends after {limit} turns by anyone, when every head is down, or when the team is dead"
        + (" (turn limit assumed: this knowledge file has none for Hydra)." if assumed_limit else "."),
    ]
    if default_season:
        notes.append("Season: the knowledge file doesn't say which Hydra season is live, so the last one is used. "
                     "Pass season to pick another.")
    return notes + sim_core.assumptions(sim)[2:]
