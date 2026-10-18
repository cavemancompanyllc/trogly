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

import speed_tune as tuning

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
        with path.open(encoding="utf-8-sig") as handle:
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

    # Team building

    def team_candidates(
        self,
        location: str,
        stage: int,
        difficulty: str = "",
        min_stars: int = 6,
        limit: int = 40,
        home: Path | None = None,
    ) -> dict[str, Any]:
        """The stage's facts plus every owned champion who could fit, with what matters for picking a team."""
        found = self.find_stage(location, stage, difficulty)
        if found.get("ambiguous") or "boss_kits" not in found:
            return found
        boss = found["boss_kits"][0] if found["boss_kits"] else None
        boss_affinity = boss["affinity"] if boss else ""
        matchup = affinity_matchup(boss_affinity) if boss else None
        roster = _owned_roster(home or instance_home())

        candidates = []
        for type_id, copies in roster.items():
            best = copies[0]
            champion_type = self.types.get(type_id)
            if not champion_type or not champion_type.get("name") or best["stars"] < min_stars:
                continue
            affinity = champion_type["affinity"]
            hits = "normal"
            if matchup and affinity in matchup["strong_hits_from"]:
                hits = "strong"
            elif matchup and affinity in matchup["weak_hits_from"]:
                hits = "weak"
            candidates.append({
                "champion": champion_type["name"],
                "id": best["id"],
                "stars": best["stars"],
                "level": best["level"],
                "awakening": best["awakening"],
                "affinity": affinity,
                "hits_on_boss": hits,
                "role": champion_type["role"],
                "stats": best["stats"],
                "kit": self._kit_summary(champion_type),
            })

        order = {"strong": 0, "normal": 1, "weak": 2}
        candidates.sort(key=lambda c: (order[c["hits_on_boss"]], -(c["awakening"] or 0), -c["stars"], -c["level"], c["champion"]))
        limit = max(1, min(int(limit), 200))
        return {
            "place": found["place"],
            "difficulty": found["difficulty"],
            "stage": found["stage"],
            "team_size": found["champion_slots"],
            "waves": found["waves"],
            "stage_modifiers": found["stage_modifiers"],
            "boss": boss,
            "candidates_total": len(candidates),
            "candidates": candidates[:limit],
            "note": (
                "hits_on_boss comes from the affinity wheel against the boss. Stats are Trogly's calculated stats "
                "(not yet verified in game). Kit summaries come from the game's skill data; read boss.skills "
                "passives for immunities and counters before picking."
            ),
        }

    # Arena

    def arena_candidates(self, mode: str = "classic", min_stars: int = 5, limit: int = 60, home: Path | None = None) -> dict[str, Any]:
        """The player's champions for arena teams, fastest first, with auras and turn meter skills."""
        rules = ARENA_RULES.get(mode.strip().lower())
        if rules is None:
            raise TroglyError(f"Arena mode is classic or tag, not '{mode}'.")
        state = _state(home or instance_home())
        try:
            tune = tuning.SpeedTune(self, state)
        except tuning.SpeedTuneError as error:
            raise TroglyError(str(error)) from error
        best: dict[int, dict[str, Any]] = {}
        for champion in state.get("champions") or []:
            current = best.get(champion.get("type_id"))
            key = (champion.get("stars", 0), champion.get("level", 0), champion.get("awakening_level", 0))
            if current is None or key > (current.get("stars", 0), current.get("level", 0), current.get("awakening_level", 0)):
                best[champion.get("type_id")] = champion

        candidates = []
        for type_id, champion in best.items():
            champion_type = self.types.get(type_id)
            if not champion_type or not champion_type.get("name") or champion.get("stars", 0) < min_stars:
                continue
            stats = _stat_view((champion.get("calculated_stats") or {}).get("total") or {})
            auras = [self._aura_view(aura) for aura in champion_type.get("leader_skills") or []]
            candidates.append({
                "champion": champion_type["name"],
                "id": champion.get("id"),
                "stars": champion.get("stars", 0),
                "level": champion.get("level", 0),
                "awakening": champion.get("awakening_level", 0),
                "affinity": champion_type["affinity"],
                "faction": champion_type["faction"],
                "role": champion_type["role"],
                "stats": stats,
                "aura": auras or None,
                "turn_meter_skills": self._turn_meter_skills(tune, champion, champion_type),
                "kit": self._kit_summary(champion_type),
            })
        candidates.sort(key=lambda c: (-(c["stats"].get("SPD") or 0), c["champion"]))
        limit = max(1, min(int(limit), 200))
        return {
            "mode": mode,
            "rules": rules,
            "candidates_total": len(candidates),
            "candidates": candidates[:limit],
            "note": ("Fastest first. Stats are Trogly's calculated stats (not yet verified in game); in arena the "
                     "leader's aura adds on top when it works there (aura.works_in_arena). turn_meter_skills use the "
                     "champion's booked cooldowns. Check a team's turn order with speed_tune before recommending it."),
        }

    def speed_tune(self, team: list[dict[str, Any]], area: str = "Arena", enemies: list[dict[str, Any]] | None = None,
                   turns: int = tuning.DEFAULT_TURNS, home: Path | None = None) -> dict[str, Any]:
        state = _state(home or instance_home())
        try:
            return tuning.speed_tune(self, state, team, area, enemies, turns)
        except tuning.SpeedTuneError as error:
            raise TroglyError(str(error)) from error

    @staticmethod
    def _aura_view(aura: dict[str, Any]) -> dict[str, Any]:
        stat = STAT_LABELS.get(aura["stat"], aura["stat"])
        amount = f"+{round(aura['amount'])}" if aura.get("absolute") else f"+{round(aura['amount'] * 100)}%"
        who = " ".join(filter(None, [aura.get("affinity"), aura.get("faction")]))
        return {
            "aura": f"{stat} {amount}{' for ' + who + ' allies' if who else ''} in {aura.get('area') or 'all battles'}",
            "works_in_arena": tuning.aura_applies(aura, "Arena"),
        }

    def _turn_meter_skills(self, tune: Any, champion: dict[str, Any], champion_type: dict[str, Any]) -> list[str]:
        lines = []
        for skill in tune.skills_for(champion, champion_type):
            parts = []
            for effect in skill.effects:
                target = effect.get("target") or "?"
                if effect.get("exclude") == "Producer":
                    target += " except self"
                when = f" if {effect['condition']}" if effect.get("condition") else ""
                chance = f" ({round(effect['chance'] * 100)}% chance)" if effect.get("chance") is not None and effect["chance"] < 0.999 else ""
                if effect["kind"] in ("IncreaseStamina", "ReduceStamina"):
                    amount = tuning.stamina_amount(effect.get("multiplier"))
                    size = tuning.describe_amount(amount) if amount else f"by {effect.get('multiplier')}"
                    word = "fill TM" if effect["kind"] == "IncreaseStamina" else "reduce TM"
                    parts.append(f"{word} {size} on {target}{chance}{when}")
                elif effect["kind"] == "ExtraTurn":
                    parts.append(f"extra turn{chance}{when}")
                for status in effect.get("statuses") or []:
                    if status["name"] in tuning.SPEED_STATUSES:
                        parts.append(f"{status_name(status['name'])} for {status['turns']} turns on {target}{chance}{when}")
            if parts:
                cooldown = f"cooldown {skill.cooldown}" + (f", booked from {skill.base_cooldown}" if skill.base_cooldown != skill.cooldown else "") if skill.cooldown else "no cooldown"
                lines.append(f"{skill.label} {skill.name} ({cooldown}): {'; '.join(parts)}")
        return lines

    def _kit_summary(self, champion_type: dict[str, Any]) -> dict[str, Any]:
        buffs, debuffs, abilities, scaling = set(), set(), set(), set()
        skills = []
        for skill_id in champion_type["skill_ids"]:
            skill = self.skills.get(str(skill_id))
            if not skill or not skill.get("name"):
                continue
            for effect in skill["effects"]:
                kind = effect["kind"]
                if kind == "ApplyBuff":
                    buffs.update(status_name(s["name"]) for s in effect["statuses"])
                elif kind == "ApplyDebuff":
                    debuffs.update(status_name(s["name"]) for s in effect["statuses"])
                elif kind in ABILITY_KINDS:
                    abilities.add(ABILITY_KINDS[kind])
                if kind in DAMAGE_KINDS and effect.get("multiplier"):
                    scaling.update(_scaling_of(effect["multiplier"]))
            skills.append({"name": skill["name"], "cooldown": skill["cooldown"] or None})
        return {
            "skills": skills,
            "damage_scales_with": sorted(scaling),
            "buffs_allies": sorted(buffs),
            "debuffs_enemies": sorted(debuffs),
            "other": sorted(abilities),
        }

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


ARENA_RULES = {
    "classic": [
        "Classic Arena: one team of 4. The first champion leads, and only the leader's aura counts.",
        "Your defence team is played by the game's AI when others attack it.",
    ],
    "tag": [
        "Tag Arena: three teams of 3, and a champion can be in only one of them.",
        "Each team's first champion leads it; its aura counts for that team's fight.",
    ],
}


def _state(home: Path) -> dict[str, Any]:
    path = home / STATE_RELATIVE_PATH
    if not path.exists():
        raise TroglyError("No account data yet. Refresh the account with trogly-core's refresh_account first.")
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def _stat_view(total: dict[str, Any]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key, label in (("health", "HP"), ("attack", "ATK"), ("defence", "DEF"), ("speed", "SPD"),
                       ("accuracy", "ACC"), ("resistance", "RES")):
        if key in total:
            stats[label] = round(total[key])
    for key, label in (("critical_chance", "C.RATE"), ("critical_damage", "C.DMG")):
        if key in total:
            stats[label] = f"{round(total[key] * 100)}%"
    return stats


ABILITY_KINDS = {
    "Heal": "heal",
    "Revive": "revive",
    "RemoveDebuff": "cleanse debuffs",
    "TransferDebuff": "transfer debuffs",
    "RemoveBuff": "remove enemy buffs",
    "StealBuff": "steal enemy buffs",
    "IncreaseStamina": "fill ally turn meter",
    "ReduceStamina": "reduce enemy turn meter",
    "ExtraTurn": "extra turn",
    "ReduceCooldown": "reduce ally cooldowns",
    "IncreaseCooldown": "increase enemy cooldowns",
    "IncreaseBuffLifetime": "extend ally buffs",
    "ReduceBuffLifetime": "shorten enemy buffs",
    "IncreaseDebuffLifetime": "extend enemy debuffs",
    "ReduceDebuffLifetime": "shorten ally debuffs",
    "TeamAttack": "ally attack",
    "DestroyHp": "destroy enemy max HP",
    "Detonate": "detonate debuffs",
    "DetonateContinuousDamage": "detonate poisons",
    "PassiveCounterattack": "counterattack",
    "PassiveReflectDamage": "reflect damage",
    "IgnoreDefenceModifier": "ignore enemy DEF",
    "ChangeDefenceModifier": "ignore enemy DEF",
    "SwapHealth": "swap HP",
}

# Formula variables for the attacker's own stats, and the ones that read the target's HP.
OWN_STAT_VARIABLES = {"ATK": "ATK", "DEF": "DEF", "HP": "HP", "SPD": "SPD"}
TARGET_HP_VARIABLES = ("TRG_HP", "REL_TRG_HP", "TRG_MAX_HP")


def _scaling_of(formula: str) -> set[str]:
    tokens = set(re.findall(r"[A-Z_]{2,}", formula))
    scaling = {label for variable, label in OWN_STAT_VARIABLES.items() if variable in tokens}
    if any(variable in tokens for variable in TARGET_HP_VARIABLES):
        scaling.add("enemy HP")
    return scaling


def _owned_roster(home: Path) -> dict[int, list[dict[str, Any]]]:
    """Owned champions by type, best copy first, with awakening and calculated stats."""
    path = home / STATE_RELATIVE_PATH
    if not path.exists():
        raise TroglyError("No account data yet. Refresh the account with trogly-core's refresh_account first.")
    with path.open(encoding="utf-8-sig") as handle:
        state = json.load(handle)
    roster: dict[int, list[dict[str, Any]]] = {}
    for champion in state.get("champions") or []:
        total = (champion.get("calculated_stats") or {}).get("total") or {}
        stats = {}
        for key, label in (("health", "HP"), ("attack", "ATK"), ("defence", "DEF"), ("speed", "SPD"),
                           ("accuracy", "ACC"), ("resistance", "RES")):
            if key in total:
                stats[label] = round(total[key])
        for key, label in (("critical_chance", "C.RATE"), ("critical_damage", "C.DMG")):
            if key in total:
                stats[label] = f"{round(total[key] * 100)}%"
        roster.setdefault(champion.get("type_id"), []).append({
            "id": champion.get("id"), "stars": champion.get("stars", 0), "level": champion.get("level", 0),
            "awakening": champion.get("awakening_level", 0), "gear_pieces": len(champion.get("equipped_artifact_ids") or {}),
            "stats": stats,
        })
    for copies in roster.values():
        copies.sort(key=lambda c: (-c["stars"], -c["level"], -(c["awakening"] or 0), -c["gear_pieces"]))
    return roster


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
        json.loads(config_path.read_text(encoding="utf-8-sig")).get("agent_path", "") if config_path.exists() else "")
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
    with path.open(encoding="utf-8-sig") as handle:
        state = json.load(handle)
    owned: dict[int, list[dict[str, Any]]] = {}
    for champion in state.get("champions") or []:
        owned.setdefault(champion.get("type_id"), []).append(
            {"id": champion.get("id"), "stars": champion.get("stars", 0), "level": champion.get("level", 0)})
    for copies in owned.values():
        copies.sort(key=lambda c: (-c["stars"], -c["level"]))
    return owned
