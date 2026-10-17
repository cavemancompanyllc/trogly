import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import speed_tune  # noqa: E402
from knowledge_tools import Knowledge, TroglyError  # noqa: E402


def skill(name, cooldown=0, effects=(), targets="AliveEnemies", bonuses=()):
    return {"name": name, "description": "", "cooldown": cooldown, "start_cooldown": None, "targets": targets,
            "level_bonuses": [{"type": kind, "value": value} for kind, value in bonuses], "effects": list(effects)}


def effect(kind, target="AllAllies", multiplier=None, statuses=(), chance=None, condition=None, exclude=None):
    return {"kind": kind, "chance": chance, "count": 1, "multiplier": multiplier, "target": target, "exclude": exclude,
            "condition": condition, "statuses": [{"name": name, "turns": turns} for name, turns in statuses]}


def champion_type(type_id, name, skill_ids, leader_skills=(), affinity="Magic", faction="Barbarians"):
    return {"type_id": type_id, "name": name, "rarity": "Legendary", "faction": faction, "affinity": affinity,
            "role": "Support", "skill_ids": list(skill_ids), "leader_skills": list(leader_skills)}


KNOWLEDGE = {
    "exported_at": "2026-10-17T00:00:00+00:00",
    "battle": {"stamina_by_tick": 0.06999999983236194, "stamina_to_turn": 100},
    "owned_type_ids": [1, 2, 3, 4, 5],
    "champion_types": [
        champion_type(1, "Speedy", [11, 12], [{"stat": "Speed", "absolute": False, "amount": 0.3, "area": "Arena",
                                               "affinity": None, "faction": None}]),
        champion_type(2, "Slowpoke", [21]),
        champion_type(3, "Booster", [31, 32, 33], [{"stat": "Speed", "absolute": False, "amount": 0.19, "area": None,
                                                    "affinity": None, "faction": None}]),
        champion_type(4, "Twin", [41]),
        champion_type(5, "Charger", [51, 52]),
    ],
    "skills": {
        "11": skill("Quick Jab"),
        "12": skill("Drain", 4, [effect("ReduceStamina", "AllEnemies", "0.3*MAX_STAMINA")]),
        "21": skill("Plod"),
        "31": skill("Tap"),
        "32": skill("Rally", 4, [effect("ApplyBuff", statuses=[("IncreaseSpeed30", 2)]),
                                 effect("IncreaseStamina", "AllAllies", "0.3*MAX_STAMINA", exclude="Producer")],
                    targets="AliveAllies", bonuses=[("Damage", 0.1), ("Cooldown", 1), ("Cooldown", 1)]),
        "33": skill("Lucky [P]", 0, [effect("IncreaseStamina", "Producer", "0.1*MAX_STAMINA", condition="isAllyTurn")]),
        "41": skill("Copy"),
        "51": skill("Stick"),
        "52": skill("Charge", 5, [effect("ApplyBuff", statuses=[("IncreaseSpeed30", 2)])], targets="AliveAllies"),
    },
    "regions": [],
}


def champion(champion_id, type_id, name, speed, base_speed=100, levels=None):
    return {"id": champion_id, "type_id": type_id, "name": name, "stars": 6, "level": 60, "awakening_level": 0,
            "skill_levels": levels or {},
            "calculated_stats": {"base": {"speed": base_speed}, "total": {"speed": speed, "accuracy": 200}}}


STATE = {"champions": [
    champion("1", 1, "Speedy", 200),
    champion("2", 2, "Slowpoke", 100),
    champion("3", 3, "Booster", 150, levels={"32": 3}),
    champion("4", 4, "Twin", 200),
    champion("5", 5, "Charger", 99, base_speed=98),
]}


@pytest.fixture
def knowledge(tmp_path):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(KNOWLEDGE), encoding="utf-8")
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(STATE), encoding="utf-8")
    return tmp_path, Knowledge.load(tmp_path)


def order(result):
    return [turn["champion"] for turn in result["turns"]]


def test_turn_meter_fills_by_spd_times_seven_hundredths_per_tick(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Slowpoke"}, {"champion": "Twin"}], area="", turns=2, home=home)
    # SPD 200 fills 14 a tick and acts on tick 8 (112); SPD 100 fills 7 a tick and acts on tick 15 (105).
    assert [(t["champion"], t["tick"]) for t in result["turns"]] == [("Twin", 8), ("Slowpoke", 15)]


def test_turn_meter_resets_to_zero_and_overflow_is_lost(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Twin"}], area="", turns=2, home=home)
    # 8 ticks x 14 = 112 -> acts, drops to 0 (not 12), so the second turn is again 8 ticks later.
    assert [t["tick"] for t in result["turns"]] == [8, 16]


def test_ties_within_a_team_go_to_slot_order(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Twin"}, {"champion": "Speedy"}], area="", turns=2, home=home)
    assert order(result) == ["Twin", "Speedy"]


def test_ties_between_teams_go_to_the_players_team(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Twin"}], area="", enemies=[{"name": "Mirror", "speed": 200}], turns=2, home=home)
    assert order(result) == ["Twin", "Mirror"]


def test_arena_aura_counts_in_tag_arena_but_not_in_dungeons(knowledge):
    home, k = knowledge
    team = [{"champion": "Speedy"}, {"champion": "Slowpoke"}]
    tag = k.speed_tune(team, area="Arena3X3", turns=1, home=home)
    dungeon = k.speed_tune(team, area="Dungeon", turns=1, home=home)
    assert tag["leader_aura"]["applies"] is True
    assert [m["battle_spd"] for m in tag["team"]] == [230, 130]
    assert dungeon["leader_aura"]["applies"] is False
    assert [m["battle_spd"] for m in dungeon["team"]] == [200, 100]


def test_aura_without_an_area_works_everywhere(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Booster"}, {"champion": "Slowpoke"}], area="Dungeon", turns=1, home=home)
    assert result["leader_aura"]["applies"] is True
    assert result["team"][1]["battle_spd"] == 119


def test_booked_cooldown_counts_only_cooldown_books(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Booster"}], area="", turns=1, home=home)
    rally = next(s for s in result["team"][0]["skills"] if s["skill"].endswith("Rally"))
    # Level 3 has the first two books: damage, then cooldown -1. The second cooldown book is level 4.
    assert rally["cooldown"] == 3 and rally["booked_from"] == 4


def test_turn_meter_fill_and_speed_buff_change_the_order(knowledge):
    home, k = knowledge
    # Booster's 19% aura works everywhere: Booster 169, Slowpoke 119, Speedy 219.
    team = [{"champion": "Booster"}, {"champion": "Slowpoke"}, {"champion": "Speedy"}]
    plain = k.speed_tune([{"champion": "Booster", "priority": ["A1"]}, *team[1:]], area="", turns=4, home=home)
    boosted = k.speed_tune(team, area="", turns=4, home=home)
    assert [(t["champion"], t["tick"]) for t in plain["turns"]] == [("Speedy", 7), ("Booster", 9), ("Slowpoke", 13), ("Speedy", 14)]
    rally = boosted["turns"][1]
    assert (rally["champion"], rally["skill"]) == ("Booster", "A2 Rally")
    # Rally fills the others' turn meters by 30% (not Booster's own) and puts Increase SPD 30% on everyone.
    assert "fills turn meter by 30%: Slowpoke, Speedy" in rally["effects"]
    assert rally["turn_meters_after"]["Booster"] == 0
    # Slowpoke had 75 after 9 ticks; +30 takes it past 100, so it moves on the very next tick instead of tick 13.
    assert (boosted["turns"][2]["champion"], boosted["turns"][2]["tick"]) == ("Slowpoke", 10)
    speedy_next = next(t for t in boosted["turns"][2:] if t["champion"] == "Speedy")
    assert speedy_next["spd"] == 284.7  # 219 + 30% of 219: the buff scales battle SPD, not base SPD


def test_turn_meter_reduction_lands_on_enemies(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Speedy", "priority": ["A2"]}], area="", enemies=[{"name": "Foe", "speed": 190}], turns=1, home=home)
    first = result["turns"][0]
    assert first["champion"] == "Speedy" and first["skill"] == "A2 Drain"
    # Foe had 8 x 13.3 = 106.4 when Speedy acted; Speedy's turn came first through the tie-free higher meter.
    assert first["turn_meters_after"]["Foe"] == pytest.approx(76.4, abs=0.1)
    assert any("assumed to land" in n for n in result["not_simulated"])


def test_passive_turn_meter_effects_are_reported_not_simulated(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Booster"}], area="", turns=1, home=home)
    assert any("Lucky [P]" in n for n in result["not_simulated"])


def test_speed_override_tries_a_different_spd(knowledge):
    home, k = knowledge
    result = k.speed_tune([{"champion": "Slowpoke", "speed": 300}], area="", turns=1, home=home)
    assert result["team"][0]["battle_spd"] == 300
    assert result["turns"][0]["tick"] == 5  # 21 a tick
    assert any("SPD set to 300" in n for n in result["notes"])


def test_unknown_champion_and_unknown_skill_are_clear_errors(knowledge):
    home, k = knowledge
    with pytest.raises(TroglyError, match="No owned champion"):
        k.speed_tune([{"champion": "Nobody"}], home=home)
    with pytest.raises(TroglyError, match="has no skill 'A9'"):
        k.speed_tune([{"champion": "Twin", "priority": ["A9"]}], home=home)


def test_arena_candidates_fastest_first_with_aura_and_turn_meter_skills(knowledge):
    home, k = knowledge
    result = k.arena_candidates("tag", home=home)
    names = [c["champion"] for c in result["candidates"]]
    assert names[:2] == ["Speedy", "Twin"] and names[-2:] == ["Slowpoke", "Charger"]
    speedy = result["candidates"][0]
    assert speedy["aura"] == [{"aura": "SPD +30% in Arena", "works_in_arena": True}]
    booster = next(c for c in result["candidates"] if c["champion"] == "Booster")
    assert booster["turn_meter_skills"] == [
        "A2 Rally (cooldown 3, booked from 4): Increase Speed 30% for 2 turns on AllAllies; fill TM by 30% on AllAllies except self"]
    assert any("three teams of 3" in rule for rule in result["rules"])
    with pytest.raises(TroglyError):
        k.arena_candidates("live", home=home)


def test_replays_a_real_arena_fight_turn_for_turn(knowledge):
    # Classic Arena on the burner account, 2026-10-17, read from the game's battle state with Agent --watch-battle:
    # our Corvis 113, Hope 128, Kael 109, Tagoar 99 (A2: Increase SPD 30% on all allies) against Kael 123,
    # Warpriest 104, Bogwalker 103.65 (their Sniper died before acting, so it is left out).
    home, k = knowledge
    team = [
        {"champion": "Twin", "speed": 113, "priority": ["A1"]},
        {"champion": "Slowpoke", "speed": 128},
        {"champion": "Speedy", "speed": 109, "priority": ["A1"]},
        {"champion": "Charger"},
    ]
    enemies = [{"name": "Their Kael", "speed": 123}, {"name": "Warpriest", "speed": 104}, {"name": "Bogwalker", "speed": 103.65}]
    # The game matches for 8 turns; after that its Kael's A2 filled his turn meter 25% for killing the Sniper, which
    # depends on kills and is reported as not simulated.
    result = k.speed_tune(team, area="Arena", enemies=enemies, turns=8, home=home)
    assert [(t["champion"], t["tick"]) for t in result["turns"]] == [
        ("Slowpoke", 12), ("Their Kael", 13), ("Twin", 14), ("Speedy", 15), ("Warpriest", 16), ("Bogwalker", 17),
        ("Charger", 18), ("Slowpoke", 22),
    ]
    # In game after the buff: Corvis 146.9, Hope 166.4, Kael 141.7, Tagoar 128.7.
    assert result["turns"][7]["spd"] == 166.4
    assert result["turns"][7]["turn_meters_after"] == pytest.approx({
        "Twin": 72.8, "Slowpoke": 0.0, "Speedy": 62.6, "Charger": 36.0, "Their Kael": 77.5, "Warpriest": 43.7, "Bogwalker": 36.3})


def test_old_knowledge_file_asks_for_a_refresh(knowledge):
    home, k = knowledge
    k.raw.pop("battle")
    with pytest.raises(TroglyError, match="refresh_knowledge"):
        k.speed_tune([{"champion": "Twin"}], home=home)
    with pytest.raises(TroglyError, match="refresh_knowledge"):
        k.arena_candidates(home=home)


@pytest.mark.parametrize("formula,expected", [
    ("0.3*MAX_STAMINA", ("max", 0.3)),
    ("MAX_STAMINA*0.15", ("max", 0.15)),
    ("MAX_STAMINA", ("max", 1.0)),
    ("TRG_STAMINA", ("current", 1.0)),
    ("0.5*TRG_STAMINA", ("current", 0.5)),
    ("MAX_STAMINA*0.25*killedEnemiesCountCurrentTurn", None),
])
def test_turn_meter_formulas(formula, expected):
    assert speed_tune.stamina_amount(formula) == expected
