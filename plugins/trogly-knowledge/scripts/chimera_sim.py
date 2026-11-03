"""Alliance Chimera battles on the whole-battle simulator. Design and status: Feature #763 (story #765); the engine is
`battle_sim`.

One boss with real HP. At the start of each of its turns the Chimera takes the form ConsiderChimeraForm gives for its
turn count (0x182CA0A80): the form of the first entry in StaticAllianceData.ChimeraSequenceForms whose key is above the
turns it has taken, Ultimate after the last. The last key is also its turn limit (ChimeraMaxTurns, 0x1822653F0; checked
against the boss's own TurnCount as for the Clan Boss). A form change swaps in that form's kit (HeroType.Forms, indexed
by ChimeraForm) and clears its buffs and debuffs, as Unholy Metamorph says. Knowledge files without the form sequence
or the form kits keep the Chimera in its first (Ultimate) form with an assumed turn limit. Trials count as none
completed, so Savage Evolution's boosts stay off.
"""

from __future__ import annotations

import statistics
from typing import Any

import battle_sim as sim_core
from battle_sim import Battle, BattleError, Fighter, Simulator, Tally, percentile, rarity_number

CHIMERA_AREA = "Chimera"
# AllianceChimeraDifficulty 1-6, in stage order within each rotation.
CHIMERA_DIFFICULTIES = ["Easy", "Normal", "Hard", "Brutal", "Nightmare", "UltraNightmare"]
# ChimeraForm: the index into HeroType.Forms.
CHIMERA_FORMS = ["Ultimate", "Ram", "Lion", "Snake"]
CHIMERA_TEAM_SIZE = 5
# Used only when the knowledge file has no Chimera form sequence (whose last key is the turn limit).
ASSUMED_TURN_LIMIT = 50


class ChimeraSimulator(Simulator):
    """Builds the Chimera of one rotation and difficulty, with its form kits and form sequence."""

    def __init__(self, knowledge: Any, roster: dict[str, Any]):
        super().__init__(knowledge, roster)
        self.area = CHIMERA_AREA
        bosses = knowledge.raw.get("alliance_bosses") or {}
        self.slots = int(bosses.get("chimera_slots") or CHIMERA_TEAM_SIZE)
        self.sequence: list[tuple[int, int]] = sorted(
            (int(turn), CHIMERA_FORMS.index(form)) for turn, form in (bosses.get("chimera_form_sequence") or {}).items()
            if form in CHIMERA_FORMS)
        self.form_kits: dict[int, tuple[list[sim_core.Skill], list[dict[str, Any]]]] = {}

    def chimera_stage(self, difficulty: str, rotation: int | None) -> dict[str, Any]:
        regions = sorted((r for r in self.knowledge.raw.get("regions") or [] if r.get("area") == CHIMERA_AREA),
                         key=lambda r: r["id"])
        if not regions:
            raise BattleError("The knowledge file has no Chimera. Run refresh_knowledge with RAID open.")
        if rotation is None:
            region = regions[-1]
        else:
            region = next((r for r in regions if r["code"].endswith(f"Rotation{int(rotation)}")), None)
            if region is None:
                raise BattleError(f"No Chimera rotation {rotation}. Rotations: 1 to {len(regions)}.")
        key = difficulty.replace(" ", "").replace("-", "").lower()
        tier = next((t for t in CHIMERA_DIFFICULTIES if t.lower() == key), None)
        if tier is None:
            raise BattleError(f"Unknown Chimera difficulty '{difficulty}'. Use one of: {', '.join(CHIMERA_DIFFICULTIES)}.")
        stages = sorted((s for group in region["difficulties"].values() for s in group), key=lambda s: s["number"])
        index = CHIMERA_DIFFICULTIES.index(tier)
        if index >= len(stages):
            raise BattleError(f"Chimera {region['code']} has no {tier} stage.")
        return {"stage": stages[index], "tier": tier, "rotation": region["code"].replace("Chimera", "")}

    def chimera_fighter(self, stage: dict[str, Any]) -> Fighter:
        enemy = stage["enemies"][0]
        chimera_type = self.knowledge.types.get(enemy["type_id"]) or {}
        stats = dict(enemy.get("stats") or {})
        if not stats:
            raise BattleError("This knowledge file has no Chimera stats. Run refresh_knowledge with RAID open.")
        for key in sim_core.STAT_KEYS:
            stats.setdefault(key, 0.0)
        name = "Chimera"
        for index, form in enumerate(chimera_type.get("forms") or []):
            kit = {"skill_ids": list(form.get("skill_ids") or []) + list(form.get("additional_skill_ids") or [])}
            self.form_kits[index] = (self.skills_for(kit, {}, name), self.passives_for(kit))
        skills, passives = self.form_kits.get(0) or (self.skills_for(chimera_type, {}, name), self.passives_for(chimera_type))
        fighter = Fighter(name, "enemy", 1, enemy["type_id"], chimera_type.get("affinity"),
                          rarity_number(chimera_type.get("rarity")), dict(stats), dict(stats), skills, passives,
                          is_boss=True, level=int(enemy.get("level") or 0))
        fighter.immune = self.immunities(chimera_type)
        if not self.form_kits or not self.sequence:
            self.miss(fighter, "form changes (this knowledge file predates the Chimera forms and form sequence; run "
                               "refresh_knowledge)")
        self.miss(fighter, "Trials (counted as none completed, so Savage Evolution's boosts stay off)")
        return fighter

    def turn_limit(self) -> tuple[int, bool]:
        if self.sequence:
            return self.sequence[-1][0], False
        return ASSUMED_TURN_LIMIT, True

    def form_for(self, turns_taken: int) -> int:
        """ConsiderChimeraForm: the form of the first sequence key above the turns taken, else Ultimate."""
        for turn, form in self.sequence:
            if turns_taken < turn:
                return form
        return 0


class ChimeraBattle(Battle):
    """One Chimera battle: champions hit the Chimera; it changes form on its own turns and keeps the damage it takes."""

    def __init__(self, sim: ChimeraSimulator, fighters: list[Fighter], seed: int, record: bool):
        super().__init__(sim, fighters, seed, record)
        self.chimera = next(f for f in fighters if f.side == "enemy")
        self.form = 0
        self.form_changes: list[dict[str, Any]] = []

    def encounter_value(self, name: str, owner: Fighter) -> float | None:
        if name == "countCompletedChallenges":
            return 0.0
        if name == "needToChangeChimeraForm":
            return float(self.sim.form_for(owner.turns_taken) != self.form)
        if name == "relevantChimeraForm":
            return float(self.sim.form_for(owner.turns_taken))
        return None

    def take_turn(self, actor: Fighter) -> None:
        if actor is self.chimera and self.sim.form_kits:
            wanted = self.sim.form_for(actor.turns_taken)
            if wanted != self.form and wanted in self.sim.form_kits:
                self.change_form(wanted)
        super().take_turn(actor)

    def change_form(self, form: int) -> None:
        """Unholy Metamorph: the form's kit, and no buffs or debuffs. Cooldowns start afresh (assumed; the export's
        ChangeHeroFormParams.ResetEffectsAndCooldowns says for sure)."""
        chimera = self.chimera
        skills, passives = self.sim.form_kits[form]
        chimera.skills = skills
        chimera.passives = passives
        chimera.statuses = []
        chimera.cooldowns = {s.label: s.start_cooldown for s in skills}
        self.form = form
        self.form_changes.append({"turn": chimera.turns_taken, "form": CHIMERA_FORMS[form]})

    def apply_effect(self, effect: dict[str, Any], context: sim_core.SkillContext, allies: list[Fighter],
                     enemies: list[Fighter], notes: list[str]) -> None:
        if effect["kind"] == "ChangeHeroForm":
            return  # done at the start of the Chimera's turn (take_turn above)
        super().apply_effect(effect, context, allies, enemies, notes)

    def reduce_hp(self, target: Fighter, dealer: Fighter | None, amount: float) -> float:
        if target is not self.chimera:
            return super().reduce_hp(target, dealer, amount)
        if amount <= 0 or not target.alive:
            return 0.0
        before = target.hp
        target.hp = max(target.hp - amount, 0.0)
        dealt = before - target.hp
        if dealer is not None and dealer.side == "team":
            dealer.damage_dealt += dealt
        if target.hp <= 0:
            target.alive = False
        return dealt

    def run(self, turn_limit: int) -> dict[str, Any]:
        start_notes: list[str] = []
        for fighter in self.fighters:
            self.fire(fighter, sim_core.BATTLE_START_PHASES, start_notes)
        while self.chimera.alive and self.chimera.turns_taken < turn_limit:
            if not any(f.alive for f in self.fighters if f.side == "team"):
                break
            self.take_turn(self.next_actor())
        team = [f for f in self.fighters if f.side == "team"]
        return {
            "damage": round(sum(f.damage_dealt for f in team)),
            "by_champion": {f.name: round(f.damage_dealt) for f in team},
            "boss_turns": self.chimera.turns_taken,
            "killed": not self.chimera.alive,
            "team_alive": sum(f.alive for f in team),
            "reached_limit": self.chimera.turns_taken >= turn_limit,
            "form_changes": self.form_changes,
            "deaths": self.deaths,
            "tallies": self.tallies,
            "hits": self.hits,
        }


def simulate_chimera(knowledge: Any, roster: dict[str, Any], team: list[dict[str, Any]], difficulty: str = "Normal",
                     rotation: int | None = None, runs: int = sim_core.DEFAULT_RUNS, seed: int = 1,
                     turn_limit: int | None = None, label: str | None = None) -> dict[str, Any]:
    """Plays one team against the Chimera `runs` times (seeds seed..seed+runs-1) and sums up the results."""
    sim = ChimeraSimulator(knowledge, roster)
    found = sim.chimera_stage(difficulty, rotation)
    chimera = sim.chimera_fighter(found["stage"])
    team_templates = sim.team_fighters(team, max_size=sim.slots)
    limit, assumed_limit = (int(turn_limit), False) if turn_limit else sim.turn_limit()
    runs = max(1, min(int(runs or sim_core.DEFAULT_RUNS), sim_core.MAX_RUNS))
    results, timeline = [], []
    for run in range(runs):
        fighters = sim.fresh_copy(team_templates + [chimera])
        battle = ChimeraBattle(sim, fighters, seed + run, record=run == 0)
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
    return {
        "team": label or " / ".join(f.name for f in team_templates),
        "encounter": f"Chimera {found['rotation']} {found['tier']}",
        "boss": {"name": chimera.name, "affinity": chimera.affinity, "level": chimera.level,
                 **{k: round(v, 2) for k, v in chimera.stats.items() if v}},
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
        "boss_turns": {"mean": round(statistics.mean(r["boss_turns"] for r in results), 1),
                       "min": min(r["boss_turns"] for r in results), "max": max(r["boss_turns"] for r in results)},
        "killed": sum(r["killed"] for r in results),
        "reached_turn_limit": sum(r["reached_limit"] for r in results),
        "form_changes": results[0]["form_changes"],
        "damage_by_champion": {f.name: round(statistics.mean(r["by_champion"][f.name] for r in results))
                               for f in team_templates},
        "first_death": sim_core.summarize_deaths(results),
        "debuffs": {name: {"attempted": t.attempted, "landed": t.landed, "resisted": t.resisted,
                           "chance_failed": t.missed_chance, "boss_immune": t.blocked,
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
        "assumed": chimera_assumptions(sim, assumed_limit, limit, rotation is None),
    }


def chimera_assumptions(sim: ChimeraSimulator, assumed_limit: bool, limit: int, default_rotation: bool) -> list[str]:
    notes = [
        "Turn order, damage, DEF, hit types, resist chances, and the Chimera's passives follow the game's code and data "
        "(build 2026-10-19).",
        ("The Chimera takes the form the game's sequence gives for the turns it has taken, at the start of its turn; a "
         "change swaps its kit, clears its buffs and debuffs, and restarts its cooldowns (assumed). Its stats stay the "
         "stage's in every form (assumed)."
         if sim.form_kits and sim.sequence else
         "The Chimera stays in its first (Ultimate) form: this knowledge file has no form kits or form sequence. Run "
         "refresh_knowledge."),
        "Trials count as none completed. The Chimera picks a random champion and uses its skills newest-first.",
        f"The battle ends after the Chimera's {limit}th turn, when it dies, or when the team is dead"
        + (" (turn limit assumed: this knowledge file has no Chimera form sequence)." if assumed_limit else "."),
    ]
    if default_rotation:
        notes.append("Rotation: the knowledge file doesn't say which Chimera rotation is live, so the last one is used. "
                     "Pass rotation to pick another.")
    return notes + sim_core.assumptions(sim)[2:]
