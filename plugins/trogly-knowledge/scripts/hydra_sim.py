"""Alliance Hydra battles on the whole-battle simulator: four heads at once, a turn limit, and the player's own focus
order. Design and status: Feature #759 (story #761); the engine is `battle_sim`.

The score is the damage the team deals to the heads (necks included). A head that reaches 0 HP is decapitated after
the hit's own passives run (Tide of Hope heals the team). Its Shed Head passive turns it into the hero variant its
Transformation names (the neck); the neck's GrowHydraHead skill later replaces it with a fresh head
(GrowHydraHeadProcessor, 0x182DC3D10): a head type not standing, its own with GrowSelfProbability and each other with an
equal share of the rest (NewHeadSetup, 0x182DB6750). Knowledge files without hero variants keep a decapitated head down.

Devour (build 2026-10-19). Ravening places a Hunger counter on one champion when none has one
(PlaceHungerCounterProcessor, 0x182DD2050): the first time on the champion with the most CRIT DMG, later at random
weighted 10 + round((CRIT DMG% - 50) * 0.2), skipping the champions spat out most recently
(IterationsBetweenDevouring of them). Each Ravening head's own turn sets it one lower; a Feast head that isn't
digesting uses Feast on the champion whose counter is at 0 (HydraReadyToEatAndTargetReadyToDevouring, 0x182E58790).
Devour (0x182DC1080) clears the champion's buffs and debuffs, empties its turn meter, takes it out of the battle, and
gives the head Digestion: DigestionStrengthFormula of damage it absorbs, for DigestionLifetimeFormula turns. Damage
that empties it spits the champion out (DigestionRemovingProcessor, cause Destroyed); running out of turns kills it
(cause Expired). A head that dies or turns into its neck releases the champion when the Devoured status is linked to
its producer (StatusEffectParams.UnapplyWhenProducerDied, UnapplyAllEffects 0x182E0F820). Knowledge files without the
digestion formulas leave Feast and Ravening out. Wrath's hit counter is listed under `not_simulated`.
"""

from __future__ import annotations

import statistics
from typing import Any

import battle_sim as sim_core
import formula
from battle_sim import Battle, BattleError, Fighter, Resolver, Simulator, Status, Tally, percentile, rarity_number

HYDRA_AREA = "Hydra"
# AllianceHydraDifficultyLevel, in stage order within each season.
HYDRA_DIFFICULTIES = ["Normal", "Hard", "Brutal", "Nightmare"]
HYDRA_TEAM_SIZE = 6
# Used only when the knowledge file has no Hydra turn limit (BattleSettings.SpecificMaxTurnsInBattleByArea).
ASSUMED_TURN_LIMIT = 300
# Head skills the simulator leaves out, and why. Death Throes targets "AllAllies except Producer", and which side that
# means when a champion's hit sets it off isn't read from the game's code yet. Feast and Ravening are left out only when
# the knowledge file lacks Devour's digestion formulas (DEVOUR_SKIPS).
SKIPPED_HEAD_SKILLS = {
    "Death Throes [P]": "which side it hits when a champion decapitates the head isn't read yet",
}
DEVOUR_SKIPS = {
    "Feast": "Devour: this knowledge file has no digestion formulas (run refresh_knowledge)",
    "Ravening [P]": "Hunger counters: they only matter for Feast",
}
# PlaceHungerCounterProcessor.Weight: a champion's chance to get the Hunger counter.
HUNGER_BASE_WEIGHT = 10
HUNGER_CRIT_DAMAGE_WEIGHT = 0.2
# Used only when the knowledge file lacks PlaceHungerCounterParams.IterationsBetweenDevouring.
ASSUMED_DEVOUR_GAP = 1


class HydraSimulator(Simulator):
    """Builds the heads of one Hydra season and difficulty, and the necks they turn into."""

    def __init__(self, knowledge: Any, roster: dict[str, Any]):
        super().__init__(knowledge, roster)
        self.area = HYDRA_AREA
        self.variants = {v["id"]: v for v in knowledge.raw.get("hero_variants") or []}
        self.head_templates: list[Fighter] = []
        self.devour = False
        # Whether a head that dies or transforms lets its champion go: the Devoured status's link to its producer.
        devoured = ((self.status_types.get("Devoured") or {}).get("effect") or {}).get("params") or {}
        flags = [devoured.get(f"StatusEffectParams.{name}") for name in ("UnapplyWhenProducerDied",
                                                                        "UnapplyWhenProducerBossDied")]
        self.release_assumed = all(flag is None for flag in flags)
        self.release_on_head_death = self.release_assumed or "true" in flags

    def skipped_skills(self, head_type: dict[str, Any]) -> dict[str, str]:
        """The head's skills left out: Feast and Ravening too unless Feast's Devour has its digestion formulas."""
        effects = [e for i in head_type.get("skill_ids") or [] for e in (self.knowledge.skills.get(str(i)) or {}).get("effects") or []]
        if any(e["kind"] == "Devour" and "DevourParams.DigestionLifetimeFormula" in (e.get("params") or {}) for e in effects):
            self.devour = True
            return dict(SKIPPED_HEAD_SKILLS)
        return {**SKIPPED_HEAD_SKILLS, **DEVOUR_SKIPS}

    def neck_variant(self, head: Fighter) -> dict[str, Any] | None:
        """The variant the head's Shed Head passive (a Transformation) turns it into, when the knowledge file has it."""
        for passive in head.passives:
            if passive["kind"] == "Transformation":
                variant_id = (passive.get("params") or {}).get("TransformationParams.VariantId")
                if variant_id is not None:
                    return self.variants.get(int(variant_id))
        return None

    def neck_fighter(self, head: Fighter, variant: dict[str, Any]) -> Fighter:
        """The head's neck: the variant's skills, and its stats from the head's (StatsMultiplier per stat, or the
        variant's own base stats; how ResolveOwnVariantStats combines them is assumed)."""
        stats = dict(head.stats)
        multiplier = variant.get("stats_multiplier") or {}
        for stat, factor in multiplier.items():
            key = sim_core.aura_stat(stat)
            if key and factor:
                stats[key] = stats[key] * factor
        if not multiplier:
            for stat, value in (variant.get("base_stats") or {}).items():
                key = sim_core.aura_stat(stat)
                if key and value:
                    stats[key] = value
        kit = {"skill_ids": variant.get("skill_ids") or []}
        name = f"{head.name} neck"
        neck = Fighter(name, "enemy", head.slot, head.type_id, head.affinity, head.rarity, dict(stats), stats,
                       self.skills_for(kit, {}, name), self.passives_for(kit), level=head.level, dies_after_hit=True)
        neck.immune = set(head.immune)
        neck.cooldowns = {s.label: s.start_cooldown for s in neck.skills}
        neck.hp = neck.max_hp
        return neck

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
            skipped = self.skipped_skills(head_type)
            skills = [s for s in self.skills_for(head_type, {}, name) if s.name not in skipped]
            passives = [p for p in self.passives_for(head_type) if p.get("skill_name") not in skipped]
            for skill_name, why in skipped.items():
                if any((self.knowledge.skills.get(str(i)) or {}).get("name") == skill_name for i in head_type.get("skill_ids") or []):
                    self.miss_named(name, f"{skill_name}: {why}")
            fighter = Fighter(name, "enemy", slot, enemy["type_id"], head_type.get("affinity"),
                              rarity_number(head_type.get("rarity")), dict(stats), dict(stats), skills, passives,
                              level=int(enemy.get("level") or 0), dies_after_hit=True)
            fighter.immune = self.immunities(head_type)
            # Feast is used only on a champion whose Hunger counter is at 0 (HydraBattle.choose_skill), never by order.
            fighter.never = {s.label for s in skills if "Devour" in s.kinds()}
            heads.append(fighter)
        self.head_templates = heads
        if not self.variants:
            for head in heads:
                self.miss_named(head.name, "neck and regrowth (this knowledge file predates hero variants; run "
                                           "refresh_knowledge)")
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

    encounter_kinds = frozenset({"PlaceHungerCounter", "SetHungerCounter"})

    def __init__(self, sim: HydraSimulator, fighters: list[Fighter], seed: int, record: bool, focus: list[str]):
        super().__init__(sim, fighters, seed, record)
        self.focus = [name.lower() for name in focus]
        self.turn_count = 0
        self.decapitations: list[dict[str, Any]] = []
        self.taken: dict[str, float] = {}
        self.swallowed: list[tuple[Fighter, Fighter]] = []  # (head, the champion it is digesting)
        self.digested: set[str] = set()
        self.spat_out: dict[str, int] = {}  # champion: the battle turn it was last spat out
        self.devours: list[dict[str, Any]] = []
        self.absorbed = 0.0
        self.feast_target: Fighter | None = None

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
            "devours": self.devours,
            "absorbed": round(self.absorbed),
            "deaths": self.deaths,
            "tallies": self.tallies,
            "hits": self.hits,
        }

    # Devour

    def hunger_counter(self, fighter: Fighter) -> Status | None:
        return next((s for s in fighter.statuses if s.name == "HungerCounter"), None)

    def hunger_holder(self) -> Fighter | None:
        return next((f for f in self.fighters if f.side == "team" and f.alive and self.hunger_counter(f)), None)

    def digesting(self, head: Fighter) -> bool:
        return any(s.name == "Digestion" for s in head.statuses)

    def held(self, champion: Fighter) -> bool:
        """Swallowed or digested: out of reach of revives (ReviveProcessor checks IsDevoured)."""
        return champion.name in self.digested or any(victim is champion for _, victim in self.swallowed)

    def encounter_value(self, name: str, owner: Fighter) -> float | None:
        if name == "heroIdWithHungerCounter":
            holder = self.hunger_holder()
            return float(self.fighters.index(holder) + 1) if holder else 0.0
        return super().encounter_value(name, owner)

    def encounter_call(self, name: str, args: list[Any], resolver: Resolver) -> float | None:
        if name == "GetHungerCounterValueOnHero":
            index = int(resolver.argument(args[0])) - 1
            hero = self.fighters[index] if 0 <= index < len(self.fighters) else None
            counter = self.hunger_counter(hero) if hero else None
            return counter.value if counter else 0.0
        return super().encounter_call(name, args, resolver)

    def place_hunger(self, effect: dict[str, Any], context: sim_core.SkillContext, notes: list[str]) -> None:
        """Ravening's PlaceHungerCounter: a counter of the effect's value on the victim hunger_victim picks."""
        head = context.actor
        if not self.condition_holds(effect, context, None):
            return
        value = self.value(effect, context, None)
        if value is None:
            return
        first = not self.effect_uses.get((head.side, effect.get("id")))
        victim = self.hunger_victim(effect, first)
        if victim is None:
            return
        self.count_use(head, effect)
        victim.statuses.append(Status("HungerCounter", -1, head, False, float(int(value)), special=True))
        notes.append(f"Hunger counter {int(value)} on {victim.name}")

    def hunger_victim(self, effect: dict[str, Any], first: bool) -> Fighter | None:
        """PlaceHungerCounterProcessor's pick among the champions in the battle, the most recently spat out left out."""
        gap = int((effect.get("params") or {}).get("PlaceHungerCounterParams.IterationsBetweenDevouring", ASSUMED_DEVOUR_GAP))
        present = [f for f in self.fighters if f.side == "team" and f.alive]
        recent = sorted((f for f in present if f.name in self.spat_out), key=lambda f: -self.spat_out[f.name])[:gap]
        ready = [f for f in present if f not in recent] or recent[-1:]
        if len(ready) <= 1:
            return ready[0] if ready else None
        if first:
            top = max(f.stat("critical_damage") for f in ready)
            return self.rng.choice([f for f in ready if f.stat("critical_damage") >= top - 1e-9])
        weights = [max(HUNGER_BASE_WEIGHT + round((f.stat("critical_damage") * 100 - 50) * HUNGER_CRIT_DAMAGE_WEIGHT), 0)
                   for f in ready]
        return self.rng.choices(ready, weights=weights)[0] if sum(weights) > 0 else self.rng.choice(ready)

    def set_hunger(self, effect: dict[str, Any], context: sim_core.SkillContext) -> None:
        """Ravening's SetHungerCounter on its head's own turn: the counter's value less one, never below 0."""
        holder = self.hunger_holder()
        if holder is None or not self.condition_holds(effect, context, holder):
            return
        value = self.value(effect, context, holder)
        if value is None:
            return
        self.count_use(context.actor, effect)
        self.hunger_counter(holder).value = float(max(int(value), 0))

    def devour(self, effect: dict[str, Any], context: sim_core.SkillContext, notes: list[str]) -> None:
        """Feast's Devour on the champion the head chose: the first of Feast's Devour effects whose condition holds
        swallows it; the rest find the head digesting and do nothing."""
        head, victim = context.actor, context.chosen
        if victim is None or not victim.alive or self.digesting(head) or not self.condition_holds(effect, context, victim):
            return
        params = effect.get("params") or {}
        resolver = Resolver(self, head, victim, context)
        try:
            turns = int(formula.run(params["DevourParams.DigestionLifetimeFormula"], resolver))
            strength = float(formula.run(params["DevourParams.DigestionStrengthFormula"], resolver))
        except KeyError:
            self.sim.miss(head, "Feast: digestion formulas aren't in this knowledge file")
            return
        except formula.Unsupported as missing:
            self.sim.miss(head, f"Feast: digestion formula uses {missing.name}")
            return
        except formula.FormulaError:
            self.sim.miss(head, "Feast: digestion formula")
            return
        self.count_use(head, effect)
        victim.statuses = []
        victim.stamina = 0
        victim.alive = False
        self.extra_turns = [f for f in self.extra_turns if f is not victim]
        head.statuses.append(Status("Digestion", turns, head, False, strength, fresh=True, special=True))
        self.swallowed.append((head, victim))
        self.devours.append({"head": head.name, "champion": victim.name, "turn": self.turn_count, "outcome": "inside"})
        notes.append(f"devours {victim.name} (digestion {round(strength)} for {turns} turns)")

    def release(self, head: Fighter, outcome: str) -> Fighter | None:
        """Lets the head's champion go (spat out, or freed with its head), or kills it (digested)."""
        pair = next((p for p in self.swallowed if p[0] is head), None)
        if pair is None:
            return None
        self.swallowed.remove(pair)
        victim = pair[1]
        entry = next(d for d in reversed(self.devours) if d["champion"] == victim.name and d["outcome"] == "inside")
        entry["outcome"] = outcome
        entry["ended"] = self.turn_count
        if outcome == "digested":
            self.digested.add(victim.name)
            victim.alive = True  # die() below records the death
            self.die(victim, head)
            return victim
        victim.alive = True
        victim.stamina = 0
        if outcome == "spat out":
            self.spat_out[victim.name] = self.turn_count
        return victim

    def choose_skill(self, actor: Fighter) -> sim_core.Skill | None:
        feast = next((s for s in actor.skills if "Devour" in s.kinds()), None)
        if feast is not None and not self.digesting(actor) and not actor.has("Provoke") and not actor.has("BlockActiveSkills"):
            holder = self.hunger_holder()
            if holder is not None and self.hunger_counter(holder).value <= 0:
                self.feast_target = holder
                return feast
        return super().choose_skill(actor)

    def end_turn(self, actor: Fighter) -> None:
        digesting = self.digesting(actor)
        super().end_turn(actor)
        if digesting and not self.digesting(actor):
            self.release(actor, "digested")  # Digestion ran out of turns (cause Expired): the champion dies

    def effect_targets(self, effect: dict[str, Any], context: sim_core.SkillContext, allies: list[Fighter],
                       enemies: list[Fighter]) -> list[Fighter] | None:
        group = super().effect_targets(effect, context, allies, enemies)
        if group is not None and effect.get("target") in sim_core.DEAD_TARGETS:
            group = [g for g in group if not self.held(g)]
        return group

    def pick_target(self, actor: Fighter, skill: sim_core.Skill, allies: list[Fighter], enemies: list[Fighter]) -> Fighter | None:
        if "Devour" in skill.kinds():
            return self.feast_target
        if skill.targets and "Dead" in skill.targets:
            dead = [a for a in allies if not a.alive and not self.held(a)]
            return dead[0] if dead else None
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

    def receive(self, target: Fighter, dealer: Fighter | None, amount: float) -> float:
        """A digesting head's Digestion absorbs damage first (DigestionProcessor, an AbsorbDamageProcessor); once it is
        empty the champion inside is spat out (cause Destroyed). Absorbed damage takes no HP, so it doesn't score."""
        digestion = next((s for s in target.statuses if s.name == "Digestion"), None)
        if digestion is not None and amount > 0 and not target.has("BlockDamage"):
            absorbed = min(digestion.value, amount)
            digestion.value -= absorbed
            amount -= absorbed
            if dealer is not None and dealer.side == "team":
                self.absorbed += absorbed
            if digestion.value <= 0:
                target.statuses.remove(digestion)
                self.release(target, "spat out")
        return super().receive(target, dealer, amount)

    def reduce_hp(self, target: Fighter, dealer: Fighter | None, amount: float) -> float:
        dealt = super().reduce_hp(target, dealer, amount)
        if target.side == "enemy" and dealer is not None and dealer.side == "team":
            self.taken[target.name] = self.taken.get(target.name, 0.0) + dealt
        return dealt

    def die(self, target: Fighter, dealer: Fighter | None) -> None:
        if target.side != "enemy":
            super().die(target, dealer)
            return
        # The head's Digestion goes with it (cause RemovedByProcessor: no kill, no spit); its champion leaves only if the
        # Devoured status is linked to its producer.
        if self.sim.release_on_head_death:
            self.release(target, "freed")
        target.alive = False
        target.hp = 0.0
        target.statuses = []
        target.stamina = 0
        if target.name.endswith(" neck"):
            return
        self.decapitations.append({"head": target.name, "turn": self.turn_count, "by": dealer.name if dealer else None})
        variant = self.sim.neck_variant(target)
        if variant is not None:
            self.fighters.append(self.sim.neck_fighter(target, variant))

    def apply_effect(self, effect: dict[str, Any], context: sim_core.SkillContext, allies: list[Fighter],
                     enemies: list[Fighter], notes: list[str]) -> None:
        if effect["kind"] == "Transformation":
            return  # Shed Head: the neck replaces the head when it dies (die above)
        if effect["kind"] == "PlaceHungerCounter":
            self.place_hunger(effect, context, notes)
            return
        if effect["kind"] == "SetHungerCounter":
            self.set_hunger(effect, context)
            return
        if effect["kind"] == "Devour":
            self.devour(effect, context, notes)
            return
        if effect["kind"] != "GrowHydraHead":
            super().apply_effect(effect, context, allies, enemies, notes)
            return
        neck = context.actor
        grown = self.grow_head(neck, effect)
        if grown is not None:
            notes.append(f"{neck.name} grows {grown.name}")

    def grow_head(self, neck: Fighter, effect: dict[str, Any]) -> Fighter | None:
        """GrowHydraHeadProcessor: kill the neck, add a fresh head chosen by NewHeadSetup's odds."""
        standing = {f.type_id for f in self.fighters if f.side == "enemy" and f.alive and not f.name.endswith(" neck")}
        candidates = [t for t in self.sim.head_templates if t.type_id not in standing]
        if not candidates:
            return None
        own = next((t for t in candidates if t.type_id == neck.type_id), None)
        grow_self = float((effect.get("params") or {}).get("GrowHydraHeadParams.GrowSelfProbability", 1.0))
        others = [t for t in candidates if t is not own]
        weights = [grow_self if t is own else (1.0 - grow_self) / max(len(candidates) - 1, 1) for t in candidates]
        if own is None and others:
            weights = [1.0 / len(others)] * len(others)
        chosen = self.rng.choices(candidates, weights=weights)[0] if sum(weights) > 0 else candidates[0]
        neck.alive = False
        neck.hp = 0.0
        head = self.sim.fresh_copy([chosen])[0]
        self.fighters.append(head)
        for fighter in self.fighters:
            if fighter.alive:
                self.fire(fighter, {"HydraHeadGrown"}, [], kinds={"GrowHydraHead"}, other=head)
        return head


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
        "damage_by_head": {name: round(statistics.mean(r["by_head"].get(name, 0) for r in results))
                           for name in dict.fromkeys([h.name for h in heads] + [n for r in results for n in r["by_head"]])},
        "decapitations": {head: {"runs": len(turns), "mean_turn": round(statistics.mean(turns), 1)}
                          for head, turns in decapitations.items()},
        "devours": devour_summary(results, runs) if sim.devour else None,
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


def devour_summary(results: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    """How often heads swallowed champions per battle, how each swallow ended, and the damage digestion soaked up."""
    devours = [d for r in results for d in r["devours"]]
    outcomes = {name: round(sum(d["outcome"] == name for d in devours) / runs, 2)
                for name in ("spat out", "digested", "freed", "inside")}
    by_champion: dict[str, float] = {}
    for d in devours:
        by_champion[d["champion"]] = by_champion.get(d["champion"], 0) + 1 / runs
    return {
        "per_battle": round(len(devours) / runs, 2),
        "outcomes_per_battle": outcomes,
        "by_champion": {name: round(count, 2) for name, count in sorted(by_champion.items(), key=lambda p: -p[1])},
        "first_turn": round(statistics.mean(min(d["turn"] for d in r["devours"]) for r in results if r["devours"]), 1)
        if any(r["devours"] for r in results) else None,
        "damage_absorbed": round(statistics.mean(r["absorbed"] for r in results)),
    }


def hydra_assumptions(sim: HydraSimulator, assumed_limit: bool, limit: int, default_season: bool) -> list[str]:
    notes = [
        "Turn order, damage, DEF, hit types, resist chances, and the heads' own passives (AoE damage cut, MAX HP "
        "damage caps, immunities) follow the game's code and data (build 2026-10-19).",
        ("A decapitated head becomes its neck, which regrows a fresh head (its own, or another head not standing) "
         "with the game's odds when its grow skill comes off cooldown; the neck's stats from the variant are assumed."
         if sim.variants else
         "A decapitated head stays down for the rest of the battle: this knowledge file has no hero variants (necks), "
         "so long battles read low. Run refresh_knowledge."),
        devour_note(sim),
        "Champions attack the first standing head in the focus order (then the first standing head); a Provoked champion "
        "attacks its provoker. Heads pick a random champion and use their skills newest-first.",
        f"The battle ends after {limit} turns by anyone, when every head is down, or when the team is dead"
        + (" (turn limit assumed: this knowledge file has none for Hydra)." if assumed_limit else "."),
    ]
    if default_season:
        notes.append("Season: the knowledge file doesn't say which Hydra season is live, so the last one is used. "
                     "Pass season to pick another.")
    return notes + sim_core.assumptions(sim)[2:]


def devour_note(sim: HydraSimulator) -> str:
    if not sim.devour:
        return ("Feast never swallows anyone: this knowledge file has no digestion formulas (run refresh_knowledge). "
                "Wrath's hit counter isn't modelled yet.")
    release = ("A head that dies or turns into its neck frees the champion inside (assumed: this knowledge file doesn't "
               "say whether Devoured is linked to its head)." if sim.release_assumed else
               "A head that dies or turns into its neck frees the champion inside." if sim.release_on_head_death else
               "A head that dies or turns into its neck keeps its champion for the rest of the battle (Devoured isn't "
               "linked to its head).")
    return ("Feast follows the game's code: Ravening's Hunger counter (the champion with the most CRIT DMG first, then "
            "weighted by CRIT DMG) counts down on each Ravening head's turn, and a head that isn't digesting swallows "
            "the champion at 0. Digestion absorbs damage on the head (it doesn't score) until it breaks and the champion "
            "is spat out, or runs out of turns and the champion dies. " + release + " A swallowed or digested champion "
            "can't be revived (digested: assumed). Wrath's hit counter isn't modelled yet.")
