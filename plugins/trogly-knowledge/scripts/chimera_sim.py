"""Alliance Chimera battles on the whole-battle simulator. Design and status: Feature #763 (story #765); the engine is
`battle_sim`.

One boss with real HP. At the start of each of its turns the Chimera takes the form ConsiderChimeraForm gives for its
turn count (0x182CA0A80): the form of the first entry in StaticAllianceData.ChimeraSequenceForms whose key is above the
turns it has taken, Ultimate after the last. The last key is also its turn limit (ChimeraMaxTurns, 0x1822653F0; checked
against the boss's own TurnCount as for the Clan Boss). A form change swaps in that form's kit (HeroType.Forms, indexed
by ChimeraForm) and clears its buffs and debuffs, as Unholy Metamorph says. Knowledge files without the form sequence
or the form kits keep the Chimera in its first (Ultimate) form with an assumed turn limit.

Trials (build 2026-10-19) are the Chimera's own skills: each form's ChallengeSkillTypeIds, whose effects carry
ChallengeParams (BattleHero.InitChallenges, 0x182E09200, keyed by skill id). StartChallenge starts one unless it is
done (0x182DD9D90). SetChallengeProgress sets its progress to the formula's value, capped at the target, never lower;
reaching the target completes it on the Chimera's current turn (0x182DD5F30). SetChallengeCounter sets its counter,
capped at the limit; reaching the limit resets the counter and the progress (0x182DD5C00). A form's Trials act only
while the Chimera is in that form. countCompletedChallenges counts the completed ones (Savage Evolution's boosts), and
the battle's competition multiplier is basic + advanced x completed (UpdateChimeraCompetitionMultiplier,
0x182DD6280). Knowledge files without Trial skills count none completed.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

import battle_sim as sim_core
from battle_sim import Battle, BattleError, Fighter, Resolver, Simulator, Tally, percentile, rarity_number

CHIMERA_AREA = "Chimera"
# AllianceChimeraDifficulty 1-6, in stage order within each rotation.
CHIMERA_DIFFICULTIES = ["Easy", "Normal", "Hard", "Brutal", "Nightmare", "UltraNightmare"]
# ChimeraForm: the index into HeroType.Forms.
CHIMERA_FORMS = ["Ultimate", "Ram", "Lion", "Snake"]
CHIMERA_TEAM_SIZE = 5
# Used only when the knowledge file has no Chimera form sequence (whose last key is the turn limit).
ASSUMED_TURN_LIMIT = 50
TRIAL_KINDS = frozenset({"StartChallenge", "SetChallengeProgress", "SetChallengeCounter"})


@dataclass
class Trial:
    """SharedModel.Battle.Core.Hero.Challenge: one Trial's state in a battle."""
    target: float
    limit: int | None
    started: bool = False
    progress: float = 0.0
    counter: int = 0
    completed_turn: int | None = None

    @property
    def completed(self) -> bool:
        return self.progress >= self.target - 1e-9


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
        self.trials: dict[int, dict[str, Any]] = {}  # Trial skill id: name, form, part, tier, target, limit
        self.difficulties = {d["boss"]["difficulty"]: d for d in bosses.get("chimera") or [] if d.get("boss")}
        self.tier: str | None = None

    def trial_passives(self, skill_ids: list[int], form: int) -> list[dict[str, Any]]:
        """A form's Trial skills as passives tagged with their Trial, and each Trial's target and limit."""
        named = {t["skill_id"]: t for d in self.difficulties.values() for t in d.get("trials") or []}
        own = (self.difficulties.get(self.tier or "") or {}).get("trials") or []
        named.update({t["skill_id"]: t for t in own})
        passives = []
        for skill_id in skill_ids:
            skill = self.knowledge.skills.get(str(skill_id)) or {}
            effects = skill.get("effects") or []
            params = next((e.get("params") for e in effects if "ChallengeParams.TargetProgress" in (e.get("params") or {})), None)
            if params is None:
                continue
            info = named.get(skill_id) or {}
            limit = params.get("ChallengeParams.CounterLimit")
            self.trials[skill_id] = {
                "name": skill.get("name") or f"Trial {skill_id}",
                "form": info.get("form") or CHIMERA_FORMS[form], "part": info.get("part"), "tier": info.get("tier"),
                "target": float(params["ChallengeParams.TargetProgress"]),
                "limit": int(limit) if limit is not None else None,
                "damage": params.get("ChallengeParams.BasedOnDamage") == "true",
            }
            passives += [{**e, "skill_name": skill.get("name"), "trial": skill_id} for e in effects]
        return passives

    def competition(self) -> tuple[float, float] | None:
        """The stage's competition multipliers (basic, advanced), when the knowledge file has them."""
        found = self.difficulties.get(self.tier or "")
        if not found or found.get("competition_basic_multiplier") is None:
            return None
        return float(found["competition_basic_multiplier"]), float(found.get("competition_advanced_multiplier") or 0.0)

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
        self.tier = tier
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
            trials = self.trial_passives(list(form.get("challenge_skill_ids") or []), index)
            self.form_kits[index] = (self.skills_for(kit, {}, name), self.passives_for(kit) + trials)
        skills, passives = self.form_kits.get(0) or (self.skills_for(chimera_type, {}, name), self.passives_for(chimera_type))
        fighter = Fighter(name, "enemy", 1, enemy["type_id"], chimera_type.get("affinity"),
                          rarity_number(chimera_type.get("rarity")), dict(stats), dict(stats), skills, passives,
                          is_boss=True, level=int(enemy.get("level") or 0))
        fighter.immune = self.immunities(chimera_type)
        if not self.form_kits or not self.sequence:
            self.miss(fighter, "form changes (this knowledge file predates the Chimera forms and form sequence; run "
                               "refresh_knowledge)")
        if not self.trials:
            self.miss(fighter, "Trials (this knowledge file has no Trial skills, so none count as completed and Savage "
                               "Evolution's boosts stay off; run refresh_knowledge)")
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

    encounter_kinds = TRIAL_KINDS

    def __init__(self, sim: ChimeraSimulator, fighters: list[Fighter], seed: int, record: bool):
        super().__init__(sim, fighters, seed, record)
        self.chimera = next(f for f in fighters if f.side == "enemy")
        self.form = 0
        self.form_changes: list[dict[str, Any]] = []
        self.trials = {skill_id: Trial(info["target"], info["limit"]) for skill_id, info in sim.trials.items()}

    def completed_trials(self) -> int:
        return sum(1 for trial in self.trials.values() if trial.completed)

    def encounter_value(self, name: str, owner: Fighter) -> float | None:
        if name == "countCompletedChallenges":
            return float(self.completed_trials())
        if name == "needToChangeChimeraForm":
            return float(self.sim.form_for(owner.turns_taken) != self.form)
        if name == "relevantChimeraForm":
            return float(self.sim.form_for(owner.turns_taken))
        return None

    def encounter_call(self, name: str, args: list[Any], resolver: Resolver) -> float | None:
        """challenge...ById(skill id): one Trial's state (ChallengeParamsBuilder, 0x182CF4A10)."""
        if not name.startswith("challenge") or not name.endswith("ById"):
            return super().encounter_call(name, args, resolver)
        trial = self.trials.get(int(resolver.argument(args[0])))
        if trial is None:
            return 0.0
        values = {
            "challengeCurrentProgressById": trial.progress,
            "challengeCurrentCounterById": float(trial.counter),
            "challengeTargetProgressById": trial.target,
            "challengeLimitCounterById": float(trial.limit or 0),
            "challengeStartedById": float(trial.started),
            "challengeBasedOnDamageById": float(self.sim.trials[int(resolver.argument(args[0]))]["damage"]),
            "challengeCompletedById": float(trial.completed),
            "challengeInProgressById": float(not trial.completed),
        }
        return values.get(name)

    def trial_effect(self, effect: dict[str, Any], context: sim_core.SkillContext, allies: list[Fighter],
                     enemies: list[Fighter], notes: list[str]) -> None:
        """StartChallengeProcessor, SetChallengeProgressProcessor, SetChallengeCounterProcessor on the Chimera's Trial."""
        trial = self.trials.get(effect.get("trial"))
        if trial is None:
            self.sim.miss(context.actor, f"{context.skill.name}: {effect['kind']} outside a Trial")
            return
        targets = self.effect_targets(effect, context, allies, enemies) or [None]
        target = next((t for t in targets if self.condition_holds(effect, context, t)), False)
        if target is False:
            return
        kind = effect["kind"]
        if kind == "StartChallenge":
            if not trial.completed:
                trial.started = True
            return
        if not trial.started or trial.completed:
            return
        value = self.value(effect, context, target)
        if value is None:
            return
        self.count_use(context.actor, effect)
        if kind == "SetChallengeProgress":
            progress = min(value, trial.target)
            if progress < trial.progress:
                return
            trial.progress = progress
            if trial.completed:
                trial.completed_turn = self.chimera.turns_taken
                notes.append(f"Trial done: {self.sim.trials[effect['trial']]['name']}")
        elif trial.limit and trial.limit > 0:
            counter = min(int(value), trial.limit)
            if counter < 0:
                return
            trial.counter = counter
            if counter == trial.limit:
                trial.counter, trial.progress = 0, 0.0  # Challenge.Reset: the Trial starts over

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
        if effect["kind"] in TRIAL_KINDS:
            self.trial_effect(effect, context, allies, enemies, notes)
            return
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
            "trials": {skill_id: trial.completed_turn for skill_id, trial in self.trials.items() if trial.completed},
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
        "trials": trial_summary(sim, results) if sim.trials else None,
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


def trial_summary(sim: ChimeraSimulator, results: list[dict[str, Any]]) -> dict[str, Any]:
    """How many Trials a battle completes, which ones and on which Chimera turn, and the competition multiplier."""
    counts = [len(r["trials"]) for r in results]
    each = {}
    for skill_id, info in sim.trials.items():
        turns = [r["trials"][skill_id] for r in results if skill_id in r["trials"]]
        each[info["name"]] = {"form": info["form"], "part": info["part"], "tier": info["tier"],
                              "completed_runs": len(turns),
                              "mean_turn": round(statistics.mean(turns), 1) if turns else None}
    summary: dict[str, Any] = {"completed_mean": round(statistics.mean(counts), 2), "completed_max": max(counts),
                               "each": each}
    competition = sim.competition()
    if competition is not None:
        basic, advanced = competition
        summary["competition_multiplier_mean"] = round(statistics.mean(basic + advanced * c for c in counts), 3)
    return summary


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
        ("Trials follow the game's code: each form's Trial skills start, progress and reset on the game's own "
         "formulas while the Chimera is in that form, and completed ones feed Savage Evolution and the competition "
         "multiplier." if sim.trials else
         "Trials count as none completed: this knowledge file has no Trial skills. Run refresh_knowledge.")
        + " The Chimera picks a random champion and uses its skills newest-first.",
        f"The battle ends after the Chimera's {limit}th turn, when it dies, or when the team is dead"
        + (" (turn limit assumed: this knowledge file has no Chimera form sequence)." if assumed_limit else "."),
    ]
    if default_rotation:
        notes.append("Rotation: the knowledge file doesn't say which Chimera rotation is live, so the last one is used. "
                     "Pass rotation to pick another.")
    return notes + sim_core.assumptions(sim)[2:]
