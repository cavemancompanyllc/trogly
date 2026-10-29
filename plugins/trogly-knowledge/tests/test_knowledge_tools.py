import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import knowledge_tools  # noqa: E402
from knowledge_tools import Knowledge, TroglyError  # noqa: E402


def stage(number, level, is_boss=False):
    return {
        "id": 2179000 + number, "number": number, "champion_slots": 5, "has_boss": True,
        "enemies": [{"wave": 1, "type_id": 26666, "name": "Akumori the Phantom Shogun", "stars": 6, "level": level, "is_boss": is_boss}],
        "modifiers": [{"wave": 1, "stat": "Speed", "value": 175, "absolute": True, "boss_only": True},
                      {"wave": 1, "stat": "Defence", "value": -0.76, "absolute": False, "boss_only": True}],
    }


KNOWLEDGE = {
    "exported_at": "2026-10-17T00:00:00+00:00",
    "owned_type_ids": [9176, 5000],
    "champion_types": [
        {"type_id": 9176, "name": "Thor Faehammer", "rarity": "Legendary", "faction": "Barbarians", "affinity": "Magic", "role": "Attack", "skill_ids": [1, 2],
         "leader_skills": [{"stat": "Speed", "absolute": False, "amount": 0.16, "area": "Dungeon", "affinity": None, "faction": None}]},
        {"type_id": 5000, "name": "Fayne", "rarity": "Legendary", "faction": "SylvanWatchers", "affinity": "Void", "role": "Attack", "skill_ids": [3]},
        {"type_id": 26666, "name": "Akumori the Phantom Shogun", "rarity": "Legendary", "faction": "Samurai", "affinity": "Force", "role": "Attack", "skill_ids": [4, 5]},
        {"type_id": 1906, "name": "Ghostborn", "rarity": "Legendary", "faction": "UndeadHordes", "affinity": "Spirit", "role": "Attack", "skill_ids": []},
        {"type_id": 4636, "name": "Tormin the Cold", "rarity": "Legendary", "faction": "Dwarves", "affinity": "Void", "role": "Attack", "skill_ids": []},
    ],
    "skills": {
        "1": {"name": "Wrath of Mjolnir", "description": "Attacks 1 enemy 2 times.", "cooldown": 0,
              "effects": [{"kind": "Damage", "chance": None, "count": 1, "multiplier": "1.9*ATK", "statuses": []}]},
        "2": {"name": "Hammer of Heaven", "description": "Attacks all enemies.", "cooldown": 4,
              "effects": [{"kind": "ApplyDebuff", "chance": 0.75, "count": 1, "multiplier": None, "statuses": [{"name": "DecreaseSpeed30", "turns": 2}]}]},
        "3": {"name": "Brutal Slash", "description": "Places a 60% [Decrease DEF] debuff.", "cooldown": 3,
              "effects": [{"kind": "ApplyDebuff", "chance": None, "count": 1, "multiplier": None, "statuses": [{"name": "DecreaseDefence60", "turns": 3}]}]},
        "4": {"name": "Scourge Sword", "description": "Attacks 1 enemy.", "cooldown": 3,
              "effects": [{"kind": "ApplyDebuff", "chance": None, "count": 1, "multiplier": None, "statuses": [{"name": "Enfeeble", "turns": 3}]}]},
        "5": {"name": None, "description": None, "cooldown": 0, "effects": []},
    },
    "regions": [
        {"id": 217, "code": "AccessoryAscendDungeon", "name": "AccessoryAscendDungeon", "area": "Dungeon",
         "difficulties": {"None": [stage(n, 100 + n * 10) for n in range(1, 26)]}},
        {"id": 219, "code": "ChampionJourneyDungeon", "name": "ChampionJourneyDungeon", "area": "Dungeon",
         "difficulties": {"None": [stage(3, 200, is_boss=True)]}},
        {"id": 702, "code": "DoomTower2", "name": "DoomTower2", "area": "DoomTower",
         "difficulties": {"Hard": [{
             "id": 7022057, "number": 57, "champion_slots": 5, "has_boss": False, "modifiers": [],
             "enemies": [{"wave": 1, "type_id": 1906, "name": "Ghostborn", "stars": 6, "level": 270, "is_boss": False},
                         {"wave": 1, "type_id": 1906, "name": "Ghostborn", "stars": 6, "level": 270, "is_boss": False},
                         {"wave": 2, "type_id": 4636, "name": "Tormin the Cold", "stars": 6, "level": 275, "is_boss": False}],
         }]}},
    ],
}


@pytest.fixture
def knowledge(tmp_path):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(KNOWLEDGE), encoding="utf-8")
    (tmp_path / "data" / "state").mkdir(parents=True)
    state = {"champions": [{"id": "16109", "type_id": 9176, "stars": 6, "level": 60, "blessing_id": "5201",
                            "masteries": ["500113", "500212", "500161", "500343"]},
                           {"id": "5396", "type_id": 5000, "stars": 6, "level": 60}]}
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(state), encoding="utf-8")
    return tmp_path, Knowledge.load(tmp_path)


@pytest.mark.parametrize("code,expected", [
    ("DecreaseDefence60", "Decrease Defence 60%"),
    ("ContinuousDamage025p", "Continuous Damage 2.5%"),
    ("ContinuousHeal075p", "Continuous Heal 7.5%"),
    ("ContinuousDamage5p", "Continuous Damage 5%"),
    ("Enfeeble", "Enfeeble"),
])
def test_status_names_read_like_the_game(code, expected):
    assert knowledge_tools.status_name(code) == expected


def test_champion_kit_lists_skills_with_debuff_chance_and_turns(knowledge):
    _, k = knowledge
    kit = k.champion_kit("thor faehammer")
    assert kit["owned"] is True
    a1, a3 = kit["skills"]
    assert a1["damage"] == ["1.9*ATK"] and a1["cooldown"] is None
    assert a3["places"] == ["debuff Decrease Speed 30% (75% chance, 2 turns)"]


def test_unnamed_internal_skills_are_left_out(knowledge):
    _, k = knowledge
    assert [s["name"] for s in k.champion_kit("Akumori the Phantom Shogun")["skills"]] == ["Scourge Sword"]


def test_search_kits_finds_owned_champions_by_debuff(knowledge):
    home, k = knowledge
    result = k.search_kits("Decrease DEF", home=home)
    assert [hit["champion"] for hit in result["champions"]] == ["Fayne"]
    assert result["champions"][0]["owned_copies"] == [{"id": "5396", "stars": 6, "level": 60}]
    assert result["champions"][0]["skills"][0]["places"] == ["Decrease Defence 60% (3 turns)"]


def test_find_stage_by_boss_prefers_the_boss_own_dungeon(knowledge):
    _, k = knowledge
    found = k.find_stage("Phantom Shogun", 20)
    assert found["place"] == "AccessoryAscendDungeon (Akumori the Phantom Shogun)"
    assert found["difficulty"] == "only track"
    assert (found["stage_id"], found["encounter"]) == (2179020, "stage:2179020")
    assert found["waves"] == [{"wave": 1, "enemies": ["Akumori the Phantom Shogun (6* level 300) - boss"]}]
    assert found["stage_modifiers"] == ["boss: SPD +175 (wave 1)", "boss: DEF -76% (wave 1)"]
    assert found["boss_kits"][0]["skills"][0]["name"] == "Scourge Sword"


def test_find_stage_without_a_number_says_how_many(knowledge):
    _, k = knowledge
    assert k.find_stage("Phantom Shogun")["stages"] == 25


def test_find_stage_out_of_range(knowledge):
    _, k = knowledge
    with pytest.raises(TroglyError, match="stages 1 to 25"):
        k.find_stage("Phantom Shogun", 40)


def test_unknown_place(knowledge):
    _, k = knowledge
    with pytest.raises(TroglyError, match="No dungeon"):
        k.find_stage("Clan Boss Nightmare")


@pytest.mark.parametrize("defender,strong,weak", [
    ("Force", ["Spirit"], ["Magic"]),
    ("Magic", ["Force"], ["Spirit"]),
    ("Spirit", ["Magic"], ["Force"]),
    ("Void", [], []),
])
def test_affinity_matchup_follows_the_raid_wheel(defender, strong, weak):
    matchup = knowledge_tools.affinity_matchup(defender)
    assert (matchup["strong_hits_from"], matchup["weak_hits_from"]) == (strong, weak)


def test_boss_kit_says_who_hits_it_hard(knowledge):
    _, k = knowledge
    matchup = k.find_stage("Phantom Shogun", 20)["boss_kits"][0]["affinity_matchup"]
    assert matchup["strong_hits_from"] == ["Spirit"] and matchup["weak_hits_from"] == ["Magic"]


def test_team_candidates_rank_by_affinity_against_the_boss_and_summarise_kits(knowledge):
    home, k = knowledge
    result = k.team_candidates("Phantom Shogun", 20, home=home)
    assert result["team_size"] == 5
    assert result["boss"]["affinity"] == "Force"
    # Fayne is Void (normal hits on Force); Thor is Magic (weak hits on Force), so Fayne ranks first.
    assert [(c["champion"], c["hits_on_boss"]) for c in result["candidates"]] == [("Fayne", "normal"), ("Thor Faehammer", "weak")]
    thor = result["candidates"][1]["kit"]
    assert thor["damage_scales_with"] == ["ATK"]
    assert thor["debuffs_enemies"] == ["Decrease Speed 30%"]


def test_team_candidates_on_a_stage_without_a_boss_rank_by_hits_on_the_wave_enemies(knowledge):
    home, k = knowledge
    result = k.team_candidates("DoomTower2", 57, "Hard", home=home)
    assert result["boss"] is None and result["area"] == "DoomTower"
    assert result["enemy_affinities"] == [{"wave": 1, "Spirit": 2}, {"wave": 2, "Void": 1}]
    # Thor is Magic: strong on both Spirit enemies. Fayne is Void: normal on everything.
    assert [(c["champion"], c["hits_on_boss"], c["hits_on_enemies"]) for c in result["candidates"]] == [
        ("Thor Faehammer", None, {"strong": 2, "normal": 1, "weak": 0}),
        ("Fayne", None, {"strong": 0, "normal": 3, "weak": 0}),
    ]


def test_team_candidates_show_masteries_blessing_and_where_the_lead_works(knowledge):
    home, k = knowledge
    thor, fayne = k.team_candidates("DoomTower2", 57, "Hard", home=home)["candidates"]
    assert thor["masteries"] == {"count": 4, "capstone": "Warmaster",
                                 "trees": {"Offense": ["Deadly Precision", "Warmaster"], "Defense": ["Tough Skin"],
                                           "Support": ["Lore of Steel"]}}
    assert thor["blessing"] == {"code": "NatureBalance"}
    assert thor["leader_skill"] == [{"aura": "SPD +16% in Dungeon", "works_here": False}]
    assert fayne["masteries"] is None and fayne["blessing"] is None and fayne["leader_skill"] is None
    in_dungeon = k.team_candidates("Phantom Shogun", 20, home=home)["candidates"]
    assert next(c for c in in_dungeon if c["champion"] == "Thor Faehammer")["leader_skill"][0]["works_here"] is True


def test_team_candidates_respect_min_stars(knowledge):
    home, k = knowledge
    assert k.team_candidates("Phantom Shogun", 20, min_stars=7, home=home)["candidates"] == []


def test_scaling_reads_own_stats_and_enemy_hp():
    assert knowledge_tools._scaling_of("3.2*ATK+0.1*TRG_HP") == {"ATK", "enemy HP"}
    assert knowledge_tools._scaling_of("4*DEF") == {"DEF"}


def _with(home, **extra):
    raw = {**KNOWLEDGE, **extra}
    (home / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(raw), encoding="utf-8")
    return Knowledge.load(home)


def test_blessing_shows_its_in_game_name_and_skill_when_the_knowledge_file_has_it(knowledge):
    home, _ = knowledge
    k = _with(home, blessing_skills={"5201": [{"grade": 1, "skill_id": 90001}, {"grade": 4, "skill_id": 90004}]},
              skills={**KNOWLEDGE["skills"],
                      "90001": {"name": "Brimstone", "description": "Weak smite.", "cooldown": 0, "effects": []},
                      "90004": {"name": "Brimstone", "description": "Strong smite.", "cooldown": 0, "effects": []}})
    thor = next(c for c in k.team_candidates("DoomTower2", 57, "Hard", home=home)["candidates"] if c["champion"] == "Thor Faehammer")
    # Thor has awakening 0 in the fixture: below grade 1, so the first grade's skill is shown with a note.
    assert thor["blessing"]["name"] == "Brimstone" and thor["blessing"]["effect"] == "Weak smite."
    assert "needs awakening 1" in thor["blessing"]["effect_note"]


def test_enemy_names_pick_the_doom_tower_rotation(knowledge):
    home, _ = knowledge
    other = {"id": 703, "code": "DoomTower3", "name": "DoomTower3", "area": "DoomTower",
             "difficulties": {"Hard": [{"id": 7032057, "number": 57, "champion_slots": 5, "has_boss": False, "modifiers": [],
                                        "enemies": [{"wave": 1, "type_id": 4636, "name": "Tormin the Cold", "stars": 6, "level": 270, "is_boss": False}]}]}}
    k = _with(home, regions=[*KNOWLEDGE["regions"], other])

    ambiguous = k.find_stage("Doom Tower", 57, "Hard")
    assert ambiguous["ambiguous"] and ambiguous["lineups"] == {
        "DoomTower2": ["wave 1: Ghostborn, Ghostborn", "wave 2: Tormin the Cold"],
        "DoomTower3": ["wave 1: Tormin the Cold"],
    }
    assert k.find_stage("Doom Tower", 57, "Hard", enemies=["ghostborn", "Tormin"])["place"] == "DoomTower2"
    assert k.team_candidates("Doom Tower", 57, "Hard", home=home, enemies=["Ghostborn"])["place"] == "DoomTower2"
    with pytest.raises(TroglyError, match="has all of"):
        k.find_stage("Doom Tower", 57, "Hard", enemies=["Kytis"])
