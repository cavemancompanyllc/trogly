"""Clan Boss team suggestions, side-by-side simulations, the HTML report, and the optional local write-up.

Team suggestions are code: champions are scored on what their kits do against a boss (Decrease DEF, Weaken, Decrease
ATK, Poison, HP Burn, Unkillable, Block Damage, ...), every five-champion team from the best of them is played a few
times, and the strongest distinct teams are played in full. AI only writes the analysis, in one of three modes:
"claude" (the session reads the digest), "ollama" (the local model writes it, so Claude reads only the result), or
"opus" (the session hands the digest to the battle-analyst agent). Stdlib only.
"""

from __future__ import annotations

import itertools
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import battle_sim

TEMPLATE = Path(__file__).resolve().parent / "report_template.html"
REPORT_RELATIVE_PATH = Path("data") / "reports" / "battle-report.html"
ANALYSIS_MODES = ("claude", "ollama", "opus")
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT_SECONDS = 300

# What a kit brings against a boss, by status (on the boss or on the team) and by effect kind.
BOSS_DEBUFF_VALUE = {
    "DecreaseDefence60": 3.0, "DecreaseDefence30": 1.5, "IncreaseDamageTaken25": 3.0, "IncreaseDamageTaken15": 1.5,
    "DecreaseAttack50": 3.0, "DecreaseAttack25": 1.5, "ContinuousDamage5p": 3.0, "ContinuousDamage025p": 1.0,
    "Burn": 2.5, "DecreaseCriticalChance30": 0.5, "DecreaseAccuracy50": 0.5,
}
TEAM_BUFF_VALUE = {
    "Unkillable": 4.0, "BlockDamage": 3.0, "IncreaseAttack50": 1.5, "IncreaseAttack25": 1.0, "IncreaseCriticalDamage30": 1.0,
    "IncreaseCriticalChance30": 1.0, "IncreaseSpeed30": 2.0, "IncreaseSpeed15": 1.0, "Shield": 1.5, "ReduceDamageTaken25": 1.5,
    "ReduceDamageTaken15": 1.0, "ContinuousHeal15p": 1.5, "ContinuousHeal075p": 1.0, "ShareDamage50": 1.0, "Counterattack": 1.0,
    "IncreaseDefence60": 1.0, "BlockDebuff": 1.0,
}
KIND_VALUE = {"Heal": 1.0, "IncreaseStamina": 1.0, "ReduceCooldown": 1.0, "Revive": 0.5, "ExtraTurn": 0.5}

DEFAULT_POOL = 11
DEFAULT_SCREEN_RUNS = 2
MAX_SHARED = 3


class ReportError(Exception):
    """A problem with the request the player or Claude can fix."""


def kit_value(knowledge: Any, champion_type: dict[str, Any]) -> tuple[float, list[str]]:
    """How much a kit brings to a boss fight, and the reasons, from the game's skill data."""
    score, reasons = 0.0, set()
    for skill_id in champion_type.get("skill_ids") or []:
        skill = knowledge.skills.get(str(skill_id)) or {}
        for effect in skill.get("effects") or []:
            for status in effect.get("statuses") or []:
                name = status["name"]
                if effect["kind"] == "ApplyDebuff" and name in BOSS_DEBUFF_VALUE:
                    score += BOSS_DEBUFF_VALUE[name]
                    reasons.add(name)
                elif effect["kind"] == "ApplyBuff" and name in TEAM_BUFF_VALUE:
                    score += TEAM_BUFF_VALUE[name]
                    reasons.add(name)
            if effect["kind"] in KIND_VALUE:
                score += KIND_VALUE[effect["kind"]]
                reasons.add(effect["kind"])
    return score, sorted(reasons)


def boss_candidates(knowledge: Any, roster: dict[str, Any], pool: int = DEFAULT_POOL, min_stars: int = 5,
                    exclude: list[str] | None = None, include: list[str] | None = None) -> list[dict[str, Any]]:
    """The roster's best boss champions: one copy each (most built), ranked by kit value, then by ATK as a tiebreak."""
    excluded = {name.lower() for name in exclude or []}
    best: dict[str, dict[str, Any]] = {}
    for champion in roster.get("champions") or []:
        name = champion.get("name") or ""
        stats = (champion.get("calculated_stats") or {}).get("total") or {}
        if champion.get("stars", 0) < min_stars or not stats or name.lower() in excluded:
            continue
        current = best.get(name)
        if current is None or (champion.get("level", 0), champion.get("stars", 0)) > (current.get("level", 0), current.get("stars", 0)):
            best[name] = champion
    scored = []
    for name, champion in best.items():
        champion_type = knowledge.types.get(champion["type_id"]) or {}
        value, reasons = kit_value(knowledge, champion_type)
        attack = ((champion.get("calculated_stats") or {}).get("total") or {}).get("attack", 0)
        scored.append({"champion": name, "kit_value": value, "brings": reasons, "attack": attack})
    scored.sort(key=lambda c: (-c["kit_value"], -c["attack"]))
    forced = [c for c in scored if c["champion"].lower() in {n.lower() for n in include or []}]
    rest = [c for c in scored if c not in forced]
    return (forced + rest)[:max(pool, len(forced))]


def suggest_teams(knowledge: Any, roster: dict[str, Any], count: int = 5, runs: int = 10, difficulty: str = "Nightmare",
                  affinity: str = "Void", pool: int = DEFAULT_POOL, screen_runs: int = DEFAULT_SCREEN_RUNS,
                  include: list[str] | None = None, exclude: list[str] | None = None, seed: int = 1,
                  boss_stats: dict[str, float] | None = None) -> dict[str, Any]:
    """Plays every five-champion team from the best `pool` boss champions `screen_runs` times, keeps the `count`
    strongest teams that share at most three champions with each other, and plays those `runs` times each."""
    count = max(1, min(int(count), 10))
    candidates = boss_candidates(knowledge, roster, max(5, min(int(pool), 14)), exclude=exclude, include=include)
    if len(candidates) < 5:
        raise ReportError("Fewer than five built champions to choose from; lower min_stars or refresh the account.")
    names = [c["champion"] for c in candidates]
    required = {n.lower() for n in include or []}
    screened = []
    for combo in itertools.combinations(names, 5):
        if required and not required <= {n.lower() for n in combo}:
            continue
        team = [{"champion": n} for n in order_team(knowledge, roster, combo)]
        result = battle_sim.simulate_clan_boss(knowledge, roster, team, difficulty, affinity, runs=screen_runs, seed=seed,
                                               boss_stats=boss_stats)
        screened.append((result["damage"]["mean"], team))
    screened.sort(key=lambda item: -item[0])
    chosen: list[list[dict[str, Any]]] = []
    for _, team in screened:
        members = {m["champion"] for m in team}
        if all(len(members & {m["champion"] for m in other}) <= MAX_SHARED for other in chosen):
            chosen.append(team)
        if len(chosen) == count:
            break
    results = compare_teams(knowledge, roster, chosen, difficulty, affinity, runs, seed, boss_stats)
    results["screened_teams"] = len(screened)
    results["candidates"] = candidates
    return results


def order_team(knowledge: Any, roster: dict[str, Any], names: tuple[str, ...]) -> list[str]:
    """Leader first: the champion whose aura works at the Clan Boss and helps most (SPD, then ATK, C.RATE, ...)."""
    order = {"Speed": 0, "Attack": 1, "CriticalChance": 2, "CriticalDamage": 3, "Accuracy": 4, "Defence": 5, "Health": 6}

    def aura_rank(name: str) -> tuple[int, float]:
        champion = next((c for c in roster.get("champions") or [] if c.get("name") == name), {})
        auras = [a for a in (knowledge.types.get(champion.get("type_id")) or {}).get("leader_skills") or []
                 if battle_sim.aura_applies(a, battle_sim.CLAN_BOSS_AREA)]
        if not auras:
            return (99, 0.0)
        best = min(auras, key=lambda a: order.get(a["stat"], 50))
        return (order.get(best["stat"], 50), -best["amount"])

    return sorted(names, key=aura_rank)


def compare_teams(knowledge: Any, roster: dict[str, Any], teams: list[Any], difficulty: str = "Nightmare",
                  affinity: str = "Void", runs: int = 10, seed: int = 1,
                  boss_stats: dict[str, float] | None = None) -> dict[str, Any]:
    """Plays each team `runs` times with the same seeds, so differences come from the teams, not the dice."""
    if not teams:
        raise ReportError("Give at least one team.")
    results = []
    for index, team in enumerate(teams, start=1):
        label = None
        if isinstance(team, dict):
            label, team = team.get("label"), team.get("champions") or team.get("team") or []
        members = [m if isinstance(m, dict) else {"champion": str(m)} for m in team]
        result = battle_sim.simulate_clan_boss(knowledge, roster, members, difficulty, affinity, runs=runs, seed=seed,
                                               boss_stats=boss_stats, label=label)
        results.append(result)
    results.sort(key=lambda r: -r["damage"]["mean"])
    return {"teams": results, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}


def digest(results: dict[str, Any]) -> dict[str, Any]:
    """What an analyst needs, without the turn-by-turn timelines."""
    return {
        "encounter": results["teams"][0]["encounter"] if results.get("teams") else None,
        "teams": [{
            "rank": index,
            "team": r["team"],
            "damage": r["damage"],
            "boss_turns": r["boss_turns"],
            "reached_turn_limit": f"{r['reached_turn_limit']} of {r['runs']}",
            "first_death": r["first_death"],
            "damage_by_champion": r["damage_by_champion"],
            "debuffs_on_boss": {name: {"landed": t["landed"], "resist_rate": t["resist_rate"]} for name, t in r["debuffs"].items()},
            "champions": [{"champion": c["champion"], "speed": c["stats"]["speed"], "accuracy": c["stats"]["accuracy"],
                           "skill_order": c["priority"], "coverage": c["coverage"]} for c in r["champions"]],
            "not_simulated": r["not_simulated"],
        } for index, r in enumerate(results.get("teams") or [], start=1)],
        "assumed": (results.get("teams") or [{}])[0].get("assumed", []),
    }


ANALYST_PROMPT = """You are analysing RAID: Shadow Legends Clan Boss simulations. The numbers come from a simulator that
plays the game's own rules; trust them and do not invent game mechanics. In plain English, at most 250 words:
1. Which team did best and why, citing the numbers (mean damage, spread, boss turns survived, first deaths).
2. What held the weaker teams back (deaths, resisted debuffs, missing Decrease DEF / Weaken / Decrease ATK).
3. One or two concrete changes to try next (a swap, a skill order, an ACC or SPD target).
Mention any champion whose coverage is under 85% as a reason its numbers may be low.

Results:
"""


def ollama_analysis(results: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Asks the local Ollama model for the write-up. Ollama is shared by every session: this only sends a request; it
    never loads, unloads, or restarts models itself."""
    base = os.environ.get("TROGLY_OLLAMA_BASE_URL") or OLLAMA_URL
    model = model or os.environ.get("TROGLY_OLLAMA_MODEL") or installed_model(base)
    body = json.dumps({"model": model, "prompt": ANALYST_PROMPT + json.dumps(digest(results), indent=1), "stream": False,
                       "options": {"temperature": 0.2}}).encode()
    request = urllib.request.Request(f"{base}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            text = json.load(response).get("response") or ""
    except (urllib.error.URLError, TimeoutError) as error:
        raise ReportError(f"Couldn't reach Ollama at {base} ({error}). Start Ollama or use analysis='claude'.") from error
    # Reasoning models wrap their thinking in <think> tags; only the answer goes in the report.
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return {"by": f"Ollama {model}", "text": text.strip()}


def installed_model(base: str) -> str:
    """The first chat model Ollama has installed (embedding models can't write), for when none is configured."""
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=10) as response:
            names = [m["name"] for m in json.load(response).get("models") or []]
    except (urllib.error.URLError, TimeoutError) as error:
        raise ReportError(f"Couldn't reach Ollama at {base} ({error}). Start Ollama or use analysis='claude'.") from error
    chat = [name for name in names if "embed" not in name]
    if not chat:
        raise ReportError("Ollama has no chat model installed. Pull one (for example `ollama pull qwen3:32b`) or set TROGLY_OLLAMA_MODEL.")
    return chat[0]


def write_report(results: dict[str, Any], home: Path, title: str | None = None, analysis: dict[str, Any] | None = None,
                 path: Path | None = None) -> Path:
    """Fills the report template with the results and saves it; the session publishes the file as an Artifact."""
    data = {"title": title or "Clan Boss Battle Report", "generated_at": results.get("generated_at"),
            "analysis": analysis, "teams": results["teams"]}
    # "</" can't appear inside a <script> block; the JSON escape keeps the data intact.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__TROGLY_DATA__", payload)
    if title:
        html = html.replace("<title>Clan Boss Battle Report</title>", f"<title>{escape(title)}</title>", 1)
    target = path or home / REPORT_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def finish(results: dict[str, Any], home: Path, analysis: str = "claude", title: str | None = None,
           ollama_model: str | None = None) -> dict[str, Any]:
    """Writes the report and returns what the session needs: the file to publish, plus the digest or the write-up."""
    mode = (analysis or "claude").lower()
    if mode not in ANALYSIS_MODES:
        raise ReportError(f"analysis must be one of {', '.join(ANALYSIS_MODES)}.")
    written = ollama_analysis(results, ollama_model) if mode == "ollama" else None
    report = write_report(results, home, title, written)
    answer: dict[str, Any] = {
        "report_file": str(report),
        "publish": "Publish report_file with the Artifact tool (it is a finished page; don't rewrite it).",
        "ranking": [{"rank": i, "team": r["team"], "mean_damage": r["damage"]["mean"], "p10": r["damage"]["p10"],
                     "p90": r["damage"]["p90"], "boss_turns": r["boss_turns"]["mean"]}
                    for i, r in enumerate(results["teams"], start=1)],
    }
    if mode == "ollama":
        answer["analysis"] = written
        answer["next"] = "Relay the Ollama analysis and the ranking; no further analysis is needed."
    elif mode == "opus":
        answer["digest"] = digest(results)
        answer["next"] = "Hand the digest to the battle-analyst agent and relay its write-up."
    else:
        answer["digest"] = digest(results)
        answer["next"] = "Explain the ranking from the digest."
    return answer
