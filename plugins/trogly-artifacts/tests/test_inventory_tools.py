import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import inventory_tools  # noqa: E402
from artifacts_tools import TroglyError  # noqa: E402


def bonus(stat, value, absolute=True, rolls=0, glyph=0):
    return {"stat": stat, "value": value, "is_absolute": absolute, "rolls": rolls, "glyph": glyph}


def piece(artifact_id, slot, main, subs, rank=6, rarity=5, level=16, set_name="Speed"):
    return {"id": artifact_id, "set": set_name, "set_name": set_name, "slot": slot, "rank": rank, "rarity": rarity,
            "level": level, "primary_stat": main, "substats": subs}


GOOD_BOOTS = piece("good", "Boots", bonus("Speed", 45), [
    bonus("CriticalChance", 0.15, False, rolls=2), bonus("CriticalDamage", 0.12, False, rolls=1),
    bonus("Attack", 0.10, False, rolls=1), bonus("Defence", 20)])
FLAT_GLOVES = piece("flat", "Gloves", bonus("Attack", 80), [
    bonus("Speed", 15, rolls=2), bonus("CriticalChance", 0.10, False, rolls=2), bonus("Health", 300), bonus("Defence", 20)])
WEAK_HELMET = piece("weak", "Helmet", bonus("Health", 2840), [
    bonus("Attack", 30, rolls=3), bonus("Defence", 40, rolls=1), bonus("Health", 600), bonus("Resistance", 10)],
    rank=5, set_name="Lifesteal")
PROMISING = piece("promising", "Shield", bonus("Defence", 35), [
    bonus("Defence", 0.05, False), bonus("Resistance", 9), bonus("Health", 0.05, False), bonus("Speed", 5)],
    level=0, set_name="Immortal")
WORN = piece("worn", "Weapon", bonus("Attack", 265), [bonus("Health", 200)], set_name="Lifesteal")

STATE = {
    "champions": [{"id": "1", "name": "Thor Faehammer", "equipped_artifact_ids": {"Weapon": "worn"}}],
    "artifacts": [GOOD_BOOTS, FLAT_GLOVES, WEAK_HELMET, PROMISING, WORN],
}


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "data" / "state" / "trogly-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(STATE), encoding="utf-8")
    return tmp_path


def test_hit_odds_match_hand_worked_epic():
    # Epic with 1 useful substat of 3: three rolls at 1/3, then a fourth substat with 2 useful of 7 left.
    odds = inventory_tools._hit_distribution(3, 1, 7, 2, 4)
    assert sum(odds.values()) == pytest.approx(1)
    assert odds[4] == pytest.approx((1 / 3) ** 3 * 2 / 7)


def test_missing_substats_arrive_with_the_last_upgrades():
    # A Rare at level 8 (two substats, two upgrades left) gains its third and fourth substats, never a roll.
    odds = inventory_tools._hit_distribution(2, 2, 9, 0, 2)
    assert odds == {0: 1.0}


def test_flat_main_on_gloves_suits_no_build():
    assert inventory_tools._scored(FLAT_GLOVES)["best_profile"] is None


def test_sell_list_skips_worn_and_protected_and_explains(home):
    result = inventory_tools.sell_candidates(protected_artifact_ids=["weak"], keep_per_slot=0, keep_per_set=0,
                                             home=home)
    assert result["vault_pieces"] == 4
    assert "worn" not in result["sell_ids"] and "weak" not in result["sell_ids"]
    flat = next(row for row in result["sell_list"] if row["id"] == "flat")
    assert "suits no build" in flat["why"]
    assert [row["id"] for row in result["upgrade_first_list"]] == ["promising"]


def test_top_pieces_per_slot_are_kept(home):
    result = inventory_tools.sell_candidates(keep_per_slot=1, keep_per_set=0, home=home)
    assert "good" not in result["sell_ids"]
    assert "flat" in result["sell_ids"]


def test_upgrade_candidates_for_wanted_stats(home):
    result = inventory_tools.upgrade_candidates(stats=["HP%", "SPD", "DEF%", "RES"], sets=["immortal"], home=home)
    (candidate,) = result["candidates"]
    assert (candidate["id"], candidate["hits_now"], candidate["upgrades_left"]) == ("promising", 4, 4)
    assert candidate["expected_hits"] == 8
    assert candidate["chance_of_min_hits"] == "100%"


def test_upgrade_candidates_reject_unknown_stats(home):
    with pytest.raises(TroglyError, match="not a substat"):
        inventory_tools.upgrade_candidates(stats=["Luck"], home=home)
