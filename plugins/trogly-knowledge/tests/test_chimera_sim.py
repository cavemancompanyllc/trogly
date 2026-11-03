import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import battle_sim  # noqa: E402
import chimera_sim  # noqa: E402
from knowledge_tools import Knowledge  # noqa: E402
from test_battle_sim import KNOWLEDGE, STATE, champion_type, effect, skill  # noqa: E402

CHIMERA_STATS = {"health": 5_000_000, "attack": 800, "defence": 500, "speed": 100, "resistance": 100, "accuracy": 100,
                 "critical_chance": 0.0, "critical_damage": 0.5}


def chimera_knowledge(with_forms):
    data = json.loads(json.dumps(KNOWLEDGE))
    chimera = champion_type(26696, "Chimera", [3601, 3602], affinity="Void")
    data["skills"].update({
        "3601": skill("Torrent", effects=[effect("Damage", "AllEnemies", "1*ATK")]),
        "3602": skill("Consume", 3, [effect("RemoveBuff", "AllEnemies"),
                                     effect("Damage", "AllEnemies", "1*ATK*(1+0.1*removedEffectsCount)")],
                      description="Attacks all enemies. Before attacking, removes all buffs from all enemies."),
        "3603": skill("Ram Bash", effects=[effect("Damage", "Target", "2*ATK")]),
    })
    if with_forms:
        chimera["forms"] = [{"skill_ids": [3601, 3602]}, {"skill_ids": [3603]}]
        data["alliance_bosses"] = {"chimera_slots": 5, "chimera_form_sequence": {"2": "Ram", "4": "Ultimate"}}
    data["champion_types"].append(chimera)
    stages = [{"id": 13019001 + n, "number": n + 1, "champion_slots": 5, "has_boss": True, "modifiers": [],
               "enemies": [{"wave": 1, "type_id": 26696, "name": "Chimera", "stars": 6, "level": 100, "is_boss": False,
                            "stats": CHIMERA_STATS}]} for n in range(6)]
    data["regions"].append({"id": 1301, "code": "ChimeraRotation1", "name": "Chimera", "area": "Chimera",
                            "difficulties": {"None": stages}})
    return data


def load(tmp_path, with_forms):
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(chimera_knowledge(with_forms)),
                                                                    encoding="utf-8")
    return Knowledge.load(tmp_path)


def team(*names):
    return [{"champion": name} for name in names]


def test_the_form_follows_the_sequence_and_the_last_key_is_the_turn_limit(tmp_path):
    knowledge = load(tmp_path, with_forms=True)
    sim = chimera_sim.ChimeraSimulator(knowledge, STATE)
    assert [sim.form_for(turns) for turns in range(6)] == [1, 1, 0, 0, 0, 0]
    result = chimera_sim.simulate_chimera(knowledge, STATE, team("Tank"), "Easy", runs=1)
    assert result["turn_limit"] == 4
    assert result["boss_turns"]["max"] == 4
    assert result["form_changes"][0] == {"turn": 0, "form": "Ram"}
    assert {"turn": 2, "form": "Ultimate"} in result["form_changes"]
    chimera_turns = [entry["skill"] for entry in result["timeline"] if entry["who"] == "Chimera"]
    assert chimera_turns[:2] == ["A1 Ram Bash", "A1 Ram Bash"]
    assert not any("form changes" in item for item in result["not_simulated"]["Chimera"])


def test_a_form_change_clears_the_chimeras_buffs_and_debuffs(tmp_path):
    knowledge = load(tmp_path, with_forms=True)
    sim = chimera_sim.ChimeraSimulator(knowledge, STATE)
    chimera = sim.chimera_fighter(sim.chimera_stage("Easy", None)["stage"])
    hero, boss = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + [chimera])
    battle = chimera_sim.ChimeraBattle(sim, [hero, boss], 1, record=False)
    boss.statuses.append(battle_sim.Status("DecreaseDefence60", 2, hero, False))
    battle.change_form(1)
    assert boss.statuses == [] and [s.name for s in boss.skills] == ["Ram Bash"]


def test_without_forms_the_chimera_stays_ultimate_and_says_so(tmp_path):
    knowledge = load(tmp_path, with_forms=False)
    result = chimera_sim.simulate_chimera(knowledge, STATE, team("Tank"), "Easy", runs=1, turn_limit=3)
    assert result["form_changes"] == []
    assert any("form changes" in item for item in result["not_simulated"]["Chimera"])
    assert chimera_sim.ChimeraSimulator(knowledge, STATE).turn_limit() == (chimera_sim.ASSUMED_TURN_LIMIT, True)


def test_damage_grows_with_the_buffs_the_skill_removed(tmp_path):
    knowledge = load(tmp_path, with_forms=False)
    sim = chimera_sim.ChimeraSimulator(knowledge, STATE)
    chimera = sim.chimera_fighter(sim.chimera_stage("Easy", None)["stage"])
    hero, boss = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + [chimera])
    battle = chimera_sim.ChimeraBattle(sim, [hero, boss], 1, record=False)
    hero.statuses += [battle_sim.Status("IncreaseAttack25", 2, hero, True), battle_sim.Status("Shield", 2, hero, True)]
    consume = boss.skills[1]
    context = battle_sim.SkillContext(boss, consume, hero)
    battle.apply_effect(consume.effects[0], context, [boss], [hero], [])
    assert context.removed == 2
    assert battle_sim.Resolver(battle, boss, hero, context).value("removedEffectsCount") == 2


def test_the_chimera_dies_when_its_health_runs_out(tmp_path):
    knowledge = load(tmp_path, with_forms=False)
    sim = chimera_sim.ChimeraSimulator(knowledge, STATE)
    chimera = sim.chimera_fighter(sim.chimera_stage("Easy", None)["stage"])
    hero, boss = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + [chimera])
    battle = chimera_sim.ChimeraBattle(sim, [hero, boss], 1, record=False)
    boss.hp = 100
    assert battle.reduce_hp(boss, hero, 500) == pytest.approx(100)
    assert not boss.alive and hero.damage_dealt == pytest.approx(100)
    with pytest.raises(battle_sim.BattleError, match="Unknown Chimera difficulty"):
        sim.chimera_stage("Insane", None)


def test_removal_takes_the_exported_count_or_what_the_text_says():
    strip = battle_sim.Skill("A2", "Strip", 1, 0, 0, None, [], description="Removes all buffs from the target.")
    single = battle_sim.Skill("A2", "Peel", 1, 0, 0, None, [], description="Removes 1 random buff from the target.")
    plain = {"kind": "RemoveBuff", "count": 1}
    assert battle_sim.removal_count(plain, strip, 4) == 4
    assert battle_sim.removal_count(plain, single, 4) == 1
    assert battle_sim.removal_count({**plain, "params": {"UnapplyStatusEffectParams.Count": "2"}}, strip, 4) == 2
    assert battle_sim.removal_count({**plain, "params": {"UnapplyStatusEffectParams.Count": "0"}}, single, 4) == 4


def with_trials(data, counter_limit=None):
    """The Ultimate form gets a Trial: started at the battle's start, one step per champion hit, done at 2 steps;
    Consume hits harder for each completed Trial."""
    trial_params = {"ChallengeParams.TargetProgress": "2", "ChallengeParams.BasedOnDamage": "true"}
    if counter_limit is not None:
        trial_params["ChallengeParams.CounterLimit"] = str(counter_limit)
    data["skills"]["3701"] = skill("Trial: Wing", targets=None, effects=[
        {**effect("StartChallenge", "Owner"), "id": 3701001, "passive": True, "params": trial_params,
         "relation": {"effect_id": None, "kinds": [], "phases": ["BattleStarted"]}},
        {**effect("SetChallengeProgress", "Owner", "challengeCurrentProgressById(3701)+1",
                  condition="ownerIsRelatedEffectTarget"), "id": 3701002, "passive": True,
         "relation": {"effect_id": None, "kinds": ["Damage"], "phases": ["SetDamageChallengeProgress"]}},
        {**effect("SetChallengeCounter", "Owner", "challengeCurrentCounterById(3701)+1",
                  condition="ownerIsRelatedEffectTarget&&isCritical"), "id": 3701003, "passive": True,
         "relation": {"effect_id": None, "kinds": ["Damage"], "phases": ["SetDamageChallengeProgress"]}},
    ])
    data["skills"]["3602"]["effects"] = [effect("Damage", "AllEnemies", "1*ATK*(1+countCompletedChallenges)")]
    chimera = next(t for t in data["champion_types"] if t["type_id"] == 26696)
    chimera.setdefault("forms", [{"skill_ids": [3601, 3602]}])[0]["challenge_skill_ids"] = [3701]
    data.setdefault("alliance_bosses", {})["chimera"] = [{
        "boss": {"difficulty": "Easy"}, "competition_basic_multiplier": 1.0, "competition_advanced_multiplier": 0.25,
        "trials": [{"skill_id": 3701, "form": "Ultimate", "part": "Wing", "tier": "Easy", "statuses": []}]}]
    return data


def trial_battle(tmp_path, counter_limit=None):
    data = with_trials(chimera_knowledge(with_forms=True), counter_limit)
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(data), encoding="utf-8")
    knowledge = Knowledge.load(tmp_path)
    sim = chimera_sim.ChimeraSimulator(knowledge, STATE)
    chimera = sim.chimera_fighter(sim.chimera_stage("Easy", None)["stage"])
    hero, boss = sim.fresh_copy(sim.team_fighters(team("Poisoner")) + [chimera])
    battle = chimera_sim.ChimeraBattle(sim, [hero, boss], 1, record=False)
    battle.fire(boss, battle_sim.BATTLE_START_PHASES, [])
    return knowledge, sim, battle, hero, boss


def hit(battle, hero, boss, crit=False):
    battle.rng.random = (lambda: 0.0) if crit else (lambda: 0.99)
    jab = hero.skills[0]
    battle.apply_effect(jab.effects[0], battle_sim.SkillContext(hero, jab, boss), [hero], [boss], notes := [])
    return notes


def test_a_trial_starts_progresses_with_each_hit_and_counts_once_done(tmp_path):
    knowledge, sim, battle, hero, boss = trial_battle(tmp_path)
    trial = battle.trials[3701]
    assert trial.started and trial.progress == 0
    assert battle.encounter_value("countCompletedChallenges", boss) == 0
    hit(battle, hero, boss)
    assert trial.progress == 1 and not trial.completed
    boss.turns_taken = 4
    notes = hit(battle, hero, boss)
    assert trial.completed and trial.completed_turn == 4 and "Trial done: Trial: Wing" in notes
    hit(battle, hero, boss)
    assert trial.progress == 2  # capped at the target
    assert battle.encounter_value("countCompletedChallenges", boss) == 1
    assert battle_sim.Resolver(battle, boss, hero, None).call("challengeCompletedById", [3701.0]) == 1


def test_reaching_a_trials_counter_limit_starts_it_over(tmp_path):
    knowledge, sim, battle, hero, boss = trial_battle(tmp_path, counter_limit=1)
    trial = battle.trials[3701]
    hit(battle, hero, boss, crit=True)
    # The crit's progress step leaves it short of 2; its counter step reaches the limit, so both start over.
    assert (trial.progress, trial.counter, trial.completed) == (0, 0, False)
    hit(battle, hero, boss)
    hit(battle, hero, boss, crit=True)
    # Done before the counter step: a completed Trial's counter no longer moves (Challenge.CanChangeCounter).
    assert (trial.progress, trial.counter, trial.completed) == (2, 0, True)


def test_the_battle_reports_trials_and_the_competition_multiplier(tmp_path):
    data = with_trials(chimera_knowledge(with_forms=False))
    (tmp_path / "data" / "knowledge").mkdir(parents=True)
    (tmp_path / "data" / "knowledge" / "knowledge.json").write_text(json.dumps(data), encoding="utf-8")
    result = chimera_sim.simulate_chimera(Knowledge.load(tmp_path), STATE, team("Poisoner"), "Easy", runs=2, turn_limit=5)
    assert result["trials"]["completed_mean"] == 1
    assert result["trials"]["each"]["Trial: Wing"]["completed_runs"] == 2
    assert result["trials"]["competition_multiplier_mean"] == pytest.approx(1.25)
    assert not any(item.startswith("Trials") for item in result["not_simulated"].get("Chimera", []))
