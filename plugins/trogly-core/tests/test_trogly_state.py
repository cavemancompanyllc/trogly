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
                "sets": [
                    {"set": "IgnoreDefense", "name": "Savage", "pieces": 1, "active": False, "active_level": 0,
                     "next_bonus_at": 4, "series": False, "bonus": "4 Set: ...", "active_stats": [], "skill_effect": False},
                    {"set": "IncreaseAccuracyAndSpeedWithInterceptBuff", "name": "Pinpoint", "pieces": 1, "active": True,
                     "active_level": 1, "next_bonus_at": 2, "series": True, "bonus": "Variable Set...",
                     "active_stats": ["Accuracy +20"], "skill_effect": False},
                ],
            },
        },
        {"id": "7", "name": "Kael", "level": 50, "stars": 5, "rarity": 3, "equipped_artifact_ids": {},
         "calculated_stats": {"total": {"speed": 140}}},
        {"id": "8", "name": "Kael", "level": 30, "stars": 3, "rarity": 3, "equipped_artifact_ids": {}},
    ],
    "artifacts": [
        {"id": "1", "set": "IgnoreDefense", "set_name": "Savage", "slot": "Weapon", "rarity": 5, "rank": 6, "level": 16,
         "primary_stat": {"stat": "Attack", "value": 265, "is_absolute": True},
         "substats": [{"stat": "CriticalChance", "value": 0.12, "is_absolute": False, "glyph": 0.01}]},
        {"id": "2", "set": "AttackSpeed", "set_name": "Speed", "slot": "Boots", "rarity": 4, "rank": 6, "level": 16,
         "primary_stat": {"stat": "Speed", "value": 45, "is_absolute": True}, "substats": []},
        {"id": "3", "set": "AttackSpeed", "set_name": "Speed", "slot": "Boots", "rarity": 3, "rank": 5, "level": 12,
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
    savage, pinpoint = thor["sets"]
    assert savage == {"set": "Savage", "pieces": 1, "active": False, "active_bonus_stats": [],
                      "skill_effect_active": False, "description": "4 Set: ...", "next_bonus_at_pieces": 4}
    assert pinpoint["active_bonus_stats"] == ["Accuracy +20"]
    assert pinpoint["kind"].startswith("variable set")


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


def test_set_name_prefers_the_game_name_and_falls_back_to_the_code():
    assert trogly_state.set_name("HpResSpdChronoBuffScalingWithStacks", "Chronophage") == "Chronophage"
    assert trogly_state.set_name("HpResSpdChronoBuffScalingWithStacks") == "Hp Res Spd Chrono Buff Scaling With Stacks"


def test_refresh_without_agent_path_explains_setting(home):
    with pytest.raises(TroglyError, match="set_agent_path"):
        trogly_state.refresh_account("", home)


def test_set_agent_path_accepts_the_folder_and_saves_the_exe(tmp_path, monkeypatch):
    monkeypatch.delenv("TROGLY_AGENT_PATH", raising=False)
    folder = tmp_path / "agent"
    folder.mkdir()
    (folder / "Trogly.Agent.exe").write_bytes(b"")
    saved = trogly_state.set_agent_path(f'"{folder}"', tmp_path)
    assert saved["agent_path"].endswith("Trogly.Agent.exe")
    assert trogly_state.configured_agent_path(tmp_path) == saved["agent_path"]


def test_set_agent_path_rejects_a_missing_file(tmp_path):
    with pytest.raises(TroglyError, match="no Trogly Agent"):
        trogly_state.set_agent_path(str(tmp_path / "nowhere"), tmp_path)


class FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def fake_agent(tmp_path, monkeypatch, payload):
    agent = tmp_path / "Trogly.Agent.exe"
    agent.write_bytes(b"")
    calls = []

    def run(command, **_):
        calls.append(command)
        calls.append(json.loads(Path(command[-1]).read_text(encoding="utf-8")))
        return FakeCompleted(json.dumps(payload))

    monkeypatch.setattr(trogly_state.subprocess, "run", run)
    return str(agent), calls


def test_calc_stats_sends_the_request_and_formats_the_answer(home, monkeypatch):
    payload = {
        "champion_id": "16109", "name": "Thor Faehammer",
        "before": {"speed": 125, "critical_chance": 1.26}, "after": {"speed": 135, "critical_chance": 1.26},
        "change": {"speed": 10}, "sets_after": [],
        "gear_after": [{"slot": "Weapon", "artifact_id": "1"}, {"slot": "Boots", "artifact_id": "3", "taken_from": None}],
        "warnings": [],
    }
    agent, calls = fake_agent(home, monkeypatch, payload)
    result = trogly_state.calc_stats(agent, "thor faehammer", equip=["3"], home=home)
    assert calls[0][-2] == "--calc-stats"
    assert calls[1] == {"champion_id": "16109", "equip": ["3"], "remove_slots": [], "only_these": False}
    assert result["change"] == {"SPD": "+10"}
    assert [piece["id"] for piece in result["pieces_put_on"]] == ["3"]
    assert result["slots_emptied"] == []
    assert result["after"]["SPD"] == 135


def test_calc_stats_relays_agent_errors(home, monkeypatch):
    agent, _ = fake_agent(home, monkeypatch, {"error": "artifact 404 is not on this account"})
    with pytest.raises(TroglyError, match="404"):
        trogly_state.calc_stats(agent, "Thor Faehammer", equip=["404"], home=home)


def test_calc_stats_reports_emptied_slots(home, monkeypatch):
    payload = {"champion_id": "16109", "name": "Thor Faehammer", "before": {}, "after": {}, "change": {},
               "sets_after": [], "gear_after": [{"slot": "Weapon", "artifact_id": "1"}], "warnings": []}
    agent, _ = fake_agent(home, monkeypatch, payload)
    result = trogly_state.calc_stats(agent, "Thor Faehammer", remove_slots=["Boots"], home=home)
    assert result["slots_emptied"] == ["Boots"]


def test_calc_stats_spells_out_set_bonus_changes(home, monkeypatch):
    pinpoint = {"set": "X", "name": "Pinpoint", "active": True, "active_stats": ["Accuracy +20"]}
    speed_on = {"set": "AttackSpeed", "name": "Speed", "active": True, "active_stats": ["Speed +12%"]}
    payload = {"champion_id": "16109", "name": "Thor Faehammer", "before": {}, "after": {}, "change": {},
               "sets_before": [pinpoint, {"set": "Y", "name": "Relentless", "active": True}],
               "sets_after": [pinpoint, speed_on], "gear_after": [], "warnings": []}
    agent, _ = fake_agent(home, monkeypatch, payload)
    bonuses = trogly_state.calc_stats(agent, "Thor Faehammer", home=home)["set_bonuses"]
    assert bonuses == {"active_after": {"Pinpoint": ["Accuracy +20"], "Speed": ["Speed +12%"]},
                       "turned_on": ["Speed"], "turned_off": ["Relentless"]}


def test_config_and_state_saved_with_a_byte_order_mark_still_load(tmp_path, monkeypatch):
    # Windows PowerShell 5 and older Notepad write UTF-8 with a BOM; a hand-edited config must still work.
    monkeypatch.delenv("TROGLY_AGENT_PATH", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.json").write_text(json.dumps({"agent_path": "C:\Trogly\agent\Trogly.Agent.exe"}), encoding="utf-8-sig")
    assert trogly_state.configured_agent_path(tmp_path) == "C:\Trogly\agent\Trogly.Agent.exe"
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(STATE), encoding="utf-8-sig")
    assert AccountState.load(tmp_path).account_summary()
