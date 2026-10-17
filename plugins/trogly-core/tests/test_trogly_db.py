import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import trogly_state  # noqa: E402
from test_trogly_state import STATE  # noqa: E402
from trogly_db import Ledger  # noqa: E402
from trogly_state import AccountState, TroglyError  # noqa: E402


def write_state(home, state):
    path = trogly_state.state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    return AccountState.load(home)


@pytest.fixture
def ledger(tmp_path):
    with Ledger(tmp_path) as opened:
        yield opened


def later_state():
    state = copy.deepcopy(STATE)
    state["last_updated"] = "2026-10-17T08:00:00+00:00"
    state["account"]["level"] = 84
    state["account"]["resources"]["Gem"] = 1500
    thor = state["champions"][0]
    thor["equipped_artifact_ids"] = {"Weapon": "1", "Boots": "3"}
    thor["calculated_stats"]["total"]["speed"] = 140
    state["champions"].pop()  # the 3-star Kael was fed away
    state["champions"].append({"id": "99", "name": "Arbiter", "level": 1, "stars": 6, "rarity": 5,
                               "equipped_artifact_ids": {}})
    return state


def test_migrations_set_schema_version(ledger):
    assert ledger.connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_import_is_skipped_for_the_same_read(tmp_path, ledger):
    state = write_state(tmp_path, STATE)
    assert ledger.import_snapshot(state)["imported"] is True
    assert ledger.import_snapshot(state)["imported"] is False
    assert len(ledger.history()) == 1


def test_diff_with_one_snapshot_explains_what_to_do(tmp_path, ledger):
    ledger.import_snapshot(write_state(tmp_path, STATE))
    assert "Refresh again" in ledger.diff()["message"]


def test_diff_reports_account_champion_and_gear_changes(tmp_path, ledger):
    ledger.import_snapshot(write_state(tmp_path, STATE))
    ledger.import_snapshot(write_state(tmp_path, later_state()))
    diff = ledger.diff("previous")
    assert diff["account"]["level"] == {"from": 83, "to": 84, "change": 1}
    assert diff["account"]["resources"]["Gem"]["change"] == -122
    assert [c["name"] for c in diff["champions_added"]] == ["Arbiter"]
    assert [c["id"] for c in diff["champions_removed"]] == ["8"]
    thor = next(c for c in diff["champions_changed"] if c["name"] == "Thor Faehammer")
    assert thor["changes"]["gear"] == {"pieces_changed": 1, "pieces_removed": 1}
    assert thor["changes"]["stats"]["SPD"] == {"from": 125, "to": 140, "change": 15}


def test_diff_since_date_uses_last_snapshot_on_or_before_it(tmp_path, ledger):
    ledger.import_snapshot(write_state(tmp_path, STATE))
    ledger.import_snapshot(write_state(tmp_path, later_state()))
    assert ledger.diff("2026-10-16")["from"].startswith("2026-10-16")


def test_goals_add_update_and_list(ledger):
    added = ledger.goals("add", title="6-star Thor", target="by November")["added"]
    ledger.goals("update", goal_id=added["id"], status="done")
    assert ledger.goals("list")["goals"] == []
    assert ledger.goals("list", status="done")["goals"][0]["title"] == "6-star Thor"


def test_goal_update_rejects_unknown_status(ledger):
    goal = ledger.goals("add", title="Arena")["added"]
    with pytest.raises(TroglyError, match="Status"):
        ledger.goals("update", goal_id=goal["id"], status="finished")


def test_team_save_resolves_names_and_replaces_on_resave(tmp_path, ledger):
    state = write_state(tmp_path, STATE)
    ledger.teams("save", state, name="Shogun 20", champions=["Thor Faehammer", "7"], roles=["damage"])
    saved = ledger.teams("save", state, name="shogun 20", champions=["7"], purpose="Phantom Shogun 20")["saved"]
    assert [m["id"] for m in saved["members"]] == ["7"]
    assert saved["purpose"] == "Phantom Shogun 20"
    assert ledger.teams("list")["teams"][0]["members"] == 1


def test_team_member_lost_from_account_is_flagged(tmp_path, ledger):
    ledger.teams("save", write_state(tmp_path, STATE), name="Fodder", champions=["8"])
    team = ledger.teams("get", write_state(tmp_path, later_state()), name="Fodder")
    assert team["members"][0]["missing"] is True


def test_loadout_defaults_to_current_gear_and_tracks_where_pieces_went(tmp_path, ledger):
    ledger.loadouts("save", write_state(tmp_path, STATE), champion="Thor Faehammer", name="Clan Boss")
    compared = ledger.loadouts("compare", write_state(tmp_path, later_state()), champion="Thor Faehammer",
                               name="Clan Boss")
    statuses = {p["id"]: p["status"] for p in compared["pieces"]}
    assert statuses == {"1": "equipped", "2": "in the vault"}
    assert compared["fully_equipped"] is False


def test_loadout_rejects_unknown_artifacts(tmp_path, ledger):
    with pytest.raises(TroglyError, match="not on the account"):
        ledger.loadouts("save", write_state(tmp_path, STATE), champion="Thor Faehammer", name="X",
                        artifact_ids=["404"])


def test_session_notes_list_newest_first(ledger):
    ledger.notes("add", body="Geared Thor", next_steps="Book his A1")
    ledger.notes("add", body="Planned Shogun")
    assert [n["body"] for n in ledger.notes("list")["notes"]] == ["Planned Shogun", "Geared Thor"]
