import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import trogly_state  # noqa: E402
from trogly_state import AccountState, TroglyError  # noqa: E402

STATE = {
    "last_updated": "2026-10-16T19:11:45.7688141+00:00",
    "extraction": {"source": "direct-memory"},
    "account": {"level": 83, "total_power": 4271538.4, "resources": {"Energy": 43.6, "Gem": 1622}, "shards": {"Void": 12}},
    "great_hall": {"Spirit": {"Attack": 4}},
    "champions": [
        {
            "id": "16109", "name": "Thor Faehammer", "level": 60, "stars": 6, "rarity": 5,
            "equipped_artifact_ids": {"Boots": "2", "Weapon": "1"}, "masteries": ["500113"],
            "awakening_level": 1, "empower_level": 0, "blessing_id": "5201",
            "calculated_stats": {
                "base": {"health": 15690, "speed": 105, "critical_chance": 0.15},
                "total": {"health": 45859, "attack": 4940, "speed": 125, "critical_chance": 1.26, "critical_damage": 1.79},
                "verified": False, "warnings": [],
            },
        },
        {"id": "7", "name": "Kael", "level": 50, "stars": 5, "rarity": 3, "equipped_artifact_ids": {},
         "calculated_stats": {"total": {"speed": 140}}},
        {"id": "8", "name": "Kael", "level": 30, "stars": 3, "rarity": 3, "equipped_artifact_ids": {}},
    ],
    "artifacts": [
        {"id": "1", "set": "IgnoreDefense", "slot": "Weapon", "rarity": 5, "rank": 6, "level": 16,
         "primary_stat": {"stat": "Attack", "value": 265, "is_absolute": True},
         "substats": [{"stat": "CriticalChance", "value": 0.12, "is_absolute": False, "glyph": 0.01}]},
        {"id": "2", "set": "AttackSpeed", "slot": "Boots", "rarity": 4, "rank": 6, "level": 16,
         "primary_stat": {"stat": "Speed", "value": 45, "is_absolute": True}, "substats": []},
        {"id": "3", "set": "AttackSpeed", "slot": "Boots", "rarity": 3, "rank": 5, "level": 12,
         "primary_stat": {"stat": "Speed", "value": 35, "is_absolute": True}, "substats": []},
        {"id": "4", "set": "HpResSpdChronoBuffScalingWithStacks", "slot": "Chest", "rarity": 3, "rank": 5, "level": 8,
         "primary_stat": {"stat": "Attack", "value": 0.25, "is_absolute": False}, "substats": []},
    ],
}


@pytest.fixture
def home(tmp_path):
    path = trogly_state.state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(STATE), encoding="utf-8")
    return tmp_path


def test_missing_state_explains_how_to_fix(tmp_path):
    with pytest.raises(TroglyError, match="refresh_account"):
        AccountState.load(tmp_path)


def test_snapshot_flags_old_data(home):
    state = AccountState.load(home)
    info = state.snapshot(now=datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc))
    assert info["age_minutes"] == 108
    assert "refresh" in info["warning"]


def test_account_summary_counts_unequipped_artifacts(home):
    summary = AccountState.load(home).account_summary()
    assert summary["champions"] == 3
    assert summary["unequipped_artifacts"] == 2
    assert summary["resources"] == {"Energy": 44, "Gem": 1622}


def test_get_champion_returns_gear_in_slot_order_with_set_names(home):
    thor = AccountState.load(home).get_champion("thor faehammer")
    assert [piece["slot"] for piece in thor["gear"]] == ["Weapon", "Boots"]
    assert thor["gear"][0]["set"] == "Savage"
    assert thor["gear"][0]["substats"] == ["C.RATE 12% (+1% glyph)"]
    assert thor["stats"]["C.RATE"] == "126%"
    assert thor["set_pieces"] == {"Savage": 1, "Speed": 1}


def test_get_champion_with_two_copies_asks_which(home):
    result = AccountState.load(home).get_champion("Kael")
    assert result["ambiguous"] is True
    assert [c["id"] for c in result["candidates"]] == ["7", "8"]


def test_get_champion_unknown_name_points_to_search(home):
    with pytest.raises(TroglyError, match="find_champions"):
        AccountState.load(home).get_champion("Arbiter")


def test_find_champions_sorts_by_calculated_speed(home):
    result = AccountState.load(home).find_champions(sort_by="speed")
    assert [c["id"] for c in result["champions"]] == ["7", "16109", "8"]


def test_find_artifacts_filters_unequipped_speed_boots(home):
    result = AccountState.load(home).find_artifacts(set_code="speed", slot="boots", equipped="no")
    assert [a["id"] for a in result["artifacts"]] == ["3"]
    assert result["artifacts"][0]["equipped_on"] is None


def test_find_artifacts_percent_main_stat(home):
    result = AccountState.load(home).find_artifacts(main_stat="ATK%")
    assert [a["id"] for a in result["artifacts"]] == ["4"]


def test_unknown_set_code_is_split_into_words():
    assert trogly_state.set_name("HpResSpdChronoBuffScalingWithStacks") == "Hp Res Spd Chrono Buff Scaling With Stacks"


def test_refresh_without_agent_path_explains_setting(home):
    with pytest.raises(TroglyError, match="agent_path"):
        trogly_state.refresh_account("", home)
