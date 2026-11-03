import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import battle_sim  # noqa: E402
import hydra_sim  # noqa: E402
from knowledge_tools import Knowledge  # noqa: E402
from test_battle_sim import KNOWLEDGE, STATE, champion_type, effect, skill  # noqa: E402

HEAD_STATS = {"health": 60_000, "attack": 500, "defence": 300, "speed": 90, "resistance": 50, "accuracy": 50,
              "critical_chance": 0.0, "critical_damage": 0.5}


def head(type_id, name):
    return {"wave": 1, "type_id": type_id, "name": name, "stars": 6, "level": 260, "is_boss": False, "stats": HEAD_STATS}


def hydra_knowledge():
    data = json.loads(json.dumps(KNOWLEDGE))
    data["champion_types"] += [
        champion_type(26070, "Head of Decay", [2601, 2602, 2603], affinity="Spirit"),
        champion_type(26090, "Head of Torment", [2601], affinity="Force"),
    ]
    data["skills"].update({
        "2601": skill("Jaws", effects=[effect("Damage", multiplier="0.5*ATK")]),
        "2602": skill("Tide of Hope [P]", targets=None, effects=[{
            **effect("Heal", "AllEnemies", "0.3*TRG_HP", condition="CUR_HP==0&&canUniqueApplyForTurn"), "passive": True,
            "relation": {"effect_id": None, "kinds": [], "phases": ["AfterDamageDealt"]}}]),
        "2603": skill("Feast", effects=[effect("Devour", "AllEnemies")], targets="AliveEnemiesIncludeInvisible"),
    })
    stages = [{"id": 8069001 + n, "number": n + 1, "champion_slots": 6, "has_boss": True, "modifiers": [],
               "enemies": [head(26070, "Head of Decay"), head(26090, "Head of Torment")]} for n in range(4)]
    data["regions"].append({"id": 806, "code": "AllianceHydraSeason6", "name": "HydraSeason6", "area": "Hydra",
                            "difficulties": {"None": stages}})
    return data


@pytest.fixture
def knowledge(tmp_path):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(hydra_knowledge()), encoding="utf-8")
    return Knowledge.load(tmp_path)


def team(*names):
    return [{"champion": name} for name in names]


def test_champions_hit_the_first_standing_head_in_the_focus_order(knowledge):
    poisoner = [{"champion": "Poisoner", "priority": ["A1"]}]
    result = hydra_sim.simulate_hydra(knowledge, STATE, poisoner, "Normal", focus=["Head of Torment"], runs=1, turn_limit=4)
    first = next(entry for entry in result["timeline"] if entry["who"] == "Poisoner")
    assert any("Head of Torment" in event for event in first["events"])
    assert not any("Head of Decay" in event for event in first["events"])


def test_a_decapitated_head_runs_its_death_passive_then_stays_down(knowledge):
    sim = hydra_sim.HydraSimulator(knowledge, STATE)
    heads = sim.head_fighters(sim.hydra_stage("Normal", None)["stage"])
    hero, decay, torment = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + heads)
    battle = hydra_sim.HydraBattle(sim, [hero, decay, torment], 1, record=False, focus=["Head of Decay"])
    hero.hp = hero.max_hp / 2
    decay.hp = 1
    battle.turn_owner = hero
    context = battle_sim.SkillContext(hero, hero.skills[0], decay)
    battle.apply_effect(hero.skills[0].effects[0], context, [hero], [decay, torment], [])
    assert hero.hp == pytest.approx(hero.max_hp / 2 + 0.3 * hero.max_hp)
    assert not decay.alive
    assert [cut["head"] for cut in battle.decapitations] == ["Head of Decay"]
    assert battle.pick_target(hero, hero.skills[0], [hero], [decay, torment]) is torment


def test_heads_leave_out_feast_and_say_so(knowledge):
    result = hydra_sim.simulate_hydra(knowledge, STATE, team("Poisoner", "Medic", "Tank"), "Nightmare", runs=1, turn_limit=6)
    assert any(entry["skill"].endswith("Feast") for entry in result["timeline"]) is False
    assert any(item.startswith("Feast") for item in result["not_simulated"]["Head of Decay"])
    assert result["encounter"] == "Hydra Season6 Nightmare"
    assert hydra_sim.HydraSimulator(knowledge, STATE).turn_limit() == (hydra_sim.ASSUMED_TURN_LIMIT, True)


def test_hydra_takes_six_champions_and_checks_the_focus_names(knowledge):
    sim = hydra_sim.HydraSimulator(knowledge, STATE)
    with pytest.raises(battle_sim.BattleError, match="at most 6"):
        sim.team_fighters(team(*["Poisoner"] * 7), max_size=hydra_sim.HYDRA_TEAM_SIZE)
    with pytest.raises(battle_sim.BattleError, match="No head called Head of Wrath"):
        hydra_sim.simulate_hydra(knowledge, STATE, team("Poisoner"), focus=["Head of Wrath"], runs=1)
    with pytest.raises(battle_sim.BattleError, match="Unknown Hydra difficulty"):
        hydra_sim.simulate_hydra(knowledge, STATE, team("Poisoner"), "UltraNightmare", runs=1)


def test_the_turn_limit_comes_from_the_knowledge_file_when_it_has_one(knowledge):
    knowledge.raw["battle"]["turn_limits"] = {"Default": 500, "Hydra": 7}
    result = hydra_sim.simulate_hydra(knowledge, STATE, team("Tank"), runs=1)
    assert result["turn_limit"] == 7
    assert result["turns"]["max"] <= 7
    assert not any("turn limit assumed" in note for note in result["assumed"])


def with_necks(knowledge, grow_self):
    knowledge.raw["skills"]["2604"] = skill("Shed Head [P]", targets=None, effects=[{
        **effect("Transformation", "Owner", condition="TRG_CUR_HP==0&&ownerIsRelatedEffectTarget"), "passive": True,
        "params": {"TransformationParams.VariantId": "7"},
        "relation": {"effect_id": None, "kinds": [], "phases": ["AfterDamageDealt"]}}])
    knowledge.raw["skills"]["2605"] = skill("Grow Head", 2, [{
        **effect("GrowHydraHead", "Owner"), "params": {"GrowHydraHeadParams.GrowSelfProbability": str(grow_self)}}],
        targets="Producer")
    for type_ in knowledge.raw["champion_types"]:
        if type_["type_id"] in (26070, 26090):
            type_["skill_ids"].append(2604)
    knowledge.raw["hero_variants"] = [{"id": 7, "type": "HydraNeck", "base_stats": None,
                                       "stats_multiplier": {"Health": 0.5}, "skill_ids": [2605]}]


def test_a_decapitated_head_becomes_a_neck_that_regrows_a_head(knowledge):
    with_necks(knowledge, grow_self=1)
    sim = hydra_sim.HydraSimulator(knowledge, STATE)
    heads = sim.head_fighters(sim.hydra_stage("Normal", None)["stage"])
    hero, decay, torment = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + heads)
    battle = hydra_sim.HydraBattle(sim, [hero, decay, torment], 1, record=False, focus=[])
    decay.hp = 1
    context = battle_sim.SkillContext(hero, hero.skills[0], decay)
    battle.apply_effect(hero.skills[0].effects[0], context, [hero], [decay, torment], [])
    neck = battle.fighters[-1]
    assert neck.name == "Head of Decay neck" and neck.alive
    assert neck.max_hp == pytest.approx(decay.max_hp * 0.5)
    neck_context = battle_sim.SkillContext(neck, neck.skills[0], neck)
    battle.apply_effect(neck.skills[0].effects[0], neck_context, [neck], [hero], notes := [])
    regrown = battle.fighters[-1]
    assert (regrown.name, regrown.alive, regrown.hp) == ("Head of Decay", True, regrown.max_hp)
    assert not neck.alive
    assert notes == ["Head of Decay neck grows Head of Decay"]


def test_a_neck_regrows_only_a_head_that_is_not_standing(knowledge):
    with_necks(knowledge, grow_self=0)
    sim = hydra_sim.HydraSimulator(knowledge, STATE)
    heads = sim.head_fighters(sim.hydra_stage("Normal", None)["stage"])
    hero, decay, torment = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + heads)
    battle = hydra_sim.HydraBattle(sim, [hero, decay, torment], 1, record=False, focus=[])
    neck = sim.neck_fighter(decay, sim.variants[7])
    decay.alive = False
    battle.fighters.append(neck)
    # Torment stands, so Decay is the only head the neck can grow, whatever GrowSelfProbability says.
    assert battle.grow_head(neck, neck.skills[0].effects[0]).name == "Head of Decay"


def with_devour(knowledge, linked=None):
    """Decay gets Ravening (a Hunger counter of 3, one lower on its turns) and a Feast whose Devour has its digestion
    formulas: 1000 damage absorbed, 2 turns."""
    knowledge.raw["skills"]["2606"] = skill("Ravening [P]", targets=None, effects=[
        {**effect("PlaceHungerCounter", "Owner", "3", condition="EnemyTeamAppliedEffectsTotalCountOfKind(HungerCounter_KindId)==0"),
         "id": 2600061, "passive": True, "params": {"PlaceHungerCounterParams.IterationsBetweenDevouring": "1"},
         "relation": {"effect_id": None, "kinds": [], "phases": ["BeforeTurnStarted"]}},
        {**effect("SetHungerCounter", "AllEnemies", "GetHungerCounterValueOnHero(heroIdWithHungerCounter)-1",
                  condition="isOwnersTurn"), "id": 2600062, "passive": True,
         "relation": {"effect_id": None, "kinds": [], "phases": ["AfterTurnStarted"]}},
    ])
    knowledge.raw["skills"]["2603"]["effects"] = [{
        **effect("Devour", "AllEnemies", condition="!OwnerHasEffectOfKind(Digestion_KindId)&&ownersLevel==260"),
        "id": 2600075, "params": {"DevourParams.DigestionStrengthFormula": "1000",
                                  "DevourParams.DigestionLifetimeFormula": "2"}}]
    decay = next(t for t in knowledge.raw["champion_types"] if t["type_id"] == 26070)
    decay["skill_ids"].append(2606)
    if linked is not None:
        knowledge.raw.setdefault("status_effects", {})["Devoured"] = {"id": 690, "effect": {
            "kind": "Devoured", "params": {"StatusEffectParams.UnapplyWhenProducerDied": linked}}}


def devour_battle(knowledge, linked=None):
    with_devour(knowledge, linked)
    sim = hydra_sim.HydraSimulator(knowledge, STATE)
    heads = sim.head_fighters(sim.hydra_stage("Normal", None)["stage"])
    fighters = sim.fresh_copy(sim.team_fighters(team("Poisoner", "Medic", "Tank")) + heads)
    battle = hydra_sim.HydraBattle(sim, fighters, 1, record=False, focus=[])
    return sim, battle, fighters


def swallow(battle, decay, victim):
    battle.hunger_counter(victim).value = 0
    feast = battle.choose_skill(decay)
    assert feast.name == "Feast" and battle.feast_target is victim
    battle.use_skill(decay, feast, notes := [])
    return notes


def test_the_first_hunger_counter_goes_to_the_most_crit_damage_and_counts_down_on_the_heads_turn(knowledge):
    sim, battle, (poisoner, medic, tank, decay, torment) = devour_battle(knowledge)
    medic.stats["critical_damage"] = 2.5
    battle.turn_owner = decay
    battle.fire(decay, battle_sim.TURN_START_PHASES, [])
    assert battle.hunger_counter(medic).value == 2  # placed at 3, then one lower on Decay's own turn
    assert battle.hunger_counter(medic).special and medic.count_of(False) == 0
    used = battle_sim.Resolver(battle, torment, None, None).call("EffectUsedByAllyTeamCountWholeBattleWithId", [2600061.0])
    assert used == 1  # counted for the heads' whole team, whichever head placed it
    battle.turn_owner = torment
    battle.fire(decay, battle_sim.TURN_START_PHASES, [])
    assert battle.hunger_counter(medic).value == 2  # not Decay's turn, and Medic already has the counter
    assert sum(1 for f in (poisoner, medic, tank) if battle.hunger_counter(f)) == 1
    assert battle.choose_skill(decay).name != "Feast"


def test_feast_swallows_the_champion_at_0_and_digestion_absorbs_damage_until_it_spits_it_out(knowledge):
    sim, battle, (poisoner, medic, tank, decay, torment) = devour_battle(knowledge)
    medic.statuses.append(battle_sim.Status("HungerCounter", -1, decay, False, 0.0, special=True))
    medic.statuses.append(battle_sim.Status("IncreaseAttack25", 2, medic, True))
    swallow(battle, decay, medic)
    assert not medic.alive and medic.statuses == [] and battle.held(medic)
    assert battle.digesting(decay) and battle.choose_skill(decay).name != "Feast"
    dealt = battle.receive(decay, poisoner, 1500)
    assert dealt == pytest.approx(500) and battle.absorbed == pytest.approx(1000)
    assert medic.alive and not battle.digesting(decay) and battle.spat_out == {"Medic": 0}
    assert battle.devours[0]["outcome"] == "spat out"


def test_digestion_running_out_kills_the_champion_for_good(knowledge):
    sim, battle, (poisoner, medic, tank, decay, torment) = devour_battle(knowledge)
    tank.statuses.append(battle_sim.Status("HungerCounter", -1, decay, False, 0.0, special=True))
    swallow(battle, decay, tank)
    battle.end_turn(decay)  # placed this turn on itself: doesn't count down yet
    battle.end_turn(decay)
    assert battle.digesting(decay)
    battle.end_turn(decay)
    assert not tank.alive and battle.devours[0]["outcome"] == "digested"
    assert [d["champion"] for d in battle.deaths] == ["Tank"]
    revive = effect("Revive", "AllDeadAllies", "0.5")
    context = battle_sim.SkillContext(medic, medic.skills[0], None)
    assert battle.effect_targets(revive, context, [poisoner, medic, tank], [decay, torment]) == []


def test_a_head_that_dies_frees_its_champion_unless_devoured_isnt_linked(knowledge):
    sim, battle, (poisoner, medic, tank, decay, torment) = devour_battle(knowledge)
    poisoner.statuses.append(battle_sim.Status("HungerCounter", -1, decay, False, 0.0, special=True))
    swallow(battle, decay, poisoner)
    battle.die(decay, medic)
    assert poisoner.alive and battle.devours[0]["outcome"] == "freed" and sim.release_assumed


def test_an_unlinked_devoured_status_keeps_the_champion_inside(tmp_path):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(hydra_knowledge()), encoding="utf-8")
    sim, battle, (poisoner, medic, tank, decay, torment) = devour_battle(Knowledge.load(tmp_path), linked="false")
    poisoner.statuses.append(battle_sim.Status("HungerCounter", -1, decay, False, 0.0, special=True))
    swallow(battle, decay, poisoner)
    battle.die(decay, medic)
    assert not poisoner.alive and battle.held(poisoner) and not sim.release_on_head_death


def test_later_hunger_counters_skip_the_champion_spat_out_last(knowledge):
    sim, battle, (poisoner, medic, tank, decay, torment) = devour_battle(knowledge)
    place = next(p for p in decay.passives if p["kind"] == "PlaceHungerCounter")
    battle.spat_out = {"Poisoner": 3, "Medic": 9}
    picks = {battle.hunger_victim(place, first=False).name for _ in range(40)}
    assert picks == {"Poisoner", "Tank"}
    poisoner.alive = tank.alive = False
    assert battle.hunger_victim(place, first=False) is medic  # nobody else left: the one spat out goes back in


def test_a_hydra_battle_with_devour_reports_swallows(knowledge):
    with_devour(knowledge)
    result = hydra_sim.simulate_hydra(knowledge, STATE, team("Tank"), "Normal", runs=3, turn_limit=60)
    assert result["devours"]["per_battle"] > 0
    assert not any(item.startswith("Feast") for item in result["not_simulated"].get("Head of Decay", []))
    assert any("Hunger counter" in note for note in result["assumed"])


def heads_and_battle(knowledge):
    sim = hydra_sim.HydraSimulator(knowledge, STATE)
    heads = sim.head_fighters(sim.hydra_stage("Normal", None)["stage"])
    fighters = sim.fresh_copy(sim.team_fighters(team("Tank")) + heads)
    return fighters, hydra_sim.HydraBattle(sim, fighters, 1, record=False, focus=[])


def test_each_head_runs_its_own_brain_by_type_id(knowledge):
    fighters, _ = heads_and_battle(knowledge)
    brains = {f.name: hydra_sim.head_brain(f) for f in fighters}
    assert brains == {"Tank": None, "Head of Decay": "Heal", "Head of Torment": "Ghost"}


def test_mischief_steals_only_with_a_buff_other_than_share_damage(knowledge):
    fighters, _ = heads_and_battle(knowledge)
    head = fighters[1]
    assert not hydra_sim.HydraBattle.thief_may_steal(head)
    head.statuses.append(battle_sim.Status("ShareDamage25", 2, head, True))
    assert not hydra_sim.HydraBattle.thief_may_steal(head)
    head.statuses.append(battle_sim.Status("IncreaseAttack25", 2, head, True))
    assert hydra_sim.HydraBattle.thief_may_steal(head)


def test_decays_third_skill_goes_to_the_most_injured_head(knowledge):
    fighters, battle = heads_and_battle(knowledge)
    champion, decay, torment = fighters
    decay.hp = decay.max_hp * 0.9
    torment.hp = torment.max_hp * 0.4
    ward = battle_sim.Skill("A3", "Rotten Ward", 2604, 5, 0, "AliveAllies", [effect("ApplyBuff", "Target")])
    assert battle.pick_target(decay, ward, [decay, torment], [champion]) is torment
