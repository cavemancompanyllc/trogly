import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alliance_rewards as rewards  # noqa: E402
import knowledge_tools  # noqa: E402
from knowledge_tools import Knowledge, TroglyError  # noqa: E402
from test_battle_sim import STATE  # noqa: E402
from test_chimera_sim import chimera_knowledge  # noqa: E402

RANGES = [{"range": "Range1", "low_percent": 0, "high_percent": 1},
          {"range": "Range2", "low_percent": 1, "high_percent": 2},
          {"range": "Range3", "low_percent": 2, "high_percent": 100}]
TABLE = {"difficulty": "Easy", "health": 1_000_000, "ranges": RANGES,
         "chest_by_range": {"Range1": "D", "Range2": "C", "Range3": "B"}}


def test_hydra_bands_include_the_top_edge_so_the_first_band_wins_a_tie():
    assert rewards.band(1.0, RANGES, chimera=False) == "Range1"
    assert rewards.chest(10_000, TABLE, chimera=False) == "D"
    assert rewards.chest(10_001, TABLE, chimera=False) == "C"


def test_chimera_bands_exclude_the_top_edge_within_the_games_margin():
    assert rewards.band(1.0, RANGES, chimera=True) == "Range2"
    assert rewards.band(1.0 - 1e-10, RANGES, chimera=True) == "Range2"
    assert rewards.band(0.5, RANGES, chimera=True) == "Range1"


def test_damage_outside_every_band_earns_no_chest():
    assert rewards.chest(2_000_000, TABLE, chimera=True) == rewards.NO_CHEST
    assert rewards.chest(5, {**TABLE, "health": 0}, chimera=False) == rewards.NO_CHEST


def test_odds_per_key_and_summed_keys():
    per_run = [5_000, 15_000, 25_000, 25_000]
    assert rewards.chest_odds(per_run, TABLE, chimera=False) == {"B": 0.5, "C": 0.25, "D": 0.25}
    two_keys = rewards.chest_odds(per_run, TABLE, chimera=False, keys=2)
    # Two keys reach B unless both draws are low (3 of 16 pairs).
    assert two_keys["B"] == pytest.approx(13 / 16, abs=0.04)
    view = rewards.outlook(per_run, TABLE, chimera=False)
    assert view["most_likely_chest"] == "B"
    assert view["expected_chest_rank"] == pytest.approx(0.5 * 3 + 0.25 * 2 + 0.25 * 1)
    assert view["percent_of_health_per_key"] == pytest.approx(1.75)


def chimera_with_tables():
    data = chimera_knowledge(with_forms=False)
    data["alliance_bosses"] = {
        "chimera": [{"boss": {"difficulty": name, "health": health,
                              "chest_by_range": {"Range1": low, "Range2": high}},
                     "reward_ranges": [{"range": "Range1", "low_percent": 0, "high_percent": 50},
                                       {"range": "Range2", "low_percent": 50, "high_percent": 10**12}]}
                    for name, health, low, high in (("Easy", 1, "D", "C"), ("Normal", 10**12, "B", "A"))],
        "chimera_rewards": [{"chest": "C", "range": "Range2", "items": [
            {"type": "Resources", "probability": 0, "min_count": 3, "max_count": 3}],
            "rolled": {"count": 1, "items": [{"type": "Artifact", "probability": 0.25, "min_count": 1, "max_count": 1,
                                              "artifact_rank": 6}]}}],
    }
    return data


def load(tmp_path, data):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(data), encoding="utf-8")
    return Knowledge.load(tmp_path)


def test_chimera_rewards_plays_every_difficulty_and_names_the_chests(tmp_path, monkeypatch):
    knowledge = load(tmp_path, chimera_with_tables())
    monkeypatch.setattr(knowledge_tools, "_state", lambda home: STATE)
    answer = knowledge.chimera_rewards([["Tank"]], runs=2, home=tmp_path)
    rows = {row["difficulty"]: row for row in answer["teams"][0]["by_difficulty"]}
    # Any damage is past 50% of a 1-HP Easy Chimera's health; a 10^12 Normal one stays in its first band.
    assert rows["Easy"]["chest_odds"] == {"C": 1.0}
    assert rows["Normal"]["chest_odds"] == {"B": 1.0}
    assert answer["teams"][0]["best_difficulty"] == "Normal"
    assert answer["chests"]["C"]["items"] == [{"type": "Resources", "count": 3}]
    assert answer["chests"]["C"]["rolled_from"][0] == {"type": "Artifact", "count": 1, "chance": 0.25, "artifact_rank": 6}
    assert answer["chest_needs"]["Normal"][1] == {"chest": "A", "from_damage": 5 * 10**11, "to_damage": 10**22}


def test_rewards_say_when_the_knowledge_file_has_no_chest_tables(tmp_path, monkeypatch):
    knowledge = load(tmp_path, chimera_knowledge(with_forms=False))
    monkeypatch.setattr(knowledge_tools, "_state", lambda home: STATE)
    with pytest.raises(TroglyError, match="no Chimera chest tables"):
        knowledge.chimera_rewards([["Tank"]], runs=1, home=tmp_path)
