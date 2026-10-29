import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import inventory_tools  # noqa: E402
import set_tools  # noqa: E402
from artifacts_tools import TroglyError  # noqa: E402

IMMUNE = "Immune to [Stun], [Sleep], [Provoke] debuffs."
BORGOTH = "Decreases the damage received according to how much Borgoth's MAX HP has been destroyed by."
DEMON_LORD = "Immune to [Stun], [Freeze], [Sleep], and [Decrease SPD] debuffs, as well as MAX HP destruction effects."


def stage(stage_id, boss_type):
    return {"id": stage_id, "number": 1, "champion_slots": 5, "has_boss": True,
            "enemies": [{"wave": 1, "type_id": boss_type, "name": {9: "Borgoth", 8: "Fyro", 7: "Demon Lord"}[boss_type],
                         "stars": 6, "level": 300, "is_boss": True}]}


KNOWLEDGE = {
    "champion_types": [
        {"type_id": 1, "name": "Nuker", "role": "Attack", "skill_ids": [1]},
        {"type_id": 2, "name": "Healer", "role": "Support", "skill_ids": [2]},
        {"type_id": 7, "name": "Demon Lord", "role": "Attack", "skill_ids": [7]},
        {"type_id": 8, "name": "Fyro", "role": "Attack", "skill_ids": [8]},
        {"type_id": 9, "name": "Borgoth", "role": "Attack", "skill_ids": [9]},
    ],
    "skills": {
        "1": {"description": "Attacks 1 enemy.", "effects": [{"kind": "Damage", "multiplier": "4*ATK"}]},
        "2": {"description": "Heals all allies.", "effects": [{"kind": "Heal"}]},
        "7": {"description": DEMON_LORD, "effects": []},
        "8": {"description": IMMUNE, "effects": []},
        "9": {"description": BORGOTH, "effects": []},
    },
    "regions": [
        {"id": 208, "code": "FireGolemCave", "name": "Fire Golem Cave", "area": "Dungeon", "difficulties": {"Hard": [stage(2082001, 8)]}},
        {"id": 701, "code": "DoomTower1", "name": "DoomTower1", "area": "DoomTower", "difficulties": {"Hard": [stage(7011030, 9)]}},
        {"id": 401, "code": "AllianceBoss", "name": "AllianceBoss", "area": "AllianceBoss", "difficulties": {"None": [stage(401001, 7)]}},
    ],
    "artifact_sets": [
        {"id": 1, "code": "Hp", "name": "Life", "pieces": 2, "bonuses": [{"stat": "Health", "value": 0.15, "absolute": False}], "series": []},
        {"id": 7, "code": "Accuracy", "name": "Accuracy", "pieces": 2, "bonuses": [{"stat": "Accuracy", "value": 40, "absolute": True}], "series": []},
        {"id": 20, "code": "DecreaseMaxHp", "name": "Destroy", "pieces": 4, "bonuses": [], "series": [], "has_effect": True},
        {"id": 21, "code": "StunChance", "name": "Stun", "pieces": 4, "bonuses": [], "series": [], "has_effect": True},
        {"id": 48, "code": "StoneSkinHpResDef", "name": "Stone Skin", "pieces": 0, "has_effect": True, "bonuses": [],
         "series": [{"pieces": 4, "bonuses": [{"stat": "Health", "value": 0.16, "absolute": False},
                                               {"stat": "Resistance", "value": 80, "absolute": True}]}]},
    ],
}


def piece(artifact_id, set_code, set_name):
    return {"id": artifact_id, "set": set_code, "set_name": set_name, "slot": "Helmet", "rank": 6, "rarity": 5, "level": 16,
            "primary_stat": {"stat": "Health", "value": 2840, "is_absolute": True},
            "substats": [{"stat": "Speed", "value": 15, "is_absolute": True, "rolls": 2},
                         {"stat": "Health", "value": 0.1, "is_absolute": False, "rolls": 1}]}


STATE = {
    "champions": [{"id": "10", "name": "Nuker", "type_id": 1, "equipped_artifact_ids": {}},
                  {"id": "11", "name": "Healer", "type_id": 2, "equipped_artifact_ids": {}}],
    "artifacts": [piece("life", "Hp", "Life"), piece("stone", "StoneSkinHpResDef", "Stone Skin")],
}


@pytest.fixture
def home(tmp_path):
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(STATE), encoding="utf-8")
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(KNOWLEDGE), encoding="utf-8")
    return tmp_path


def value(result, set_name):
    return next(row["value"] for row in result["best_sets"] if row["set"] == set_name)


def test_same_stats_on_a_better_set_score_higher(home):
    weights = inventory_tools._set_weights(home)
    life = inventory_tools._scored(piece("a", "Hp", "Life"), weights)["best_score"]
    stone = inventory_tools._scored(piece("b", "StoneSkinHpResDef", "Stone Skin"), weights)["best_score"]
    assert stone > life


def test_accuracy_set_does_not_fit_a_champion_that_places_no_debuffs(home):
    result = set_tools.set_advice("Nuker", top=100, home=home)
    accuracy = next(row for row in result["best_sets"] if row["set"] == "Accuracy")
    assert accuracy["fit"] == 0 and "ACC doesn't help Nuker" in accuracy["notes"]


def test_on_hit_sets_need_a_champion_that_deals_damage(home):
    assert value(set_tools.set_advice("Healer", top=100, home=home), "Stun") == 0


def test_boss_immunity_zeroes_the_set(home):
    result = set_tools.set_advice("Nuker", "stage:2082001", top=100, home=home)
    assert "Stun" in result["useless_here"]
    assert "Destroy" not in result["useless_here"]


def test_clan_boss_is_immune_to_max_hp_destruction(home):
    assert "Destroy" in set_tools.set_advice("Nuker", "clan_boss:ultra_nightmare", top=100, home=home)["useless_here"]


@pytest.mark.parametrize("encounter", ["stage:7011030", "Borgoth", "boss:borgoth"])
def test_borgoth_puts_destroy_on_top(home, encounter):
    result = set_tools.set_advice("Nuker", encounter, top=100, home=home)
    assert result["best_sets"][0]["set"] == "Destroy"
    assert result["best_sets"][0]["value"] == pytest.approx(set_tools.MECHANIC_VALUE)


def test_unknown_encounter_is_an_error_not_silence(home):
    with pytest.raises(TroglyError, match="not an encounter or a boss"):
        set_tools.set_advice("Nuker", "Nobody", home=home)


def test_player_tier_and_rules_override_defaults(home):
    set_tools.set_preferences("set_tier", "Life", tier="S+", home=home)
    assert value(set_tools.set_advice(top=100, home=home), "Life") == 1.0
    set_tools.set_preferences("add_rule", "Stun", encounter="arena", multiplier=2, note="arena stun bait", home=home)
    stun = next(r for r in set_tools.set_advice("Nuker", "arena:classic", top=100, home=home)["best_sets"] if r["set"] == "Stun")
    assert stun["encounter_multiplier"] == 2 and stun["notes"] == "arena stun bait"
    shown = set_tools.set_preferences("gear_stage", gear_stage="late", home=home)
    assert shown["gear_stage"] == "late" and "Life (yours)" in shown["tiers"]["S+"]


def test_bad_preferences_are_rejected(home):
    with pytest.raises(TroglyError, match="Tier must be"):
        set_tools.set_preferences("set_tier", "Life", tier="SSS", home=home)
    with pytest.raises(TroglyError, match="No artifact set"):
        set_tools.set_preferences("set_tier", "Banana", tier="S", home=home)
