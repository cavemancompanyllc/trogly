import json
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import battle_rules as rules  # noqa: E402
import battle_sim  # noqa: E402
import formula  # noqa: E402
from knowledge_tools import Knowledge  # noqa: E402


def skill(name, cooldown=0, effects=(), targets="AliveEnemies", bonuses=(), description=""):
    return {"name": name, "description": description, "cooldown": cooldown, "start_cooldown": None, "targets": targets,
            "level_bonuses": [{"type": kind, "value": value} for kind, value in bonuses], "effects": list(effects)}


def effect(kind, target="Target", multiplier=None, statuses=(), chance=None, condition=None, count=1):
    return {"kind": kind, "chance": chance, "count": count, "multiplier": multiplier, "target": target, "exclude": None,
            "condition": condition, "statuses": [{"name": name, "turns": turns} for name, turns in statuses]}


def champion_type(type_id, name, skill_ids, affinity="Magic", rarity="Legendary"):
    return {"type_id": type_id, "name": name, "rarity": rarity, "faction": "Barbarians", "affinity": affinity,
            "role": "Attack", "skill_ids": list(skill_ids), "leader_skills": []}


BOSS_STATS = {"health": 50_000_000, "attack": 1000, "defence": 1500, "speed": 120, "resistance": 100, "accuracy": 100,
              "critical_chance": 0.0, "critical_damage": 0.5}

KNOWLEDGE = {
    "exported_at": "2026-10-28T00:00:00+00:00",
    "battle": {"stamina_by_tick": 0.06999999983236194, "stamina_to_turn": 100},
    "owned_type_ids": [1, 2, 3],
    "champion_types": [
        champion_type(1, "Poisoner", [11, 12, 13]),
        champion_type(2, "Medic", [21, 22]),
        champion_type(3, "Tank", [31]),
        champion_type(9000, "Demon Lord", [901, 902, 903, 904, 905, 906], affinity="Void", rarity="Common"),
    ],
    "skills": {
        "11": skill("Jab", effects=[effect("Damage", multiplier="1*ATK")]),
        "12": skill("Venom", 2, [effect("Damage", multiplier="2*ATK"),
                                 effect("ApplyDebuff", "RelationTarget", statuses=[("ContinuousDamage5p", 2)]),
                                 effect("ApplyDebuff", "RelationTarget", statuses=[("Stun", 1)])]),
        "13": skill("Rally", 4, [effect("TeamAttack", "AllAllies")], targets="AliveAllies"),
        "21": skill("Tap", effects=[effect("Damage", multiplier="1*ATK")]),
        "22": skill("Second Wind", 3, [effect("Revive", "AllDeadAllies", multiplier="0.5")], targets="AliveAllies"),
        "31": skill("Bonk", effects=[effect("Damage", multiplier="1*DEF")]),
        "901": skill("Crushing Force", effects=[effect("Damage", multiplier="0.2*TRG_B_HP"),
                                                effect("ApplyDebuff", "RelationTarget", statuses=[("Stun", 1)], chance=1)],
                     description="Attacks 1 enemy. Places a [Stun] debuff for 1 turn. This debuff cannot be resisted."),
        "902": skill("Flesh Wither", 3, [effect("Damage", "AllEnemies", "3*ATK")]),
        "903": skill("Dark Nova", 3, [effect("Damage", "AllEnemies", "3.6*ATK")]),
        "904": skill("Infernal Resilience [P]", effects=[], targets=None,
                     description="Immune to [Stun], [Freeze], [Sleep], and [Decrease SPD] debuffs."),
        "905": skill("Clan Boss Cont damage resistance IV", targets=None, effects=[
            effect("ChangeDamageMultiplier", "Owner", "-(DMG_MUL-(!relatedEffectWasDetonated*50000))",
                   condition="relatedStatusEffectTypeId==ContinuousDamage5p&&DMG_MUL>=!relatedEffectWasDetonated*50000")]),
        "906": skill("Gathering Fury [P]", targets=None, effects=[
            effect("ChangeDamageMultiplier", "Owner", "DMG_MUL*0.75*(OWNERS_TURN_NUMBER-9)",
                   condition="isOwnerProduceRelatedEffect&&OWNERS_TURN_NUMBER>=10&&OWNERS_TURN_NUMBER<20")]),
    },
    "regions": [{"id": 401, "code": "AllianceBoss", "name": "AllianceBoss", "area": "AllianceBoss", "difficulties": {"None": [
        {"id": 4019017, "number": 17, "champion_slots": 5, "has_boss": True, "modifiers": [],
         "enemies": [{"wave": 1, "type_id": 9000, "name": "Demon Lord", "stars": 6, "level": 185, "is_boss": False,
                      "stats": BOSS_STATS}]},
    ]}}],
}


def champion(champion_id, type_id, name, speed, attack=3000, defence=1500, health=40000, accuracy=250, levels=None):
    total = {"health": health, "attack": attack, "defence": defence, "speed": speed, "resistance": 100,
             "accuracy": accuracy, "critical_chance": 0.5, "critical_damage": 1.0, "critical_heal": 0, "ignore_defence": 0}
    return {"id": champion_id, "type_id": type_id, "name": name, "stars": 6, "level": 60, "awakening_level": 0,
            "skill_levels": levels or {}, "calculated_stats": {"base": {**total, "health": 15000}, "precise": total, "sets": []}}


STATE = {"champions": [
    champion("1", 1, "Poisoner", 200),
    champion("2", 2, "Medic", 180),
    champion("3", 3, "Tank", 150, health=200000),
]}


@pytest.fixture
def knowledge(tmp_path):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(KNOWLEDGE), encoding="utf-8")
    return Knowledge.load(tmp_path)


def simulate(knowledge, team=("Poisoner", "Medic", "Tank"), **options):
    return battle_sim.simulate_clan_boss(knowledge, STATE, [{"champion": name} for name in team], "Nightmare", "Void",
                                         boss_turn_limit=options.pop("boss_turn_limit", 12), **options)


class Values:
    def __init__(self, **values):
        self.values = values

    def value(self, name):
        if name not in self.values:
            raise formula.Unsupported(name)
        return self.values[name]

    def call(self, name, args):
        raise formula.Unsupported(name)


# Game formulas


def test_resist_chance_follows_the_game_curve():
    assert rules.resist_chance(100, 130) == pytest.approx(0.30)
    assert rules.resist_chance(100, 100) == pytest.approx(0.03 + 0.27 * math.exp(-1.8))
    assert rules.resist_chance(300, 100) == pytest.approx(0.03, abs=0.001)
    assert rules.resist_chance(100, 230) == pytest.approx(0.3 + 0.67 * (1 - math.exp(-3)))


def test_defence_absorbs_at_most_85_percent():
    assert rules.defence_factor(1500) == pytest.approx(1 - 0.85 * (1 - math.exp(-1)))
    assert rules.defence_factor(0) == pytest.approx(1.0)
    assert rules.defence_factor(10 ** 7) == pytest.approx(0.15)
    # Ignore DEF lowers the effective DEF.
    assert rules.defence_factor(3000, defence_modifier=-0.5) == pytest.approx(rules.defence_factor(1500))


def test_one_roll_checks_glance_before_crit():
    constants, _ = rules.gameplay_constants({})
    rng = random.Random()
    rng.random = lambda: 0.1
    assert rules.hit_type(-1, 1.0, rng, constants) == rules.GLANCING
    assert rules.hit_type(0, 1.0, rng, constants) == rules.CRITICAL
    assert rules.hit_type(0, 0.05, rng, constants) == rules.NORMAL
    # An advantage adds crit chance before crushing hits get their turn.
    assert rules.hit_type(1, 0.0, rng, constants) == rules.CRITICAL
    rng.random = lambda: 0.2
    assert rules.hit_type(1, 0.0, rng, constants) == rules.CRUSHING


def test_damage_variance_is_90_to_109_percent():
    rng = random.Random(3)
    rolls = {rules.damage_roll(rng) for _ in range(2000)}
    assert min(rolls) == 0.9 and max(rolls) == 1.09


# Formulas


def test_formulas_follow_operator_precedence():
    assert formula.run("!a*5+2", Values(a=0)) == 7
    assert formula.run("-(DMG-(!x*100))", Values(DMG=250, x=0)) == -150
    assert formula.run("a>=10&&a<20||b", Values(a=12, b=0)) == 1
    assert formula.run("3.9*DEF", Values(DEF=1000)) == pytest.approx(3900)


def test_unknown_names_are_reported_not_guessed():
    with pytest.raises(formula.Unsupported) as error:
        formula.run("mystery*2", Values())
    assert error.value.name == "mystery"
    assert formula.names_in("TargetHasEffectOfKind(Burn)&&x") == {"TargetHasEffectOfKind", "x"}


# Battles


def test_the_same_seed_plays_the_same_battle(knowledge):
    first, second = simulate(knowledge, runs=3, seed=7), simulate(knowledge, runs=3, seed=7)
    assert first["per_run"] == second["per_run"]
    assert simulate(knowledge, runs=3, seed=8)["per_run"] != first["per_run"]


def test_the_boss_opens_with_its_aoes_then_stuns(knowledge):
    turns = [entry["skill"] for entry in simulate(knowledge, runs=1)["timeline"] if entry["who"] == "Demon Lord"]
    assert turns[:6] == ["A2 Flesh Wither", "A3 Dark Nova", "A1 Crushing Force"] * 2


def test_boss_damage_caps_apply_to_poison(knowledge):
    events = [event for entry in simulate(knowledge, runs=1)["timeline"] if entry["who"] == "Demon Lord" for event in entry["events"]]
    poison = [event for event in events if event.startswith("poison")]
    # 5% of 50M would be 2.5M; the boss's own passive caps a 5% poison tick at 50,000.
    assert poison and all(event == "poison 50000" for event in poison)


def test_the_boss_is_immune_to_what_its_passive_says(knowledge):
    debuffs = simulate(knowledge, runs=2)["debuffs"]
    assert debuffs["Stun"]["landed"] == 0 and debuffs["Stun"]["boss_immune"] == debuffs["Stun"]["attempted"]
    assert debuffs["ContinuousDamage5p"]["landed"] > 0


def test_revives_wait_until_someone_is_dead(knowledge):
    timeline = simulate(knowledge, runs=1)["timeline"]
    medic = [entry for entry in timeline if entry["who"] == "Medic"]
    assert medic and all(entry["skill"] != "A2 Second Wind" for entry in medic[:3])


def test_presets_override_the_default_order(knowledge):
    team = [{"champion": "Poisoner", "priority": ["A1"], "never": ["A2"]}, {"champion": "Tank"}]
    result = battle_sim.simulate_clan_boss(knowledge, STATE, team, "Nightmare", "Void", runs=1, boss_turn_limit=5)
    used = {entry["skill"] for entry in result["timeline"] if entry["who"] == "Poisoner"} - {"-"}  # "-": stunned
    assert used == {"A1 Jab"}


def test_what_isnt_modelled_is_listed(knowledge):
    result = simulate(knowledge, runs=1)
    assert "Rally: TeamAttack effect" in result["not_simulated"]["Poisoner"]
    assert result["champions"][0]["coverage"] < 1


def test_gathering_fury_raises_the_bosses_damage_from_turn_10(knowledge):
    timeline = simulate(knowledge, team=("Tank",), runs=1, boss_turn_limit=13)["timeline"]
    nova = {entry["turn"]: entry for entry in timeline if entry["who"] == "Demon Lord" and entry["skill"] == "A3 Dark Nova"}
    early = int(nova[2]["events"][0].split()[-1])
    late = int(nova[11]["events"][0].split()[-1])
    assert late > early * 1.9


def test_a_knowledge_file_without_boss_stats_says_what_to_do(knowledge):
    knowledge.raw["regions"][0]["difficulties"]["None"][0]["enemies"][0]["stats"] = None
    with pytest.raises(battle_sim.BattleError, match="refresh_knowledge"):
        simulate(knowledge, runs=1)


# Report


def test_compared_teams_rank_by_mean_damage_and_fill_the_report(knowledge, tmp_path):
    import battle_report

    results = battle_report.compare_teams(knowledge, STATE, [["Tank"], {"label": "Poison </script>", "champions": ["Poisoner", "Tank"]}],
                                          runs=2)
    assert [r["damage"]["mean"] for r in results["teams"]] == sorted((r["damage"]["mean"] for r in results["teams"]), reverse=True)
    report = battle_report.write_report(results, tmp_path, title="Test <Report>")
    html = report.read_text(encoding="utf-8")
    assert "<title>Test &lt;Report&gt;</title>" in html
    assert r"Poison <\/script>" in html and "__TROGLY_DATA__" not in html


def test_ollama_mode_returns_the_local_write_up(knowledge, tmp_path, monkeypatch):
    import io
    import battle_report

    def fake_urlopen(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/api/tags"):
            return io.BytesIO(json.dumps({"models": [{"name": "nomic-embed-text"}, {"name": "qwen3:32b"}]}).encode())
        assert json.loads(request.data)["model"] == "qwen3:32b"
        return io.BytesIO(json.dumps({"response": "<think>hmm</think>Team 1 wins on poison uptime."}).encode())

    monkeypatch.setattr(battle_report.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("TROGLY_OLLAMA_MODEL", raising=False)
    results = battle_report.compare_teams(knowledge, STATE, [["Poisoner", "Tank"]], runs=1)
    answer = battle_report.finish(results, tmp_path, analysis="ollama")
    assert answer["analysis"] == {"by": "Ollama qwen3:32b", "text": "Team 1 wins on poison uptime."}
    assert "digest" not in answer and Path(answer["report_file"]).exists()


# Damage-changing statuses


HIT = {"kind": "Damage", "target": "Target", "multiplier": "1*ATK"}
POISON_TICK = {"kind": "ContinuousDamage", "target": "Target", "multiplier": None}
STATUS_TYPES = {
    "IncreaseDamageTaken25": {"id": 350, "effect": {"kind": "IncreaseDamageTaken", "multiplier": "1.3",
                                                    "condition": "ownerIsRelatedEffectTarget",
                                                    "relation": {"kinds": ["Damage"], "phases": ["BeforeDamageCalculated"]}}},
    "ReduceDamageTaken15": {"id": 510, "effect": {"kind": "ReduceDamageTaken", "multiplier": "0.85", "condition": None,
                                                  "relation": {"kinds": ["Damage"], "phases": ["BeforeDamageCalculated"]}}},
}


def one_battle(knowledge, status_types=None):
    if status_types is not None:
        knowledge.raw["status_effects"] = status_types
    sim = battle_sim.Simulator(knowledge, STATE)
    stage = sim.clan_boss_stage("Nightmare", "Void")["stage"]
    hero, boss = sim.fresh_copy(sim.team_fighters([{"champion": "Poisoner"}]) + [sim.boss_fighter(stage, None)])
    battle = battle_sim.Battle(sim, [hero, boss], 1, record=False)
    return sim, battle, hero, boss, battle_sim.SkillContext(hero, hero.skills[0], boss)


def test_weaken_and_strengthen_use_the_games_own_formulas(knowledge):
    sim, battle, hero, boss, context = one_battle(knowledge, STATUS_TYPES)
    boss.statuses.append(battle_sim.Status("IncreaseDamageTaken25", 2, hero, False))
    assert battle.status_multiplier(hero, boss, HIT, context) == pytest.approx(1.3)
    # Two damage-multiplying statuses multiply, as the processor does to the hit's base value.
    boss.statuses.append(battle_sim.Status("ReduceDamageTaken15", 2, boss, True))
    assert battle.status_multiplier(hero, boss, HIT, context) == pytest.approx(1.3 * 0.85)
    # Only the effect kinds in the status's relation: poison ticks aren't Damage.
    assert battle.status_multiplier(hero, boss, POISON_TICK, context) == 1.0
    assert not sim.fallback_statuses


def test_a_damage_taken_status_only_changes_the_damage_its_holder_takes(knowledge):
    _, battle, hero, boss, context = one_battle(knowledge, STATUS_TYPES)
    hero.statuses += [battle_sim.Status("IncreaseDamageTaken25", 2, boss, False), battle_sim.Status("ReduceDamageTaken15", 2, hero, True)]
    assert battle.status_multiplier(hero, boss, HIT, context) == 1.0


def test_older_knowledge_files_fall_back_to_the_share_in_the_name(knowledge):
    sim, battle, hero, boss, context = one_battle(knowledge)
    boss.statuses.append(battle_sim.Status("IncreaseDamageTaken15", 2, hero, False))
    assert battle.status_multiplier(hero, boss, HIT, context) == pytest.approx(1.15)
    assert sim.fallback_statuses == {"IncreaseDamageTaken15"}


def test_freeze_invisible_and_stone_skin_reduce_damage_as_the_game_does():
    assert rules.status_reduction({"Freeze"}, False, "Damage", {}) == (0.75, [])
    assert rules.status_reduction({"Invisible2"}, True, "Damage", {})[0] == pytest.approx(0.85)
    assert rules.status_reduction({"Invisible2"}, False, "Damage", {})[0] == 1.0
    assert rules.status_reduction({"Freeze", "Invisible1"}, True, "Damage", {})[0] == pytest.approx(0.675)
    factors = {"SimpleStoneSkin": {"stone_skin": 0.5, "stone_skin_hp_burn": 0.2}}
    assert rules.status_reduction({"SimpleStoneSkin"}, False, "AoEContinuousDamage", factors)[0] == pytest.approx(0.2)
    assert rules.status_reduction({"Freeze", "SimpleStoneSkin"}, False, "Damage", factors)[0] == pytest.approx(0.375)
    assert rules.status_reduction({"Petrification"}, False, "Damage", {}) == (1.0, ["Petrification"])


# Passives and masteries


WARMASTER = skill("Warmaster", targets=None, effects=[{
    **effect("Damage", "RelationTarget", "0.1*TRG_HP", chance=1.0, condition="isOwnerProduceRelatedEffect"),
    "id": 5001632, "passive": True, "unique": True,
    "relation": {"effect_id": None, "kinds": ["Damage"], "phases": ["AfterDamageDealt"]},
}])
MAX_HP_CAP = effect("ChangeDamageMultiplier", "Owner", "-(DMG_MUL-75000)",
                    condition="reletionEffectScalesByTargetHp&&DMG_MUL>=75000&&relatedEffectId==5001632")


def with_warmaster(knowledge):
    knowledge.raw["skills"]["500163"] = WARMASTER
    knowledge.raw["skills"]["907"] = skill("Infernal Resilience [P]", targets=None, effects=[MAX_HP_CAP])
    boss_type = next(t for t in knowledge.raw["champion_types"] if t["type_id"] == 9000)
    boss_type["skill_ids"] = boss_type["skill_ids"] + [907]
    knowledge.types[9000] = boss_type
    state = json.loads(json.dumps(STATE))
    state["champions"][0]["masteries"] = ["500163", "500111"]
    return state


def test_a_mastery_hits_once_per_skill_and_the_boss_caps_it(knowledge):
    state = with_warmaster(knowledge)
    team = [{"champion": "Poisoner", "priority": ["A1"], "never": ["A2", "A3"]}]
    result = battle_sim.simulate_clan_boss(knowledge, state, team, "Nightmare", "Void", runs=1, boss_turn_limit=6)
    turns = [entry for entry in result["timeline"] if entry["who"] == "Poisoner" and entry["skill"] == "A1 Jab"]
    assert turns
    for entry in turns:
        assert [event for event in entry["events"] if event.startswith("Warmaster")] == ["Warmaster Demon Lord 75000"]
    assert not any("Warmaster" in item for item in result["not_simulated"].get("Poisoner", []))


def test_a_once_per_turn_mastery_hits_once_however_many_hits_set_it_off(knowledge):
    state = with_warmaster(knowledge)
    knowledge.raw["skills"]["500163"]["effects"][0].update(
        unique=False, condition="isOwnerProduceRelatedEffect&&!relationTargetIsAlly&&canUniqueApplyForTargetCurrentTurn")
    knowledge.raw["skills"]["11"]["effects"][0]["count"] = 3
    team = [{"champion": "Poisoner", "priority": ["A1"], "never": ["A2", "A3"]}]
    result = battle_sim.simulate_clan_boss(knowledge, state, team, "Nightmare", "Void", runs=1, boss_turn_limit=4)
    turns = [entry for entry in result["timeline"] if entry["who"] == "Poisoner" and entry["skill"] == "A1 Jab"]
    assert turns
    for entry in turns:
        assert len([event for event in entry["events"] if event.startswith("Warmaster")]) == 1
    assert not any("Warmaster" in item for item in result["not_simulated"].get("Poisoner", []))


def test_relation_variables_name_the_hit_target(knowledge):
    _, battle, hero, boss, context = one_battle(knowledge)
    assert battle_sim.Resolver(battle, hero, boss, context).value("relationTargetIsAlly") == 0
    assert battle_sim.Resolver(battle, hero, hero, context).value("relationTargetIsAlly") == 1
    assert battle_sim.Resolver(battle, hero, boss, context).value("REL_TRG_HP") == boss.max_hp
    assert battle_sim.Resolver(battle, hero, boss, context, related_producer=boss).value("relationProducerIsAlly") == 0


def test_a_random_hit_count_above_a_wave_means_each_enemy_once(knowledge):
    knowledge.raw["skills"]["11"]["effects"][0].update(target="RandomEnemy", count=20)
    _, battle, hero, boss, _ = one_battle(knowledge)
    context = battle_sim.SkillContext(hero, hero.skills[0], boss)
    battle.apply_effect(hero.skills[0].effects[0], context, [hero], [boss], notes := [])
    assert len([note for note in notes if "Demon Lord" in note]) == 1


def test_masteries_in_an_older_knowledge_file_are_reported(knowledge):
    state = json.loads(json.dumps(STATE))
    state["champions"][0]["masteries"] = ["500163"]
    result = battle_sim.simulate_clan_boss(knowledge, state, [{"champion": "Poisoner"}], "Nightmare", "Void", runs=1, boss_turn_limit=2)
    assert any("predates" in item for item in result["not_simulated"]["Poisoner"])


def test_turn_variables_know_whose_turn_it_is(knowledge):
    _, battle, hero, boss, context = one_battle(knowledge)
    battle.turn_owner = boss
    assert battle_sim.Resolver(battle, hero, None, context).value("isOwnersTurn") == 0
    assert battle_sim.Resolver(battle, hero, None, context).value("isAllyTurn") == 0
    battle.turn_owner = hero
    assert battle_sim.Resolver(battle, hero, None, context).value("isOwnersTurn") == 1


def test_an_active_set_grants_its_skill(knowledge):
    knowledge.raw["set_skills"] = {"Poison": [{"pieces": 4, "skill_id": 600001}]}
    knowledge.raw["skills"]["600001"] = skill("Toxic", targets=None, effects=[{
        **effect("ApplyDebuff", "RelationTarget", statuses=[("ContinuousDamage025p", 2)], chance=1.0,
                 condition="isOwnerProduceRelatedEffect"),
        "id": 6000011, "passive": True, "relation": {"effect_id": None, "kinds": ["Damage"], "phases": ["AfterDamageDealt"]},
    }])
    state = json.loads(json.dumps(STATE))
    state["champions"][2]["calculated_stats"]["sets"] = [
        {"set": "Poison", "name": "Toxic", "pieces": 4, "active": True, "series": False, "skill_effect": True}]
    result = battle_sim.simulate_clan_boss(knowledge, state, [{"champion": "Tank"}], "Nightmare", "Void", runs=1, boss_turn_limit=4)
    assert result["debuffs"]["ContinuousDamage025p"]["landed"] > 0
    assert "Tank" not in result["not_simulated"]


def test_a_blessing_grants_the_skill_for_the_highest_grade_reached(knowledge):
    knowledge.raw["blessing_skills"] = {"1301": [{"grade": 1, "skill_id": 700101}, {"grade": 4, "skill_id": 700104}]}
    for skill_id, name in (("700101", "Blessed I"), ("700104", "Blessed IV")):
        knowledge.raw["skills"][skill_id] = skill(name, targets=None, effects=[{**effect("Heal", "Owner", "1"), "passive": True}])
    sim = battle_sim.Simulator(knowledge, STATE)
    champion = {"blessing_id": "1301", "awakening_level": 3}
    assert {p["skill_name"] for p in sim.blessing_passives(champion)} == {"Blessed I"}
    assert {p["skill_name"] for p in sim.blessing_passives({**champion, "awakening_level": 6})} == {"Blessed IV"}
    assert sim.blessing_passives({**champion, "awakening_level": 0}) == []


def test_immunities_come_only_from_the_immune_to_sentences(knowledge):
    knowledge.raw["skills"]["904"]["description"] = (
        "Decreases the damage the Demon Lord receives from [Poison] debuffs.\n\nImmune to [Stun] and [Freeze] debuffs.")
    immune = battle_sim.Simulator(knowledge, STATE).immunities(knowledge.types[9000])
    assert {"Stun", "Freeze"} <= immune
    assert "ContinuousDamage" not in immune


def test_a_passive_tied_to_one_effect_fires_only_after_that_effect(knowledge):
    knowledge.raw["skills"]["11"]["effects"][0]["id"] = 111
    knowledge.raw["skills"]["13"] = skill("Rally [P]", targets=None, effects=[{
        **effect("ApplyBuff", "Owner", statuses=[("IncreaseAttack25", 1)]), "passive": True,
        "relation": {"effect_id": 999, "kinds": [], "phases": ["AfterDamageDealt"]}}])
    _, battle, hero, boss, context = one_battle(knowledge)
    battle.apply_effect(hero.skills[0].effects[0], context, [hero], [boss], [])
    assert not hero.has("IncreaseAttack")
    hero.passives[0]["relation"]["effect_id"] = 111
    battle.apply_effect(hero.skills[0].effects[0], context, [hero], [boss], [])
    assert hero.has("IncreaseAttack")


def test_a_damage_passive_changes_only_the_damage_kinds_it_names(knowledge):
    knowledge.raw["skills"]["904"]["effects"] = [{
        **effect("ChangeDamageMultiplier", "Owner", "-0.9*DMG_MUL", condition="ownerIsRelatedEffectTarget"),
        "passive": True, "relation": {"effect_id": None, "kinds": ["ContinuousDamage"], "phases": ["BeforeDamageCalculated"]}}]
    _, battle, hero, boss, _ = one_battle(knowledge)
    assert battle.passive_damage(hero, boss, 1000, HIT, False, kind="Damage") == pytest.approx(1000)
    assert battle.passive_damage(hero, boss, 1000, None, False, kind="ContinuousDamage") == pytest.approx(100)


def test_a_passive_hit_on_its_owner_sees_the_attacker_and_the_damage(knowledge):
    sim = battle_sim.Simulator(knowledge, STATE)
    owner, attacker = sim.fresh_copy(sim.team_fighters([{"champion": "Tank"}, {"champion": "Poisoner"}]))
    attacker.statuses.append(battle_sim.Status("Invisible2", 2, attacker, True))
    battle = battle_sim.Battle(sim, [owner, attacker], 1, record=False)
    passive = battle_sim.Skill("P", "passive", 0, 0, 0, None, [])
    context = battle_sim.SkillContext(actor=owner, skill=passive, chosen=attacker, passive=True, owner_produces=False,
                                      dealt=1234.0)
    resolver = battle_sim.Resolver(battle, owner, owner, context)
    assert resolver.call("RelationProducerHasEffectOfKind", ["Invisible_KindId"]) == 1.0
    assert resolver.value("DEALT_DMG") == 1234.0


def test_a_selected_removal_takes_only_the_listed_statuses_and_moves_each_one_on(knowledge):
    knowledge.raw["status_effects"] = {"ContinuousDamage5p": {"id": 80}, "DecreaseAttack50": {"id": 140}}
    sim = battle_sim.Simulator(knowledge, STATE)
    owner, enemy = sim.fresh_copy(sim.team_fighters([{"champion": "Tank"}, {"champion": "Poisoner"}]))
    battle = battle_sim.Battle(sim, [owner, enemy], 1, record=False)
    for name in ("ContinuousDamage5p", "DecreaseAttack50", "ContinuousDamage5p"):
        owner.statuses.append(battle_sim.Status(name, 2, enemy, False))
    remove = {**effect("RemoveDebuff", "Owner"), "id": 7, "passive": True,
              "params": {"UnapplyStatusEffectParams.Count": "-1", "UnapplyStatusEffectParams.UnapplyMode": "Selected",
                         "UnapplyStatusEffectParams.StatusEffectTypeIds": "80"}}
    move = {**effect("ApplyDebuff", "AllEnemies", statuses=[("ContinuousDamage5p", 2)]), "id": 8, "passive": True,
            "condition": "relationTypeId==ContinuousDamage5p",
            "relation": {"effect_id": 7, "kinds": [], "phases": ["AfterEffectUnappliedFromHero"]}}
    passive = battle_sim.Skill("Enemy of Mortality", "passive", 0, 0, 0, None, [remove, move])
    context = battle_sim.SkillContext(actor=owner, skill=passive, chosen=owner, passive=True)
    applied = []
    real = battle.apply_effect
    battle.apply_effect = lambda e, *rest: applied.append(e["id"]) if e["id"] == 8 else real(e, *rest)
    real(remove, context, [owner], [enemy], [])
    assert [s.name for s in owner.statuses] == ["DecreaseAttack50"]
    assert applied == [8, 8]


def test_type_and_count_checks_on_the_target(knowledge):
    sim = battle_sim.Simulator(knowledge, STATE)
    owner, target = sim.fresh_copy(sim.team_fighters([{"champion": "Tank"}, {"champion": "Poisoner"}]))
    battle = battle_sim.Battle(sim, [owner, target], 1, record=False)
    target.statuses += [battle_sim.Status("ContinuousDamage5p", 2, owner, False),
                        battle_sim.Status("ContinuousDamage025p", 2, owner, False)]
    context = battle_sim.SkillContext(actor=owner, skill=battle_sim.Skill("A1", "A1", 0, 0, 0, None, []), chosen=target)
    resolver = battle_sim.Resolver(battle, owner, target, context)
    assert resolver.call("TargetHasEffectOfType", ["ContinuousDamage025p"]) == 1.0
    assert resolver.call("TargetHasEffectOfType", ["Burn"]) == 0.0
    assert resolver.call("EffectsAppliedOnTargetCountOfKind", ["ContinuousDamage_KindId"]) == 2.0


def test_trial_conditions_read_the_hitters_buffs_and_rule_out_ally_attacks(knowledge):
    sim = battle_sim.Simulator(knowledge, STATE)
    owner, hitter = sim.fresh_copy(sim.team_fighters([{"champion": "Tank"}, {"champion": "Poisoner"}]))
    battle = battle_sim.Battle(sim, [owner, hitter], 1, record=False)
    hitter.statuses += [battle_sim.Status("BlockDebuff", 2, hitter, True), battle_sim.Status("IncreaseAttack25", 2, hitter, True)]
    context = battle_sim.SkillContext(actor=owner, skill=battle_sim.Skill("P", "passive", 0, 0, 0, None, []), chosen=hitter,
                                      passive=True, owner_produces=False)
    resolver = battle_sim.Resolver(battle, owner, owner, context)
    condition = ("RelationProducerHasEffectOfKind(BlockDebuff_KindId)&&RelationProducerBuffCount>=2&&"
                 "((effectThatCausedRelationSkillKindId!=TeamAttack_KindId)||producerIdThatCausedRelationSkill==1)&&"
                 "!relatedEffectIsInCounterattack")
    assert battle_sim.formula.run(condition, resolver) == 1.0


def test_relation_target_is_boss(knowledge):
    _, battle, hero, boss, context = one_battle(knowledge)
    assert battle_sim.Resolver(battle, hero, boss, context).value("relationTargetIsBoss") == 1.0
    assert battle_sim.Resolver(battle, hero, hero, context).value("relationTargetIsBoss") == 0.0


def test_the_clever_brain_prefers_a_kill_then_low_hp_and_debuffs_and_skips_a_wasted_hit(knowledge):
    sim = battle_sim.Simulator(knowledge, STATE)
    a, b = sim.fresh_copy(sim.team_fighters([{"champion": "Tank"}, {"champion": "Poisoner"}]))
    battle = battle_sim.Battle(sim, [a, b], 1, record=False)
    skill = battle_sim.Skill("Bite", "A1", 0, 0, 0, "Target", [effect("Damage", "Target", "1*ATK")])
    battle.potential_damage = lambda actor, s, target: 100.0
    for f in (a, b):
        f.stats["health"] = f.hp = 1000.0
        f.affinity = None
    # Equal targets: the first one wins the tie.
    assert battle.clever_target(a, skill, [a, b]) is a
    b.statuses.append(battle_sim.Status("DecreaseDefence60", 2, a, False))
    assert battle.clever_target(a, skill, [a, b]) is b
    a.hp = 50.0  # 100 potential > 1.2 x 50: Decisive Blow, and low HP adds 80 - 5
    assert battle.clever_score(a, skill, a) == pytest.approx(10 + 150 + 75)
    a.statuses.append(battle_sim.Status("Unkillable", 1, a, True))
    assert battle.clever_score(a, skill, a) == pytest.approx(10 + 150 + 75 - 250)
    assert battle.clever_target(a, skill, [a, b]) is b
