"""Gear optimisation for Trogly: turns a plain request into an Agent `--optimize-gear` call and explains the result.

Stdlib only, so it can be tested without an MCP runtime; `artifacts_mcp.py` is the MCP wrapper. This plugin depends
on trogly-core, which owns the instance's data: the account read in `data/state/trogly-state.json` and the Agent
location saved in `data/config.json`. This plugin only reads those files and never writes them.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

STATE_RELATIVE_PATH = Path("data") / "state" / "trogly-state.json"
CONFIG_RELATIVE_PATH = Path("data") / "config.json"
REQUEST_RELATIVE_PATH = Path("data") / "tmp" / "optimize-request.json"
AGENT_TIMEOUT_SECONDS = 180
DEFAULT_TIME_LIMIT_SECONDS = 15
MAX_RESULTS = 5

# Accepted names for each stat, mapped to the Agent's names. Crit stats are given as percentages (100 = 100%).
STAT_ALIASES = {
    "health": ("hp", "health"),
    "attack": ("atk", "attack"),
    "defence": ("def", "defence", "defense"),
    "speed": ("spd", "speed"),
    "resistance": ("res", "resistance"),
    "accuracy": ("acc", "accuracy"),
    "critical_chance": ("c.rate", "crit rate", "crit_rate", "critical_chance", "cr", "critical rate"),
    "critical_damage": ("c.dmg", "crit damage", "crit_damage", "critical_damage", "cd", "critical damage"),
}
PERCENT_STATS = {"critical_chance", "critical_damage"}
STAT_LABELS = {
    "health": "HP", "attack": "ATK", "defence": "DEF", "speed": "SPD", "resistance": "RES", "accuracy": "ACC",
    "critical_chance": "C.RATE", "critical_damage": "C.DMG",
}
SHORT_STAT = {
    "Health": "HP", "Attack": "ATK", "Defence": "DEF", "Speed": "SPD", "Resistance": "RES", "Accuracy": "ACC",
    "CriticalChance": "C.RATE", "CriticalDamage": "C.DMG", "CriticalHeal": "C.HEAL", "IgnoreDefence": "IGN.DEF",
}
SLOT_NAMES = {"Cloak": "Amulet"}

NOTE = (
    "Stats are exact from Trogly's calculator but not yet verified against the in-game screen. Nothing was changed "
    "in the game; the player moves the gear themselves."
)


class TroglyError(Exception):
    """A problem the player can act on."""


def instance_home() -> Path:
    return Path(os.environ.get("TROGLY_HOME") or Path.cwd())


def stat_name(name: str) -> str:
    key = name.strip().lower()
    for canonical, aliases in STAT_ALIASES.items():
        if key in aliases:
            return canonical
    known = ", ".join(sorted({alias for aliases in STAT_ALIASES.values() for alias in aliases if "_" not in alias}))
    raise TroglyError(f"'{name}' is not a stat Trogly can optimise. Use one of: {known}.")


def stat_limits(limits: dict[str, float] | None) -> dict[str, float]:
    converted = {}
    for name, value in (limits or {}).items():
        stat = stat_name(name)
        converted[stat] = float(value) / 100 if stat in PERCENT_STATS else float(value)
    return converted


def optimize_gear(
    champion: str,
    maximize: str,
    minimum: dict[str, float] | None = None,
    maximum: dict[str, float] | None = None,
    require_sets: dict[str, int] | None = None,
    allowed_sets: list[str] | None = None,
    use_other_champions_gear: bool = False,
    never_take_from: list[str] | None = None,
    exclude_artifacts: list[str] | None = None,
    results: int = 3,
    time_limit_seconds: float = DEFAULT_TIME_LIMIT_SECONDS,
    home: Path | None = None,
) -> dict[str, Any]:
    home = home or instance_home()
    state = _load_state(home)
    target = _resolve_champion(state, champion)
    protected = [_resolve_champion(state, name)["id"] for name in never_take_from or []]
    request = {
        "champion_id": target["id"],
        "maximize": stat_name(maximize),
        "min": stat_limits(minimum),
        "max": stat_limits(maximum),
        "require_sets": dict(require_sets or {}),
        "allowed_sets": list(allowed_sets) if allowed_sets else None,
        "pool": "everything" if use_other_champions_gear else "vault_and_own",
        "never_take_from": protected,
        "exclude_artifacts": [str(artifact_id) for artifact_id in exclude_artifacts or []],
        "results": max(1, min(int(results), MAX_RESULTS)),
        "time_limit_seconds": float(time_limit_seconds),
    }
    answer = _run_agent(home, request)
    artifacts = {artifact["id"]: artifact for artifact in state.get("artifacts") or []}
    loadouts = [_explain_loadout(loadout, artifacts, target) for loadout in answer.get("loadouts") or []]
    result: dict[str, Any] = {
        "champion": f"{answer.get('name')} (id {answer.get('champion_id')})",
        "request": _describe_request(request),
        "current_stats": _stat_block(answer.get("current") or {}),
        "loadouts": loadouts,
        "search": {
            "complete": answer.get("search_complete"),
            "seconds": answer.get("seconds"),
            "candidates_per_slot": answer.get("candidates_per_slot"),
        },
        "note": NOTE,
    }
    if not loadouts:
        result["message"] = (
            "No loadout from the allowed gear meets every requirement. Relax a minimum, allow more sets, or allow "
            "gear from other champions."
        )
    elif not answer.get("search_complete"):
        result["message"] = (
            "The search hit its time limit, so these are the best loadouts found, not proven best. A longer "
            "time_limit_seconds may find better."
        )
    return result


def _explain_loadout(loadout: dict[str, Any], artifacts: dict[str, dict[str, Any]], champion: dict[str, Any]) -> dict[str, Any]:
    worn = champion.get("equipped_artifact_ids") or {}
    pieces = []
    for slot in loadout.get("gear") or []:
        artifact = artifacts.get(slot["artifact_id"], {})
        pieces.append({
            "slot": SLOT_NAMES.get(slot["slot"], slot["slot"]),
            "artifact_id": slot["artifact_id"],
            "already_worn": worn.get(slot["slot"]) == slot["artifact_id"],
            "set": artifact.get("set_name") or artifact.get("set"),
            "stars": artifact.get("rank"),
            "level": artifact.get("level"),
            "main_stat": _bonus_text(artifact["primary_stat"]) if artifact.get("primary_stat") else None,
            "substats": [_bonus_text(bonus) for bonus in artifact.get("substats") or []],
            "taken_from": slot.get("taken_from"),
        })
    return {
        "stats": _stat_block(loadout.get("stats") or {}),
        "change": _stat_change(loadout.get("change") or {}),
        "pieces_to_change": sum(1 for piece in pieces if not piece["already_worn"]),
        "pieces": pieces,
        "active_sets": {
            (entry.get("name") or entry.get("set")): entry.get("active_stats") or []
            for entry in loadout.get("sets") or []
            if entry.get("active")
        },
    }


def _describe_request(request: dict[str, Any]) -> dict[str, Any]:
    def shown(stat: str, value: float) -> str:
        return f"{value * 100:g}%" if stat in PERCENT_STATS else f"{value:g}"

    return {
        "maximize": STAT_LABELS[request["maximize"]],
        "at_least": {STAT_LABELS[stat]: shown(stat, value) for stat, value in request["min"].items()},
        "at_most": {STAT_LABELS[stat]: shown(stat, value) for stat, value in request["max"].items()},
        "required_sets": request["require_sets"],
        "allowed_sets": request["allowed_sets"],
        "gear_pool": "vault, plus gear on other champions" if request["pool"] == "everything" else "vault and own gear",
        "protected_champions": request["never_take_from"],
    }


def _run_agent(home: Path, request: dict[str, Any]) -> dict[str, Any]:
    agent = _agent_path(home)
    request_path = home / REQUEST_RELATIVE_PATH
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    command = ["dotnet", "run", "--project", str(agent), "--"] if agent.suffix == ".csproj" else [str(agent)]
    try:
        completed = subprocess.run(
            [*command, "--optimize-gear", str(request_path)],
            capture_output=True, text=True, timeout=AGENT_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TroglyError(f"The Agent did not answer within {AGENT_TIMEOUT_SECONDS} seconds.") from error
    try:
        answer = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        tail = "\n".join((completed.stderr or completed.stdout).strip().splitlines()[-6:])
        raise TroglyError(
            f"The Agent returned something Trogly couldn't read. It may be older than this plugin.\n{tail}"
        ) from error
    if "error" in answer:
        raise TroglyError(answer["error"])
    return answer


def _agent_path(home: Path) -> Path:
    configured = os.environ.get("TROGLY_AGENT_PATH") or _read_json(home / CONFIG_RELATIVE_PATH).get("agent_path", "")
    if not configured:
        raise TroglyError(
            "Trogly doesn't know where the Agent is yet. Ask the player where Trogly.Agent.exe is installed and save "
            "it with trogly-core's set_agent_path tool."
        )
    agent = Path(configured)
    if not agent.exists():
        raise TroglyError(f"The Trogly Agent is no longer at {agent}. Update it with set_agent_path.")
    return agent


def _load_state(home: Path) -> dict[str, Any]:
    path = home / STATE_RELATIVE_PATH
    if not path.exists():
        raise TroglyError("No account data yet. Refresh the account with trogly-core's refresh_account first.")
    return _read_json(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def _resolve_champion(state: dict[str, Any], reference: str) -> dict[str, Any]:
    key = str(reference).strip().lower()
    champions = state.get("champions") or []
    by_id = [c for c in champions if c.get("id") == key]
    exact = [c for c in champions if c.get("name", "").lower() == key]
    partial = [c for c in champions if key in c.get("name", "").lower()]
    matches = by_id or exact or partial
    if not matches:
        raise TroglyError(f"No champion matches '{reference}'.")
    if len({c["name"] for c in matches}) > 1:
        names = ", ".join(sorted({c["name"] for c in matches})[:8])
        raise TroglyError(f"'{reference}' matches several champions ({names}). Use the full name or the id.")
    # Several copies of one champion: the most built.
    return sorted(matches, key=lambda c: (-c.get("stars", 0), -c.get("level", 0), -len(c.get("equipped_artifact_ids") or {})))[0]


def _stat_block(stats: dict[str, Any]) -> dict[str, Any]:
    shown = {}
    for stat, label in STAT_LABELS.items():
        if stat in stats:
            value = float(stats[stat])
            shown[label] = f"{round(value * 100)}%" if stat in PERCENT_STATS else round(value)
    return shown


def _stat_change(change: dict[str, Any]) -> dict[str, Any]:
    shown = {}
    for stat, label in STAT_LABELS.items():
        if stat in change:
            value = float(change[stat])
            shown[label] = f"{value * 100:+.0f}%" if stat in PERCENT_STATS else f"{value:+,.0f}"
    return shown


def _bonus_text(bonus: dict[str, Any]) -> str:
    stat = bonus.get("stat", "")
    label = SHORT_STAT.get(stat, stat)
    value = float(bonus.get("value", 0)) + float(bonus.get("glyph") or 0)
    if not bonus.get("is_absolute", True) or stat in ("CriticalChance", "CriticalDamage"):
        return f"{label} {round(value * 100, 1):g}%"
    return f"{label} {round(value):,}"
