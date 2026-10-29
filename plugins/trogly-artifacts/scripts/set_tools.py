"""Artifact set advice for Trogly: how good each set is, whether it helps a champion, and whether it works in an
encounter.

Stdlib only; `artifacts_mcp.py` is the MCP wrapper. A set's value for a champion in an encounter is

    tier weight x fit x encounter multiplier

- **Tier weight** comes from `data/set_tiers.json` in this plugin, overridden by the player's
  `data/artifacts/set-preferences.json` in the instance.
- **Fit** says whether the set's bonus helps this champion. Stat bonuses count only for stats the champion uses
  (ACC only for champions that place debuffs). Effects count only when the champion can use them (on-hit effects
  need a champion that deals damage). The champion's kit comes from trogly-knowledge's `data/knowledge/knowledge.json`.
- **Encounter multiplier** comes from the bosses' own kits: a boss immune to Stun makes a Stun set worth nothing,
  and a boss that only takes full damage once its MAX HP is destroyed (Borgoth) puts Destroy above every tier.
  Player rules are applied on top.

Set definitions (pieces, stat bonuses, in-game text) also come from the knowledge file. Without it, set advice
uses tiers only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import artifacts_tools
from artifacts_tools import TroglyError, _load_state, _read_json, _resolve_champion, instance_home

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIERS_PATH = PLUGIN_ROOT / "data" / "set_tiers.json"
KNOWLEDGE_RELATIVE_PATH = Path("data") / "knowledge" / "knowledge.json"
PREFERENCES_RELATIVE_PATH = Path("data") / "artifacts" / "set-preferences.json"
ACCESSORY_SET_MIN_ID = 1000
DEFAULT_GEAR_STAGE = "mid"
# A boss mechanic that a set answers (Borgoth takes 90% less damage until his MAX HP is destroyed) lifts the set's
# value to at least this, above any tier weight.
MECHANIC_VALUE = 1.2
# Pieces needed for a variable set's effect.
VARIABLE_SET_EFFECT_PIECES = 4

STAT_LABELS = {
    "Health": "HP", "Attack": "ATK", "Defence": "DEF", "Speed": "SPD", "Resistance": "RES", "Accuracy": "ACC",
    "CriticalChance": "C.RATE", "CriticalDamage": "C.DMG", "IgnoreDefence": "IGN.DEF",
}
SCALING_STATS = {"ATK": "Attack", "DEF": "Defence", "HP": "Health"}
SUPPORT_ROLES = {"Support", "Defense", "HP"}
AREA_OF_ENCOUNTER = {"clan_boss": "AllianceBoss", "hydra": "Hydra", "chimera": "Chimera"}
BOSSLESS_ENCOUNTERS = {"arena", "siege", "general"}
ON_HIT_TAGS = {"ignore_def", "damage_boost", "crowd_control", "max_hp", "poison", "heal_block", "counter"}


# Catalog


def catalog(home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every artifact set by game code, with its tier weight and, when the knowledge file has it, its bonuses."""
    home = home or instance_home()
    defaults = json.loads(DEFAULT_TIERS_PATH.read_text(encoding="utf-8"))
    preferences = preferences_file(home)
    weights = defaults["tier_weights"]
    knowledge_sets = {s["code"]: s for s in _read_json(home / KNOWLEDGE_RELATIVE_PATH).get("artifact_sets") or []}
    names = _names_from_state(home)
    sets: dict[str, dict[str, Any]] = {}
    for code in sorted(set(defaults["sets"]) | {c for c, s in knowledge_sets.items() if s["id"] < ACCESSORY_SET_MIN_ID}):
        default = defaults["sets"].get(code, {"tier": "C", "tags": [], "source": "estimate"})
        game = knowledge_sets.get(code, {})
        name = game.get("name") or names.get(code) or code
        override = preferences.get("sets", {}).get(code) or preferences.get("sets", {}).get(name) or {}
        tier = override.get("tier") or default["tier"]
        weight = override.get("weight", weights.get(tier, 0.25))
        series = game.get("series") or []
        sets[code] = {
            "code": code,
            "name": name,
            "tier": tier,
            "weight": float(weight),
            "tier_source": "player" if override else default.get("source", "tier list"),
            "tags": default.get("tags", []),
            "debuff": default.get("debuff"),
            "effect_pieces": game.get("pieces") or (VARIABLE_SET_EFFECT_PIECES if series else _pieces_guess(code)),
            "variable": bool(series),
            "stats": _set_stats(game),
            "description": game.get("description"),
        }
    return sets


def find_set(sets: dict[str, dict[str, Any]], reference: str) -> dict[str, Any]:
    key = reference.strip().lower()
    for entry in sets.values():
        if key in (entry["code"].lower(), entry["name"].lower()):
            return entry
    raise TroglyError(f"No artifact set called '{reference}'. Use the in-game name, such as Stone Skin or Speed.")


def _set_stats(game: dict[str, Any]) -> list[str]:
    bonuses = list(game.get("bonuses") or [])
    for series in game.get("series") or []:
        bonuses += series.get("bonuses") or []
    return sorted({b["stat"] for b in bonuses})


def _pieces_guess(code: str) -> int:
    return 2 if code in {"Hp", "AttackPower", "Defense", "AttackSpeed", "CriticalChance", "CriticalDamage", "Accuracy",
                         "Resistance"} else 4


def _names_from_state(home: Path) -> dict[str, str]:
    path = home / artifacts_tools.STATE_RELATIVE_PATH
    if not path.exists():
        return {}
    return {a["set"]: a["set_name"] for a in _read_json(path).get("artifacts") or [] if a.get("set_name")}


# Preferences


def preferences_file(home: Path) -> dict[str, Any]:
    return _read_json(home / PREFERENCES_RELATIVE_PATH)


def set_preferences(
    action: str = "show", set_name: str = "", tier: str = "", weight: float | None = None, gear_stage: str = "",
    encounter: str = "", boss: str = "", multiplier: float | None = None, note: str = "", home: Path | None = None,
) -> dict[str, Any]:
    home = home or instance_home()
    path = home / PREFERENCES_RELATIVE_PATH
    preferences = preferences_file(home)
    defaults = json.loads(DEFAULT_TIERS_PATH.read_text(encoding="utf-8"))
    sets = catalog(home)
    if action == "show":
        return _show_preferences(preferences, sets, defaults)
    if action == "set_tier":
        entry = find_set(sets, set_name)
        if tier and tier not in defaults["tier_weights"]:
            raise TroglyError(f"Tier must be one of: {', '.join(defaults['tier_weights'])}.")
        if not tier and weight is None:
            raise TroglyError("Give a tier (S+ to D) or a weight (0 to 1).")
        override = {"tier": tier} if tier else {}
        if weight is not None:
            override["weight"] = max(0.0, min(1.0, float(weight)))
        preferences.setdefault("sets", {})[entry["code"]] = override
    elif action == "reset_tier":
        entry = find_set(sets, set_name)
        preferences.get("sets", {}).pop(entry["code"], None)
    elif action == "gear_stage":
        if gear_stage not in defaults["gear_stages"] or gear_stage == "about":
            raise TroglyError("Gear stage must be early (stats before sets), mid, or late (sets first).")
        preferences["gear_stage"] = gear_stage
    elif action == "add_rule":
        entry = find_set(sets, set_name)
        if multiplier is None or not encounter:
            raise TroglyError("A rule needs an encounter key (or kind, like doom_tower or clan_boss), a set, and a multiplier.")
        rule = {"encounter": encounter.strip().lower(), "set": entry["code"], "multiplier": float(multiplier)}
        if boss:
            rule["boss"] = boss.strip()
        if note:
            rule["note"] = note
        preferences.setdefault("encounter_rules", []).append(rule)
    elif action == "remove_rule":
        entry = find_set(sets, set_name)
        before = preferences.get("encounter_rules", [])
        preferences["encounter_rules"] = [r for r in before if not (r["set"] == entry["code"] and
                                                                       (not encounter or r["encounter"] == encounter.strip().lower()))]
    else:
        raise TroglyError("Action must be show, set_tier, reset_tier, gear_stage, add_rule, or remove_rule.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(preferences, indent=2), encoding="utf-8")
    return _show_preferences(preferences, catalog(home), defaults)


def _show_preferences(preferences: dict[str, Any], sets: dict[str, dict[str, Any]], defaults: dict[str, Any]) -> dict[str, Any]:
    by_tier: dict[str, list[str]] = {tier: [] for tier in defaults["tier_weights"]}
    for entry in sorted(sets.values(), key=lambda e: e["name"]):
        label = entry["name"] + (" (yours)" if entry["tier_source"] == "player" else
                                 " (estimate)" if entry["tier_source"] == "estimate" else "")
        by_tier.setdefault(entry["tier"], []).append(label)
    stage = preferences.get("gear_stage", DEFAULT_GEAR_STAGE)
    return {
        "tiers": by_tier,
        "tier_weights": defaults["tier_weights"],
        "gear_stage": stage,
        "gear_stage_meaning": f"gives up to {defaults['gear_stages'][stage]:.0%} of the main stat per unit of set value",
        "encounter_rules": preferences.get("encounter_rules", []),
    }


# Champion fit


def champion_needs(home: Path, champion: str) -> dict[str, Any]:
    state = _load_state(home)
    hero = _resolve_champion(state, champion)
    knowledge = _read_json(home / KNOWLEDGE_RELATIVE_PATH)
    kit = next((t for t in knowledge.get("champion_types") or [] if t["type_id"] == hero.get("type_id")), None)
    if kit is None:
        raise TroglyError(
            f"Trogly has no kit for {hero['name']}. Export knowledge with trogly-knowledge (refresh_knowledge) first.")
    effects = [e for skill_id in kit["skill_ids"] for e in (knowledge["skills"].get(str(skill_id)) or {}).get("effects", [])]
    damage = [e for e in effects if e["kind"] == "Damage"]
    scaling = sorted({SCALING_STATS[m] for e in damage for m in re.findall(r"\b(ATK|DEF|HP)\b", e.get("multiplier") or "")})
    debuffs = [e for e in effects if e["kind"] == "ApplyDebuff" and e.get("target") not in ("Producer",)]
    return {
        "champion": hero["name"],
        "role": kit.get("role"),
        "deals_damage": bool(damage),
        "scales_with": scaling or (["Attack"] if damage else []),
        "places_debuffs": bool(debuffs),
    }


def stat_usefulness(needs: dict[str, Any]) -> dict[str, float]:
    damage = needs["deals_damage"]
    support = needs["role"] in SUPPORT_ROLES or not damage
    useful = {
        "Speed": 1.0,
        "CriticalChance": 1.0 if damage else 0.0,
        "CriticalDamage": 1.0 if damage else 0.0,
        "IgnoreDefence": 1.0 if damage else 0.0,
        "Accuracy": 1.0 if needs["places_debuffs"] else 0.0,
        "Resistance": 1.0 if support else 0.5,
        "Health": 0.4,
        "Defence": 0.4,
        "Attack": 0.0,
    }
    for stat in needs["scales_with"]:
        useful[stat] = 1.0
    if support:
        useful["Health"] = max(useful["Health"], 0.8)
        useful["Defence"] = max(useful["Defence"], 0.8)
    return useful


def set_fit(entry: dict[str, Any], needs: dict[str, Any]) -> tuple[float, str]:
    useful = stat_usefulness(needs)
    parts, reasons = [], []
    if entry["stats"]:
        stat_fit = sum(useful.get(s, 0.0) for s in entry["stats"]) / len(entry["stats"])
        parts.append(stat_fit)
        wasted = [STAT_LABELS.get(s, s) for s in entry["stats"] if useful.get(s, 0.0) == 0]
        if wasted:
            reasons.append(f"{', '.join(wasted)} doesn't help {needs['champion']}")
    if entry["tags"]:
        effect_fit = max(_tag_fit(tag, needs) for tag in entry["tags"])
        parts.append(effect_fit)
        if effect_fit == 0:
            reasons.append(f"its effect needs a champion that deals damage")
    fit = sum(parts) / len(parts) if parts else 0.5
    return fit, "; ".join(reasons)


def _tag_fit(tag: str, needs: dict[str, Any]) -> float:
    damage = needs["deals_damage"]
    if tag in ON_HIT_TAGS:
        return 1.0 if damage else 0.0
    if tag == "team_support":
        return 1.0 if needs["role"] in SUPPORT_ROLES or not damage else 0.5
    if tag == "survival":
        return 1.0 if needs["role"] in SUPPORT_ROLES else 0.8
    return {"extra_turn": 1.0, "turn_meter": 0.8, "debuff_block": 0.8}.get(tag, 0.5)


# Encounters


def encounter_effects(home: Path, encounter: str, sets: dict[str, dict[str, Any]]) -> dict[str, tuple[float, str]]:
    """Set code to (multiplier, reason) for an encounter, from boss kits and the player's rules."""
    effects: dict[str, tuple[float, str]] = {}
    key = encounter.strip().lower()
    bosses = _encounter_bosses(home, key)
    if bosses:
        immune = set.intersection(*(_immunities(kit) for _, kit in bosses))
        rewards_max_hp = any(_rewards_max_hp(kit) for _, kit in bosses)
        boss_names = sorted({name for name, _ in bosses})
        for code, entry in sets.items():
            debuff = entry.get("debuff")
            if debuff and debuff.lower() in immune:
                effects[code] = (0.0, f"{', '.join(boss_names)} is immune to {debuff}")
            elif "max_hp" in entry["tags"] and rewards_max_hp:
                effects[code] = (max(1.0, MECHANIC_VALUE / max(entry["weight"], 0.01)),
                                 f"{', '.join(boss_names)} takes less damage until its MAX HP is destroyed")
    for rule in preferences_file(home).get("encounter_rules", []):
        if not (key == rule["encounter"] or key.startswith(rule["encounter"] + ":")):
            continue
        if rule.get("boss") and not any(rule["boss"].lower() in name.lower() for name, _ in bosses):
            continue
        current = effects.get(rule["set"], (1.0, ""))[0]
        effects[rule["set"]] = (current * rule["multiplier"], rule.get("note") or "your rule")
    return effects


def _encounter_bosses(home: Path, key: str) -> list[tuple[str, dict[str, Any]]]:
    knowledge = _read_json(home / KNOWLEDGE_RELATIVE_PATH)
    regions = knowledge.get("regions") or []
    types = {t["type_id"]: t for t in knowledge.get("champion_types") or []}
    stages = []
    kind = key.split(":")[0]
    if key.startswith("stage:") and key[6:].isdigit():
        stage_id = int(key[6:])
        stages = [s for r in regions for track in r["difficulties"].values() for s in track if s["id"] == stage_id]
    elif kind in AREA_OF_ENCOUNTER:
        stages = [s for r in regions if r["area"] == AREA_OF_ENCOUNTER[kind] for track in r["difficulties"].values() for s in track]
    elif kind not in BOSSLESS_ENCOUNTERS:
        # A boss name ("Borgoth", "boss:Fyro"): every stage that boss leads.
        name = key.removeprefix("boss:").strip()
        stages = [s for r in regions for track in r["difficulties"].values() for s in track
                  if s.get("has_boss") and any(name in e["name"].lower() for e in s["enemies"])]
        if not stages:
            raise TroglyError(f"'{key}' is not an encounter or a boss Trogly knows. Use a boss name (Borgoth), "
                              "stage:<id> from find_stage, clan_boss, hydra, chimera, arena, siege, or general.")
    bosses = {}
    for stage in stages:
        if not stage.get("enemies"):
            continue
        last_wave = max(e["wave"] for e in stage["enemies"])
        flagged = [e for e in stage["enemies"] if e["is_boss"]]
        candidates = flagged or ([max((e for e in stage["enemies"] if e["wave"] == last_wave), key=lambda e: e["level"])]
                                 if stage.get("has_boss") else [])
        if kind not in AREA_OF_ENCOUNTER and not key.startswith("stage:") and kind not in BOSSLESS_ENCOUNTERS:
            candidates = [e for e in candidates if key.removeprefix("boss:").strip() in e["name"].lower()]
        for enemy in candidates:
            if enemy["type_id"] in types:
                bosses[enemy["type_id"]] = (enemy["name"], _kit_text(knowledge, types[enemy["type_id"]]))
    return list(bosses.values())


def _kit_text(knowledge: dict[str, Any], champion_type: dict[str, Any]) -> dict[str, Any]:
    return {"texts": [(knowledge["skills"].get(str(i)) or {}).get("description") or "" for i in champion_type["skill_ids"]]}


def _immunities(kit: dict[str, Any]) -> set[str]:
    immune: set[str] = set()
    for text in kit["texts"]:
        for clause in re.findall(r"[Ii]mmune to ([^.]*)", text):
            immune |= {name.lower() for name in re.findall(r"\[([^\]]+)\]", clause)}
            if re.search(r"MAX HP destruction", clause, re.I):
                immune.add("max hp destruction")
    return immune


def _rewards_max_hp(kit: dict[str, Any]) -> bool:
    return any(re.search(r"MAX HP has been destroyed", text, re.I) for text in kit["texts"])


# Advice


def set_advice(champion: str = "", encounter: str = "", top: int = 12, home: Path | None = None) -> dict[str, Any]:
    home = home or instance_home()
    sets = catalog(home)
    needs = champion_needs(home, champion) if champion else None
    effects = encounter_effects(home, encounter, sets) if encounter else {}
    rows = []
    for code, entry in sets.items():
        fit, why_fit = set_fit(entry, needs) if needs else (1.0, "")
        multiplier, why_encounter = effects.get(code, (1.0, ""))
        value = entry["weight"] * fit * multiplier
        rows.append({
            "set": entry["name"],
            "tier": entry["tier"] + (" (yours)" if entry["tier_source"] == "player" else ""),
            "value": round(value, 2),
            "fit": round(fit, 2) if needs else None,
            "encounter_multiplier": multiplier if encounter else None,
            "pieces_for_bonus": entry["effect_pieces"],
            "bonus": entry["description"],
            "notes": "; ".join(n for n in (why_fit, why_encounter) if n) or None,
        })
    rows.sort(key=lambda r: -r["value"])
    useless = [r["set"] for r in rows if r["encounter_multiplier"] == 0]
    extra = {}
    if not any(entry["stats"] for entry in sets.values()):
        extra["data_note"] = ("The knowledge file has no set bonuses, so fit uses set tags only. Update the Trogly "
                              "Agent and refresh knowledge (trogly-knowledge) to get them.")
    return {
        **extra,
        "champion": needs,
        "encounter": encounter or None,
        "best_sets": [{k: v for k, v in r.items() if v is not None} for r in rows[:top]],
        "useless_here": useless,
        "boosted_here": [r["set"] for r in rows if (r["encounter_multiplier"] or 1) > 1],
        "gear_stage": preferences_file(home).get("gear_stage", DEFAULT_GEAR_STAGE),
        "how_value_works": "value = tier weight x fit for the champion x encounter multiplier. Tiers are a community "
                           "list plus the player's own changes; fit and encounter effects come from the game's data.",
    }


def compare_set_options(
    champion: str,
    maximize: str,
    minimum: dict[str, float] | None = None,
    encounter: str = "",
    sets: list[str] | None = None,
    candidates: int = 3,
    use_other_champions_gear: bool = False,
    never_take_from: list[str] | None = None,
    time_limit_seconds: float = 8,
    home: Path | None = None,
) -> dict[str, Any]:
    """Best gear with no set required, then with each candidate set; says whether each set is worth what it costs."""
    home = home or instance_home()
    defaults = json.loads(DEFAULT_TIERS_PATH.read_text(encoding="utf-8"))
    stage = preferences_file(home).get("gear_stage", DEFAULT_GEAR_STAGE)
    allowance = defaults["gear_stages"][stage]
    catalog_sets = catalog(home)
    if sets:
        chosen = [find_set(catalog_sets, name) for name in sets]
        advice = {row["set"]: row for row in set_advice(champion, encounter, top=200, home=home)["best_sets"]}
        values = [(entry, advice.get(entry["name"], {}).get("value", entry["weight"])) for entry in chosen]
    else:
        advice = set_advice(champion, encounter, top=200, home=home)["best_sets"]
        values = [(find_set(catalog_sets, row["set"]), row["value"]) for row in advice if row["value"] > 0][:candidates]

    def run(require: dict[str, int] | None) -> dict[str, Any]:
        return artifacts_tools.optimize_gear(
            champion, maximize, minimum=minimum, require_sets=require, use_other_champions_gear=use_other_champions_gear,
            never_take_from=never_take_from, results=1, time_limit_seconds=time_limit_seconds, home=home)

    baseline = run(None)
    if not baseline["loadouts"]:
        raise TroglyError("No loadout meets the minimums even without a set. Relax a minimum first.")
    stat_label = baseline["request"]["maximize"]
    base_value = _number(baseline["loadouts"][0]["stats"][stat_label])
    options = []
    for entry, value in values:
        result = run({entry["code"]: entry["effect_pieces"]})
        allowed = allowance * value
        if not result["loadouts"]:
            options.append({"set": entry["name"], "value": value, "verdict": "can't build",
                            "why": f"no {entry['effect_pieces']}-piece {entry['name']} loadout meets the minimums"})
            continue
        loadout = result["loadouts"][0]
        loss = (base_value - _number(loadout["stats"][stat_label])) / base_value if base_value else 0.0
        options.append({
            "set": entry["name"],
            "value": value,
            f"{stat_label}_cost": f"{loss:.1%}",
            "allowed_cost": f"{allowed:.1%}",
            "verdict": "worth it" if loss <= allowed else "not worth it",
            "stats": loadout["stats"],
            "pieces_to_change": loadout["pieces_to_change"],
            "pieces": loadout["pieces"],
            "search_complete": result["search"]["complete"],
        })
    return {
        "champion": baseline["champion"],
        "maximize": stat_label,
        "gear_stage": stage,
        "rule": f"a set is worth it if it costs at most {allowance:.0%} of {stat_label} per unit of set value ({stage} game)",
        "no_set": {"stats": baseline["loadouts"][0]["stats"], "pieces_to_change": baseline["loadouts"][0]["pieces_to_change"],
                   "pieces": baseline["loadouts"][0]["pieces"]},
        "options": options,
        "note": artifacts_tools.NOTE,
    }


def _number(value: Any) -> float:
    return float(str(value).rstrip("%").replace(",", ""))
