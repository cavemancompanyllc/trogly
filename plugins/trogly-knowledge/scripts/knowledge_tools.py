"""RAID knowledge from the game's own static data: champion kits and stages.

The Trogly Agent exports the data with `--export-knowledge` into `data/knowledge/knowledge.json` in the instance.
It changes only with game updates, so it is exported once and re-exported on request or when missing. Stdlib only;
`knowledge_mcp.py` is the MCP wrapper. Needs trogly-core for the account read and the saved Agent location.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

KNOWLEDGE_RELATIVE_PATH = Path("data") / "knowledge" / "knowledge.json"
STATE_RELATIVE_PATH = Path("data") / "state" / "trogly-state.json"
CONFIG_RELATIVE_PATH = Path("data") / "config.json"
EXPORT_TIMEOUT_SECONDS = 300
DEFAULT_LIMIT = 25

# RAID's affinity wheel: each affinity lands strong hits on the one it beats and weak hits on the one that beats
# it. Void is neutral against everything (and nothing is strong or weak against it).
BEATS = {"Magic": "Spirit", "Spirit": "Force", "Force": "Magic"}

DAMAGE_KINDS = {"Damage"}
PLACE_KINDS = {"ApplyBuff", "ApplyDebuff"}

STAT_LABELS = {
    "Health": "HP", "Attack": "ATK", "Defence": "DEF", "Speed": "SPD", "Resistance": "RES", "Accuracy": "ACC",
    "CriticalChance": "C.RATE", "CriticalDamage": "C.DMG", "CriticalHeal": "C.HEAL",
}


class TroglyError(Exception):
    """A problem the player can act on."""


def affinity_matchup(defender: str) -> dict[str, Any]:
    """Which attacker affinities land strong, normal, or weak hits on a defender of this affinity."""
    if defender not in BEATS:
        return {"defender": defender, "strong_hits_from": [], "weak_hits_from": [],
                "normal_hits_from": ["Magic", "Spirit", "Force", "Void"]}
    strong = [attacker for attacker, beaten in BEATS.items() if beaten == defender]
    weak = [BEATS[defender]]
    return {
        "defender": defender,
        "strong_hits_from": strong,
        "weak_hits_from": weak,
        "normal_hits_from": [a for a in ("Magic", "Spirit", "Force", "Void") if a not in strong + weak],
    }


def instance_home() -> Path:
    return Path(os.environ.get("TROGLY_HOME") or Path.cwd())


def status_name(code: str) -> str:
    """'DecreaseDefence60' -> 'Decrease Defence 60%'; 'Enfeeble' stays as is."""
    match = re.match(r"^(.*?)(\d+)(p)?$", code)
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", match.group(1) if match else code).strip()
    if not match:
        return words
    number = match.group(2)
    # The game writes fractional percentages without the point: 025p is 2.5%, 075p is 7.5%.
    if match.group(3) and number.startswith("0"):
        number = f"{number[:-1]}.{number[-1]}".lstrip("0") or "0"
        number = number if not number.startswith(".") else "0" + number
    return f"{words} {number}%"


class Knowledge:
    def __init__(self, raw: dict[str, Any], path: Path):
        self.raw = raw
        self.path = path
        self.types = {t["type_id"]: t for t in raw.get("champion_types") or []}
        self.skills = raw.get("skills") or {}
        self.owned = set(raw.get("owned_type_ids") or [])

    @classmethod
    def load(cls, home: Path | None = None, refresh: bool = False) -> "Knowledge":
        home = home or instance_home()
        path = home / KNOWLEDGE_RELATIVE_PATH
        if refresh or not path.exists():
            export_knowledge(home)
        with path.open(encoding="utf-8") as handle:
            return cls(json.load(handle), path)

    # Champion kits

    def champion_kit(self, champion: str) -> dict[str, Any]:
        matches = self._find_types(champion)
        if not matches:
            raise TroglyError(f"No champion called '{champion}' in Trogly's knowledge. Check the spelling, or refresh_knowledge after a game update.")
        names = sorted({t["name"] for t in matches})
        if len(names) > 1:
            return {"ambiguous": True, "message": f"'{champion}' matches several champions. Ask which one.", "candidates": names[:DEFAULT_LIMIT]}
        champion_type = self._preferred(matches)
        return {
            "champion": champion_type["name"],
            "rarity": champion_type["rarity"],
            "faction": champion_type["faction"],
            "affinity": champion_type["affinity"],
            "affinity_matchup": affinity_matchup(champion_type["affinity"]),
            "role": champion_type["role"],
            "owned": champion_type["type_id"] in self.owned,
            "skills": [self._skill_view(skill_id) for skill_id in champion_type["skill_ids"] if self._skill_view(skill_id)],
            "exported_at": self.raw.get("exported_at"),
        }

    def search_kits(self, effect: str, owned_only: bool = True, limit: int = DEFAULT_LIMIT, home: Path | None = None) -> dict[str, Any]:
        wanted = effect.strip().lower()
        if not wanted:
            raise TroglyError("Say which buff, debuff, or effect to look for, for example 'Decrease DEF', 'Revive', or 'Enfeeble'.")
        compact = re.sub(r"[^a-z0-9]", "", wanted)
        owned_details = _owned_champions(home or instance_home())
        hits = []
        for champion_type in self.types.values():
            if owned_only and champion_type["type_id"] not in self.owned:
                continue
            if not champion_type.get("name"):
                continue
            found = []
            for skill_id in champion_type["skill_ids"]:
                skill = self.skills.get(str(skill_id))
                if not skill or not skill.get("name"):
                    continue
                statuses = [s for e in skill["effects"] for s in e["statuses"]
                            if compact in re.sub(r"[^a-z0-9]", "", status_name(s["name"]).lower())]
                in_text = wanted in (skill.get("description") or "").lower()
                kinds = [e["kind"] for e in skill["effects"] if compact in e["kind"].lower()]
                if statuses or in_text or kinds:
                    found.append({
                        "skill": skill["name"],
                        "cooldown": skill["cooldown"] or None,
                        "places": sorted({f"{status_name(s['name'])} ({s['turns']} turns)" if s["turns"] > 0 else status_name(s["name"]) for s in statuses}),
                    })
            if found:
                hits.append({"champion": champion_type["name"], "rarity": champion_type["rarity"], "affinity": champion_type["affinity"],
                             "owned_copies": owned_details.get(champion_type["type_id"], []), "skills": found})
        hits.sort(key=lambda hit: (-max((c["stars"] for c in hit["owned_copies"]), default=0), hit["champion"]))
        return {"effect": effect, "owned_only": owned_only, "total_matches": len(hits), "champions": hits[:max(1, min(limit, 200))]}

    # Stages

    def find_stage(self, location: str, stage: int | None = None, difficulty: str = "") -> dict[str, Any]:
        regions = self._find_regions(location)
        if not regions:
            raise TroglyError(f"No dungeon, region, or boss called '{location}'. Try the boss name or the dungeon name.")
        if len(regions) > 1:
            return {"ambiguous": True, "message": "Several places match. Ask which one.",
                    "candidates": [self._region_label(r) for r in regions[:DEFAULT_LIMIT]]}
        region = regions[0]
        difficulties = region["difficulties"]
        if difficulty:
            key = next((d for d in difficulties if d.lower() == difficulty.lower()), None)
            if key is None:
                raise TroglyError(f"{self._region_label(region)} has difficulties {', '.join(difficulties)}, not '{difficulty}'.")
        elif len(difficulties) == 1:
            key = next(iter(difficulties))
        else:
            # Hard-mode dungeons list Hard separately; the main track is the longest.
            key = max(difficulties, key=lambda d: len(difficulties[d]))
        stages = difficulties[key]
        shown_difficulty = "only track" if key == "None" else key
        if stage is None:
            return {"place": self._region_label(region), "difficulty": shown_difficulty, "stages": len(stages),
                    "message": "Say which stage to see its enemies, boss skills, and stage modifiers.",
                    "other_difficulties": [d for d in difficulties if d != key]}
        chosen = next((s for s in stages if s["number"] == stage), None)
        if chosen is None:
            raise TroglyError(f"{self._region_label(region)} ({shown_difficulty}) has stages 1 to {len(stages)}.")
        enemies = chosen["enemies"]
        boss_ids = {e["type_id"] for e in enemies if e["is_boss"]}
        if chosen["has_boss"] and not boss_ids and enemies:
            # The game doesn't always flag the boss slot; the strongest enemy of the last wave is it.
            last_wave = max(e["wave"] for e in enemies)
            boss_ids = {max((e for e in enemies if e["wave"] == last_wave), key=lambda e: e["level"])["type_id"]}
        return {
            "place": self._region_label(region),
            "difficulty": shown_difficulty,
            "stage": chosen["number"],
            "champion_slots": chosen["champion_slots"],
            "waves": [
                {"wave": wave, "enemies": [f"{e['name']} ({e['stars']}* level {e['level']}){' - boss' if e['type_id'] in boss_ids else ''}"
                                           for e in enemies if e["wave"] == wave]}
                for wave in sorted({e["wave"] for e in enemies})
            ],
            "stage_modifiers": [self._modifier_text(m) for m in chosen["modifiers"]],
            "boss_kits": [self.champion_kit_by_type(type_id) for type_id in sorted(boss_ids)],
            "exported_at": self.raw.get("exported_at"),
        }

    def champion_kit_by_type(self, type_id: int) -> dict[str, Any]:
        champion_type = self.types[type_id]
        return {"champion": champion_type["name"], "affinity": champion_type["affinity"],
                "affinity_matchup": affinity_matchup(champion_type["affinity"]),
                "skills": [view for skill_id in champion_type["skill_ids"] if (view := self._skill_view(skill_id))]}

    # Helpers

    def _skill_view(self, skill_id: int) -> dict[str, Any] | None:
        skill = self.skills.get(str(skill_id))
        if not skill or not skill.get("name"):
            return None
        damage = [e["multiplier"] for e in skill["effects"] if e["kind"] in DAMAGE_KINDS and e.get("multiplier")]
        placed = []
        for e in skill["effects"]:
            if e["kind"] not in PLACE_KINDS:
                continue
            for s in e["statuses"]:
                chance = f"{round(e['chance'] * 100)}% chance, " if e.get("chance") is not None else ""
                turns = f"{s['turns']} turns" if s["turns"] > 0 else "until removed"
                placed.append(f"{'buff' if e['kind'] == 'ApplyBuff' else 'debuff'} {status_name(s['name'])} ({chance}{turns})")
        view: dict[str, Any] = {"name": skill["name"], "cooldown": skill["cooldown"] or None, "description": skill.get("description")}
        if damage:
            view["damage"] = damage
        if placed:
            view["places"] = placed
        return view

    def _find_types(self, name: str) -> list[dict[str, Any]]:
        key = name.strip().lower()
        exact = [t for t in self.types.values() if (t.get("name") or "").lower() == key]
        return exact or [t for t in self.types.values() if key in (t.get("name") or "").lower()]

    def _preferred(self, types: list[dict[str, Any]]) -> dict[str, Any]:
        # Bosses and enemies reuse champion names with their own kits; the player's own type wins, then a playable one.
        owned = [t for t in types if t["type_id"] in self.owned]
        return (owned or sorted(types, key=lambda t: len(t["skill_ids"])))[0]

    def _find_regions(self, location: str) -> list[dict[str, Any]]:
        key = location.strip().lower()
        compact = re.sub(r"[^a-z0-9]", "", key)
        regions = self.raw.get("regions") or []
        direct = [r for r in regions if compact in re.sub(r"[^a-z0-9]", "", f"{r['name']} {r['code']}".lower())]
        if direct:
            return direct
        # By boss: the region where that boss leads the most stages is "its" dungeon; a region where it only guests
        # (for example a few Champion Journey stages) loses unless it's the only match.
        counts = []
        for region in regions:
            count = sum(1 for stages in region["difficulties"].values() for s in stages
                        if s["has_boss"] and any(key in e["name"].lower() for e in s["enemies"]))
            if count:
                counts.append((count, region))
        counts.sort(key=lambda pair: -pair[0])
        if len(counts) > 1 and counts[0][0] >= 2 * counts[1][0]:
            return [counts[0][1]]
        return [region for _, region in counts]

    def _region_label(self, region: dict[str, Any]) -> str:
        if region["name"] and region["name"] != region["code"]:
            return region["name"]
        bosses = {e["name"] for stages in region["difficulties"].values() for s in stages[-1:] for e in s["enemies"] if s["has_boss"]}
        return f"{region['code']} ({', '.join(sorted(bosses))})" if bosses else region["code"]

    @staticmethod
    def _modifier_text(modifier: dict[str, Any]) -> str:
        stat = STAT_LABELS.get(modifier["stat"], modifier["stat"])
        value = modifier["value"]
        amount = f"{value:+g}" if modifier["absolute"] else f"{value * 100:+g}%"
        who = "boss" if modifier["boss_only"] else "all enemies"
        return f"{who}: {stat} {amount} (wave {modifier['wave']})"


def export_knowledge(home: Path) -> dict[str, Any]:
    agent = _agent_path(home)
    output = home / KNOWLEDGE_RELATIVE_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["dotnet", "run", "--project", str(agent), "--"] if agent.suffix == ".csproj" else [str(agent)]
    try:
        completed = subprocess.run([*command, "--export-knowledge", str(output)], capture_output=True, text=True,
                                   timeout=EXPORT_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as error:
        raise TroglyError(f"The Agent did not finish exporting within {EXPORT_TIMEOUT_SECONDS} seconds.") from error
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise TroglyError("The Agent's answer couldn't be read. It may be older than this plugin.") from error
    if "error" in result:
        raise TroglyError(result["error"])
    return result


def _agent_path(home: Path) -> Path:
    config_path = home / CONFIG_RELATIVE_PATH
    configured = os.environ.get("TROGLY_AGENT_PATH") or (
        json.loads(config_path.read_text(encoding="utf-8")).get("agent_path", "") if config_path.exists() else "")
    if not configured:
        raise TroglyError("Trogly doesn't know where the Agent is yet. Save it with trogly-core's set_agent_path tool.")
    agent = Path(configured)
    if not agent.exists():
        raise TroglyError(f"The Trogly Agent is no longer at {agent}. Update it with set_agent_path.")
    return agent


def _owned_champions(home: Path) -> dict[int, list[dict[str, Any]]]:
    path = home / STATE_RELATIVE_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    owned: dict[int, list[dict[str, Any]]] = {}
    for champion in state.get("champions") or []:
        owned.setdefault(champion.get("type_id"), []).append(
            {"id": champion.get("id"), "stars": champion.get("stars", 0), "level": champion.get("level", 0)})
    for copies in owned.values():
        copies.sort(key=lambda c: (-c["stars"], -c["level"]))
    return owned
