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
