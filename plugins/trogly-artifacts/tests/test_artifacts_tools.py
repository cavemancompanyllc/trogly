import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import artifacts_tools  # noqa: E402
from artifacts_tools import TroglyError  # noqa: E402

STATE = {
    "champions": [
        {"id": "16109", "name": "Thor Faehammer", "stars": 6, "level": 60, "equipped_artifact_ids": {"Weapon": "1", "Boots": "2"}},
        {"id": "14224", "name": "Arbiter", "stars": 6, "level": 60, "equipped_artifact_ids": {"Boots": "3"}},
        {"id": "7", "name": "Kael", "stars": 5, "level": 50, "equipped_artifact_ids": {}},
        {"id": "8", "name": "Kael", "stars": 3, "level": 30, "equipped_artifact_ids": {}},
    ],
    "artifacts": [
        {"id": "1", "set": "IgnoreDefense", "set_name": "Savage", "slot": "Weapon", "rank": 6, "level": 16,
         "primary_stat": {"stat": "Attack", "value": 265, "is_absolute": True}, "substats": []},
        {"id": "3", "set": "AttackSpeed", "set_name": "Speed", "slot": "Boots", "rank": 6, "level": 16,
         "primary_stat": {"stat": "Speed", "value": 45, "is_absolute": True},
         "substats": [{"stat": "CriticalChance", "value": 0.1, "is_absolute": False, "glyph": 0.02}]},
    ],
}

ANSWER = {
    "champion_id": "16109", "name": "Thor Faehammer",
    "current": {"speed": 125, "attack": 4940, "critical_chance": 1.26},
    "loadouts": [{
        "stats": {"speed": 170, "attack": 4800, "critical_chance": 1.0},
        "change": {"speed": 45, "attack": -140, "critical_chance": -0.26},
        "gear": [{"slot": "Weapon", "artifact_id": "1", "taken_from": None},
                 {"slot": "Boots", "artifact_id": "3", "taken_from": "Arbiter (id 14224)"}],
        "sets": [{"set": "AttackSpeed", "name": "Speed", "active": False, "active_stats": []},
                 {"set": "X", "name": "Pinpoint", "active": True, "active_stats": ["Accuracy +20"]}],
    }],
    "search_complete": True, "seconds": 1.2, "candidates_per_slot": {"Weapon": 3, "Boots": 4},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("TROGLY_AGENT_PATH", raising=False)
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(STATE), encoding="utf-8")
    agent = tmp_path / "Trogly.Agent.exe"
    agent.write_bytes(b"")
    (tmp_path / "data" / "config.json").write_text(json.dumps({"agent_path": str(agent)}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def agent(monkeypatch):
    calls = []

    class Completed:
        def __init__(self, stdout):
            self.stdout, self.stderr, self.returncode = stdout, "", 0

    def run(command, **_):
        calls.append((command, json.loads(Path(command[-1]).read_text(encoding="utf-8"))))
        return Completed(json.dumps(agent.answer))

    agent.answer = ANSWER
    monkeypatch.setattr(artifacts_tools.subprocess, "run", run)
    agent.calls = calls
    return agent


def test_request_uses_friendly_names_and_percent_crit(home, agent):
    artifacts_tools.optimize_gear(
        "thor faehammer", "atk", minimum={"SPD": 200, "C.RATE": 100}, maximum={"crit damage": 250},
        require_sets={"Speed": 4}, use_other_champions_gear=True, never_take_from=["Arbiter"], home=home,
    )
    command, request = agent.calls[0]
    assert command[-2] == "--optimize-gear"
    assert request == {
        "champion_id": "16109", "maximize": "attack", "min": {"speed": 200.0, "critical_chance": 1.0},
        "max": {"critical_damage": 2.5}, "require_sets": {"Speed": 4}, "allowed_sets": None, "pool": "everything",
        "never_take_from": ["14224"], "exclude_artifacts": [], "results": 3, "time_limit_seconds": 15.0,
    }


def test_loadout_explains_pieces_changes_and_sets(home, agent):
    result = artifacts_tools.optimize_gear("Thor Faehammer", "SPD", home=home)
    loadout = result["loadouts"][0]
    assert loadout["change"] == {"ATK": "-140", "SPD": "+45", "C.RATE": "-26%"}
    assert loadout["pieces_to_change"] == 1
    weapon, boots = loadout["pieces"]
    assert weapon["already_worn"] is True
    assert (boots["set"], boots["main_stat"], boots["substats"], boots["taken_from"]) == (
        "Speed", "SPD 45", ["C.RATE 12%"], "Arbiter (id 14224)")
    assert loadout["active_sets"] == {"Pinpoint": ["Accuracy +20"]}
    assert result["request"]["gear_pool"] == "vault and own gear"


def test_no_loadout_explains_how_to_relax(home, agent):
    agent.answer = {**ANSWER, "loadouts": []}
    result = artifacts_tools.optimize_gear("Thor Faehammer", "SPD", minimum={"SPD": 400}, home=home)
    assert "Relax a minimum" in result["message"]


def test_incomplete_search_is_flagged(home, agent):
    agent.answer = {**ANSWER, "search_complete": False}
    result = artifacts_tools.optimize_gear("Thor Faehammer", "SPD", home=home)
    assert "not proven best" in result["message"]


def test_duplicate_copies_resolve_to_the_most_built(home, agent):
    artifacts_tools.optimize_gear("Kael", "SPD", home=home)
    assert agent.calls[0][1]["champion_id"] == "7"


def test_unknown_stat_lists_the_options(home, agent):
    with pytest.raises(TroglyError, match="not a stat"):
        artifacts_tools.optimize_gear("Thor Faehammer", "swiftness", home=home)


def test_agent_errors_are_relayed(home, agent):
    agent.answer = {"error": "'Spped' is not an artifact set."}
    with pytest.raises(TroglyError, match="Spped"):
        artifacts_tools.optimize_gear("Thor Faehammer", "SPD", require_sets={"Spped": 4}, home=home)


def test_missing_agent_points_to_set_agent_path(tmp_path, monkeypatch):
    monkeypatch.delenv("TROGLY_AGENT_PATH", raising=False)
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(STATE), encoding="utf-8")
    with pytest.raises(TroglyError, match="set_agent_path"):
        artifacts_tools.optimize_gear("Thor Faehammer", "SPD", home=tmp_path)


def test_answers_from_the_saved_game_read_say_so_and_how_old_they_are():
    assert artifacts_tools._data_source({"source": "live", "read_at": "2026-10-29T03:31:07+00:00"}) is None
    assert artifacts_tools._data_source(None) is None
    saved = artifacts_tools._data_source({"source": "saved", "read_at": "2000-01-01T00:00:00+00:00"})
    assert saved["source"] == "saved game read" and saved["age_minutes"] > 0
    assert "last account refresh" in saved["message"]
