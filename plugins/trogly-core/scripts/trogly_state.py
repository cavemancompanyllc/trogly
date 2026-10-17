"""Reads the Trogly Agent's state file and answers account questions about it.

Everything here is plain Python (stdlib only) so it can be tested without an MCP runtime; `trogly_mcp.py` is the
thin MCP wrapper. Paths are resolved from the instance root (`TROGLY_HOME`, else the current directory), never from
this file's location, because an installed plugin lives in Claude Code's plugin cache, not in the instance.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_RELATIVE_PATH = Path("data") / "state" / "trogly-state.json"
CONFIG_RELATIVE_PATH = Path("data") / "config.json"
AGENT_FILE_NAME = "Trogly.Agent.exe"
REFRESH_TIMEOUT_SECONDS = 300
STALE_AFTER_MINUTES = 30
DEFAULT_LIMIT = 25
MAX_LIMIT = 200

RARITY_NAMES = {1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary", 6: "Mythical"}

SLOT_NAMES = {"Cloak": "Amulet"}
SLOT_ORDER = ["Weapon", "Helmet", "Shield", "Gloves", "Chest", "Boots", "Ring", "Cloak", "Banner"]

STAT_SHORT = {
    "Health": "HP",
    "Attack": "ATK",
    "Defence": "DEF",
    "Speed": "SPD",
    "Resistance": "RES",
    "Accuracy": "ACC",
    "CriticalChance": "C.RATE",
    "CriticalDamage": "C.DMG",
    "CriticalHeal": "C.HEAL",
    "IgnoreDefence": "IGN.DEF",
}

# Calculated-stat keys as the Agent writes them, with how to show them.
CALCULATED_STATS = [
    ("health", "HP", False),
    ("attack", "ATK", False),
    ("defence", "DEF", False),
    ("speed", "SPD", False),
    ("critical_chance", "C.RATE", True),
    ("critical_damage", "C.DMG", True),
    ("resistance", "RES", False),
    ("accuracy", "ACC", False),
]
SORTABLE_STATS = {key for key, _, _ in CALCULATED_STATS}

KEY_RESOURCES = ["Energy", "Silver", "Gem", "Token"]

CALCULATED_NOTE = (
    "Stats are calculated by Trogly from game data and have not yet been checked against the in-game champion "
    "screen. Say so when you quote them."
)


class TroglyError(Exception):
    """A problem the user can act on, such as a missing state file or Agent."""


def instance_home() -> Path:
    return Path(os.environ.get("TROGLY_HOME") or Path.cwd())


def state_path(home: Path | None = None) -> Path:
    return (home or instance_home()) / STATE_RELATIVE_PATH


def set_name(code: str, game_name: str = "") -> str:
    """The set's in-game name when the Agent supplied one, else the game's set code split into words."""
    if not code or code == "None":
        return "No set"
    if game_name:
        return game_name
    words = []
    for char in code:
        if char.isupper() and words and not words[-1].endswith(" "):
            words.append(" ")
        words.append(char)
    return "".join(words).rstrip("0123456789")


def rarity_name(rarity: int) -> str:
    return RARITY_NAMES.get(rarity, f"Rarity {rarity}")


def format_bonus(bonus: dict[str, Any]) -> str:
    stat = bonus.get("stat", "")
    label = STAT_SHORT.get(stat, stat)
    value = float(bonus.get("value", 0))
    glyph = float(bonus.get("glyph") or 0)
    is_percent = not bonus.get("is_absolute", True) or stat in ("CriticalChance", "CriticalDamage")
    if is_percent:
        text = f"{label} {_trim(value * 100)}%"
        if glyph:
            text += f" (+{_trim(glyph * 100)}% glyph)"
    else:
        text = f"{label} {round(value):,}"
        if glyph:
            text += f" (+{round(glyph):,} glyph)"
    return text


def _trim(number: float) -> str:
    rounded = round(number, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


class AccountState:
    """One loaded state file with lookup indexes."""

    def __init__(self, raw: dict[str, Any], path: Path):
        self.raw = raw
        self.path = path
        self.champions: list[dict[str, Any]] = raw.get("champions") or []
        self.artifacts_by_id: dict[str, dict[str, Any]] = {a["id"]: a for a in raw.get("artifacts") or []}
        self.owner_by_artifact: dict[str, dict[str, Any]] = {}
        for champion in self.champions:
            for artifact_id in (champion.get("equipped_artifact_ids") or {}).values():
                self.owner_by_artifact[artifact_id] = champion

    @classmethod
    def load(cls, home: Path | None = None) -> "AccountState":
        path = state_path(home)
        if not path.exists():
            raise TroglyError(
                f"No account data yet at {path}. Run refresh_account with RAID open and logged in on this PC."
            )
        with path.open(encoding="utf-8") as handle:
            return cls(json.load(handle), path)

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        read_at = self.raw.get("last_updated")
        info: dict[str, Any] = {"read_at": read_at, "source": (self.raw.get("extraction") or {}).get("source")}
        if read_at:
            age = (now or datetime.now(timezone.utc)) - _parse_time(read_at)
            minutes = max(0, int(age.total_seconds() // 60))
            info["age_minutes"] = minutes
            if minutes > STALE_AFTER_MINUTES:
                info["warning"] = (
                    f"Account data is {minutes} minutes old. Offer to refresh before relying on it."
                )
        return info

    def account_summary(self) -> dict[str, Any]:
        account = self.raw.get("account") or {}
        resources = account.get("resources") or {}
        rarity_counts = Counter(rarity_name(c.get("rarity", 0)) for c in self.champions)
        return {
            "snapshot": self.snapshot(),
            "level": account.get("level"),
            "total_power": round(account.get("total_power") or 0),
            "champions": len(self.champions),
            "champions_by_rarity": dict(rarity_counts.most_common()),
            "six_star_champions": sum(1 for c in self.champions if c.get("stars") == 6),
            "artifacts": len(self.artifacts_by_id),
            "unequipped_artifacts": sum(1 for a in self.artifacts_by_id if a not in self.owner_by_artifact),
            "resources": {key: round(resources[key]) for key in KEY_RESOURCES if key in resources},
            "shards": account.get("shards") or {},
            "great_hall": self.raw.get("great_hall") or {},
        }

    def find_champions(
        self,
        query: str = "",
        rarity: str = "",
        min_stars: int = 0,
        sort_by: str = "speed",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        matches = [c for c in self.champions if not query or query.lower() in c.get("name", "").lower()]
        if rarity:
            matches = [c for c in matches if rarity_name(c.get("rarity", 0)).lower() == rarity.lower()]
        if min_stars:
            matches = [c for c in matches if c.get("stars", 0) >= min_stars]
        sort_key = sort_by if sort_by in SORTABLE_STATS else None
        if sort_key:
            matches.sort(key=lambda c: -_total_stat(c, sort_key))
        elif sort_by == "level":
            matches.sort(key=lambda c: (-c.get("stars", 0), -c.get("level", 0)))
        else:
            matches.sort(key=lambda c: c.get("name", ""))
        limit = _clamp_limit(limit)
        return {
            "snapshot": self.snapshot(),
            "total_matches": len(matches),
            "champions": [self.champion_row(c) for c in matches[:limit]],
            "note": CALCULATED_NOTE,
        }

    def get_champion(self, name_or_id: str) -> dict[str, Any]:
        candidates = self.resolve_champion(name_or_id)
        if not candidates:
            raise TroglyError(f"No champion matches '{name_or_id}'. Try find_champions to search by part of the name.")
        if len(candidates) > 1:
            return {
                "snapshot": self.snapshot(),
                "ambiguous": True,
                "message": f"{len(candidates)} champions match '{name_or_id}'. Ask which one, or pass its id.",
                "candidates": [self.champion_row(c) for c in candidates[:DEFAULT_LIMIT]],
            }
        champion = candidates[0]
        gear = []
        equipped = champion.get("equipped_artifact_ids") or {}
        for slot in sorted(equipped, key=lambda s: SLOT_ORDER.index(s) if s in SLOT_ORDER else len(SLOT_ORDER)):
            artifact = self.artifacts_by_id.get(equipped[slot])
            if artifact:
                gear.append(self.artifact_detail(artifact, include_owner=False))
        return {
            "snapshot": self.snapshot(),
            **self.champion_row(champion),
            "awakening_level": champion.get("awakening_level"),
            "empower_level": champion.get("empower_level"),
            "blessing_id": champion.get("blessing_id") or None,
            "mastery_count": len(champion.get("masteries") or []),
            "locked": champion.get("locked"),
            "in_storage": champion.get("in_storage"),
            "base_stats": _stat_block((champion.get("calculated_stats") or {}).get("base")),
            "sets": [_set_status(entry) for entry in (champion.get("calculated_stats") or {}).get("sets") or []],
            "gear": gear,
            "stat_warnings": (champion.get("calculated_stats") or {}).get("warnings") or [],
            "note": CALCULATED_NOTE,
        }

    def find_artifacts(
        self,
        set_code: str = "",
        slot: str = "",
        main_stat: str = "",
        min_rank: int = 0,
        min_level: int = 0,
        equipped: str = "any",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        matches = list(self.artifacts_by_id.values())
        if set_code:
            wanted = set_code.lower()
            matches = [
                a for a in matches
                if wanted in (a.get("set", "").lower(), set_name(a.get("set", ""), a.get("set_name", "")).lower())
            ]
        if slot:
            wanted_slot = {"amulet": "cloak"}.get(slot.lower(), slot.lower())
            matches = [a for a in matches if a.get("slot", "").lower() == wanted_slot]
        if main_stat:
            matches = [a for a in matches if _main_stat_matches(a, main_stat)]
        if min_rank:
            matches = [a for a in matches if a.get("rank", 0) >= min_rank]
        if min_level:
            matches = [a for a in matches if a.get("level", 0) >= min_level]
        if equipped == "yes":
            matches = [a for a in matches if a["id"] in self.owner_by_artifact]
        elif equipped == "no":
            matches = [a for a in matches if a["id"] not in self.owner_by_artifact]
        matches.sort(key=lambda a: (-a.get("rank", 0), -a.get("level", 0), -a.get("rarity", 0)))
        limit = _clamp_limit(limit)
        return {
            "snapshot": self.snapshot(),
            "total_matches": len(matches),
            "artifacts": [self.artifact_detail(a, include_owner=True) for a in matches[:limit]],
        }

    def resolve_champion(self, name_or_id: str) -> list[dict[str, Any]]:
        key = name_or_id.strip().lower()
        by_id = [c for c in self.champions if c.get("id") == key]
        if by_id:
            return by_id
        exact = [c for c in self.champions if c.get("name", "").lower() == key]
        if exact:
            # Several copies of one champion come back most-built first, so the caller can ask which copy.
            return _by_progress(exact)
        return [c for c in self.champions if key in c.get("name", "").lower()]

    def champion_row(self, champion: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": champion.get("id"),
            "name": champion.get("name"),
            "rarity": rarity_name(champion.get("rarity", 0)),
            "stars": champion.get("stars"),
            "level": champion.get("level"),
            "gear_pieces": len(champion.get("equipped_artifact_ids") or {}),
            "stats": _stat_block((champion.get("calculated_stats") or {}).get("total")),
        }

    def artifact_detail(self, artifact: dict[str, Any], include_owner: bool) -> dict[str, Any]:
        detail = {
            "id": artifact.get("id"),
            "slot": SLOT_NAMES.get(artifact.get("slot", ""), artifact.get("slot")),
            "set": set_name(artifact.get("set", ""), artifact.get("set_name", "")),
            "rarity": rarity_name(artifact.get("rarity", 0)),
            "rank": artifact.get("rank"),
            "level": artifact.get("level"),
            "main_stat": format_bonus(artifact["primary_stat"]) if artifact.get("primary_stat") else None,
            "substats": [format_bonus(b) for b in artifact.get("substats") or []],
        }
        if include_owner:
            owner = self.owner_by_artifact.get(artifact["id"])
            detail["equipped_on"] = f"{owner['name']} (id {owner['id']})" if owner else None
        return detail


def set_agent_path(path: str, home: Path | None = None) -> dict[str, Any]:
    """Saves where the player installed the Trogly Agent, after checking the file is there."""
    agent = Path(path.strip().strip('"'))
    if agent.is_dir():
        agent = agent / AGENT_FILE_NAME
    if not agent.is_file():
        raise TroglyError(f"There is no Trogly Agent at {agent}. Give the full path to {AGENT_FILE_NAME} or its folder.")
    config = _load_config(home)
    config["agent_path"] = str(agent.resolve())
    config_path = (home or instance_home()) / CONFIG_RELATIVE_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"agent_path": config["agent_path"], "saved": True}


def configured_agent_path(home: Path | None = None) -> str:
    """The Agent path: TROGLY_AGENT_PATH if set (for testing), else the one saved with set_agent_path."""
    return os.environ.get("TROGLY_AGENT_PATH") or _load_config(home).get("agent_path", "")


def _load_config(home: Path | None) -> dict[str, Any]:
    path = (home or instance_home()) / CONFIG_RELATIVE_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def refresh_account(agent_path: str, home: Path | None = None) -> dict[str, Any]:
    """Runs the Trogly Agent once to read RAID's memory into the instance's state file."""
    agent = _require_agent(agent_path)
    output = state_path(home)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [*_agent_command(agent), "--refresh-direct"]
    environment = {**os.environ, "TROGLY_STATE_OUTPUT": str(output)}
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=REFRESH_TIMEOUT_SECONDS, env=environment, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise TroglyError(f"The Agent did not finish within {REFRESH_TIMEOUT_SECONDS} seconds.") from error
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-8:])
        raise TroglyError(
            "The Agent could not read the game. Check that RAID is open and logged in on this PC (not only on a "
            f"phone).\n{tail}"
        )
    state = AccountState.load(home)
    summary = state.account_summary()
    return {
        "refreshed": True,
        "snapshot": state.snapshot(),
        "champions": summary["champions"],
        "artifacts": summary["artifacts"],
        "level": summary["level"],
        "note": (
            "If the PC client shows 'logged in from another device', the game's memory still holds the last data it "
            "received, so this refresh can be older than it looks. Ask the user if anything seems out of date."
        ),
    }


def _set_status(entry: dict[str, Any]) -> dict[str, Any]:
    status = {
        "set": entry.get("name") or set_name(entry.get("set", "")),
        "pieces": entry.get("pieces"),
        "active": entry.get("active"),
        "active_bonus_stats": entry.get("active_stats") or [],
        "skill_effect_active": entry.get("skill_effect", False),
        "description": entry.get("bonus") or None,
    }
    if entry.get("series"):
        status["kind"] = "variable set: the bonus grows with each piece"
    elif entry.get("active"):
        status["full_sets"] = entry.get("active_level")
    if entry.get("next_bonus_at"):
        status["next_bonus_at_pieces"] = entry["next_bonus_at"]
    return status


def calc_stats(
    agent_path: str,
    champion: str,
    equip: list[str] | None = None,
    remove_slots: list[str] | None = None,
    only_these: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    """Asks the Agent what a champion's stats would be with a gear change, using the game's live data."""
    state = AccountState.load(home)
    matches = state.resolve_champion(champion)
    if not matches:
        raise TroglyError(f"No champion matches '{champion}'. Try find_champions to search by part of the name.")
    if len(matches) > 1 and not all(c.get("name") == matches[0].get("name") for c in matches):
        names = ", ".join(sorted({c["name"] for c in matches})[:8])
        raise TroglyError(f"'{champion}' matches several champions ({names}). Use the full name or the id.")
    target = matches[0]
    request = {
        "champion_id": target["id"],
        "equip": [str(artifact_id) for artifact_id in equip or []],
        "remove_slots": list(remove_slots or []),
        "only_these": bool(only_these),
    }
    result = _run_agent_json(agent_path, "--calc-stats", request, home)
    current = target.get("equipped_artifact_ids") or {}
    put_on = []
    for slot in result.get("gear_after") or []:
        if current.get(slot["slot"]) == slot["artifact_id"]:
            continue
        artifact = state.artifacts_by_id.get(slot["artifact_id"])
        detail = state.artifact_detail(artifact, include_owner=False) if artifact else {"id": slot["artifact_id"]}
        put_on.append({**detail, "taken_from": slot.get("taken_from")})
    kept = {slot["slot"] for slot in result.get("gear_after") or []}
    emptied = [SLOT_NAMES.get(slot, slot) for slot in current if slot not in kept]
    return {
        "champion": f"{result.get('name')} (id {result.get('champion_id')})",
        "before": _stat_block(result.get("before")),
        "after": _stat_block(result.get("after")),
        "change": _stat_change(result.get("change") or {}),
        "pieces_put_on": put_on,
        "slots_emptied": emptied,
        "set_bonuses": _set_bonus_change(result.get("sets_before") or [], result.get("sets_after") or []),
        "sets_after": [_set_status(entry) for entry in result.get("sets_after") or []],
        "warnings": result.get("warnings") or [],
        "note": "Calculated from live game data with Trogly's stat calculator; not yet verified in game. "
        "Nothing was changed in the game.",
    }


def _run_agent_json(agent_path: str, command: str, request: dict[str, Any], home: Path | None) -> dict[str, Any]:
    agent = _require_agent(agent_path)
    request_path = (home or instance_home()) / "data" / "tmp" / "agent-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    try:
        completed = subprocess.run(
            [*_agent_command(agent), command, str(request_path)],
            capture_output=True, text=True, timeout=REFRESH_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TroglyError(f"The Agent did not answer within {REFRESH_TIMEOUT_SECONDS} seconds.") from error
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        tail = "\n".join((completed.stderr or completed.stdout).strip().splitlines()[-6:])
        raise TroglyError(f"The Agent returned something Trogly couldn't read. Is it up to date?\n{tail}") from error
    if "error" in result:
        raise TroglyError(result["error"])
    return result


def _require_agent(agent_path: str) -> Path:
    if not agent_path:
        raise TroglyError(
            f"Trogly doesn't know where the Agent is yet. Ask the player for the folder where they installed "
            f"{AGENT_FILE_NAME}, then call set_agent_path."
        )
    agent = Path(agent_path)
    if not agent.exists():
        raise TroglyError(
            f"The Trogly Agent is no longer at {agent}. Ask the player where it is now and call set_agent_path."
        )
    return agent


def _agent_command(agent: Path) -> list[str]:
    return ["dotnet", "run", "--project", str(agent), "--"] if agent.suffix == ".csproj" else [str(agent)]


def _set_bonus_change(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    def active(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
        return {
            (e.get("name") or set_name(e.get("set", ""))): e.get("active_stats") or []
            for e in entries
            if e.get("active")
        }

    was, now = active(before), active(after)
    return {
        "active_after": {name: stats for name, stats in now.items()},
        "turned_on": sorted(now.keys() - was.keys()),
        "turned_off": sorted(was.keys() - now.keys()),
    }


def _stat_change(change: dict[str, Any]) -> dict[str, Any]:
    shown = {}
    for key, label, is_percent in CALCULATED_STATS:
        if key in change:
            value = float(change[key])
            shown[label] = f"{value * 100:+.0f}%" if is_percent else f"{value:+,.0f}"
    return shown


def _parse_time(value: str) -> datetime:
    # .NET writes 7 fractional digits; Python accepts at most 6.
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        digits = "".join(ch for ch in rest if ch.isdigit())
        zone = rest[len(digits):]
        text = f"{head}.{digits[:6]}{zone}"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _total_stat(champion: dict[str, Any], key: str) -> float:
    return float(((champion.get("calculated_stats") or {}).get("total") or {}).get(key) or 0)


def _stat_block(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if not block:
        return None
    shown = {}
    for key, label, is_percent in CALCULATED_STATS:
        value = float(block.get(key) or 0)
        shown[label] = f"{round(value * 100)}%" if is_percent else round(value)
    return shown


def _main_stat_matches(artifact: dict[str, Any], wanted: str) -> bool:
    primary = artifact.get("primary_stat")
    if not primary:
        return False
    wanted = wanted.strip().lower()
    stat = primary.get("stat", "")
    names = {stat.lower(), STAT_SHORT.get(stat, stat).lower()}
    is_percent = not primary.get("is_absolute", True)
    if wanted.endswith("%"):
        return is_percent and wanted[:-1] in names
    return wanted in names


def _by_progress(copies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(copies, key=lambda c: (-c.get("stars", 0), -c.get("level", 0), -len(c.get("equipped_artifact_ids") or {})))


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
