"""Inventory-wide gear advice for Trogly: what to sell and what is worth upgrading.

Stdlib only; `artifacts_mcp.py` is the MCP wrapper. Scores come from fixed rules, not from the model:

- A piece is scored per build profile (attack, HP, DEF, debuff, support). Each substat counts in roll units (its
  value divided by a typical top roll), so SPD 10 and C.RATE 10% compare. The flat version of a profile's
  percentage stat counts a quarter.
- Gloves, Chest, and Boots only fit a profile when their main stat suits it (a flat ATK glove fits none).
- Pieces below level 16 are scored on their expected value at 16, with the odds of the remaining upgrades worked
  out exactly (see `_hit_distribution`).
- The set counts too: scores are scaled by the set's tier weight (`set_tools`), so the same stats on Stone Skin beat
  them on Life, and better sets keep more spares.

Trogly never sells or upgrades anything; these are lists for the player.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import set_tools
from artifacts_tools import SHORT_STAT, SLOT_NAMES, TroglyError, _bonus_text, _load_state, instance_home

RARITY_NAMES = {1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary", 6: "Mythical"}
UPGRADE_LEVELS = (4, 8, 12, 16)
MAX_SUBSTATS = 4
# Every stat a substat can be. A piece can't roll its own main stat as a substat.
SUBSTAT_POOL = ("HP", "HP%", "ATK", "ATK%", "DEF", "DEF%", "SPD", "C.RATE", "C.DMG", "ACC", "RES")
# A typical top roll on a 6-star piece, in the game's units (percentages as fractions).
ROLL_UNIT = {
    "SPD": 5, "C.RATE": 0.05, "C.DMG": 0.06, "ATK%": 0.05, "HP%": 0.05, "DEF%": 0.05,
    "ACC": 10, "RES": 10, "ATK": 20, "HP": 300, "DEF": 20,
}
FLAT_WEIGHT = 0.25
# Future rolls are counted as one roll unit on a 6-star piece; lower ranks roll smaller.
RANK_ROLL_FACTOR = {6: 1.0, 5: 0.8, 4: 0.6, 3: 0.45, 2: 0.3, 1: 0.2}
# Score = stat score x (SET_FLOOR + (1 - SET_FLOOR) x set weight): stats still matter most, the set breaks ties.
SET_FLOOR = 0.6
NO_SET_WEIGHT = 0.45
FLAT_OF = {"ATK%": "ATK", "HP%": "HP", "DEF%": "DEF"}
MAIN_CHECKED_SLOTS = ("Gloves", "Chest", "Boots")
PROFILES: dict[str, dict[str, Any]] = {
    "attack": {
        "stats": ("SPD", "C.RATE", "C.DMG", "ATK%"),
        "mains": {"Gloves": ("C.RATE", "C.DMG", "ATK%"), "Chest": ("ATK%",), "Boots": ("SPD", "ATK%")},
    },
    "hp": {
        "stats": ("SPD", "C.RATE", "C.DMG", "HP%"),
        "mains": {"Gloves": ("C.RATE", "C.DMG", "HP%"), "Chest": ("HP%",), "Boots": ("SPD", "HP%")},
    },
    "def": {
        "stats": ("SPD", "C.RATE", "C.DMG", "DEF%"),
        "mains": {"Gloves": ("C.RATE", "C.DMG", "DEF%"), "Chest": ("DEF%",), "Boots": ("SPD", "DEF%")},
    },
    "debuff": {
        "stats": ("SPD", "ACC", "HP%", "DEF%"),
        "mains": {"Gloves": ("HP%", "DEF%"), "Chest": ("ACC", "HP%", "DEF%"), "Boots": ("SPD",)},
    },
    "support": {
        "stats": ("SPD", "HP%", "DEF%", "RES"),
        "mains": {"Gloves": ("HP%", "DEF%"), "Chest": ("HP%", "DEF%", "RES"), "Boots": ("SPD", "HP%", "DEF%")},
    },
}
NOTE = (
    "Scores follow Trogly's fixed rules (useful substats per build profile, in roll units). Upgrade odds assume each "
    "upgrade picks a substat at random, and a new substat is drawn from the stats the piece doesn't have yet. Trogly "
    "never sells or upgrades; the player decides."
)


def sell_candidates(
    protected_artifact_ids: list[str] | None = None,
    keep_per_slot: int = 10,
    keep_per_set: int = 2,
    set_keep_min_score: float = 3.0,
    upgrade_chance: float = 0.3,
    limit: int = 50,
    home: Path | None = None,
) -> dict[str, Any]:
    """Splits the vault (unworn pieces) into keep, upgrade first, and sell."""
    home = home or instance_home()
    state = _load_state(home)
    weights = _set_weights(home)
    protected = {str(i) for i in protected_artifact_ids or []}
    worn = _worn_ids(state)
    vault = [_scored(a, weights) for a in state.get("artifacts") or [] if a["id"] not in worn]

    keep_reasons: dict[str, str] = {}
    for artifact in vault:
        if artifact["id"] in protected:
            keep_reasons[artifact["id"]] = "in a saved build or plan"
    for profile in PROFILES:
        fits = [a for a in vault if a["profiles"][profile] is not None]
        for slot in {a["slot"] for a in fits}:
            in_slot = sorted((a for a in fits if a["slot"] == slot), key=lambda a: -a["profiles"][profile])
            for rank, artifact in enumerate(in_slot[:keep_per_slot], start=1):
                keep_reasons.setdefault(artifact["id"], f"#{rank} {profile} {_slot(slot)} in the vault")
            for set_name in {a["set_name"] for a in in_slot}:
                spares = _spares(keep_per_set, next(a["set_weight"] for a in in_slot if a["set_name"] == set_name))
                same_set = [a for a in in_slot
                            if a["set_name"] == set_name and a["profiles"][profile] >= set_keep_min_score][:spares]
                for rank, artifact in enumerate(same_set, start=1):
                    keep_reasons.setdefault(artifact["id"], f"#{rank} {set_name} {_slot(slot)} for {profile}")

    upgrade_first, sell = [], []
    for artifact in vault:
        if artifact["id"] in keep_reasons:
            continue
        best = artifact["best_profile"]
        # Odds weighed by the set: a long shot on a top set can be worth it, the same odds on a weak set aren't.
        if artifact["level"] < 16 and best and artifact["good_chance"][best] * _set_factor(artifact["set_weight"]) >= upgrade_chance:
            upgrade_first.append(artifact)
        else:
            sell.append(artifact)
    sell.sort(key=lambda a: (a["best_score"], a["rank"], a["rarity"]))
    upgrade_first.sort(key=lambda a: -a["good_chance"][a["best_profile"]])

    return {
        "vault_pieces": len(vault),
        "keep": len(keep_reasons),
        "upgrade_first": len(upgrade_first),
        "sell": len(sell),
        "sell_by_slot": _count(sell, lambda a: _slot(a["slot"])),
        "sell_by_stars": _count(sell, lambda a: f"{a['rank']}*"),
        "sell_list": [_sell_row(a, vault) for a in sell[:limit]],
        "sell_ids": [a["id"] for a in sell],
        "upgrade_first_list": [_upgrade_row(a, a["best_profile"]) for a in upgrade_first[:limit]],
        "rules": {"keep_per_slot": keep_per_slot, "keep_per_set": keep_per_set,
                  "set_keep_min_score": set_keep_min_score, "upgrade_chance": upgrade_chance,
                  "protected": len(protected)},
        "note": NOTE,
    }


def upgrade_candidates(
    profile: str = "",
    stats: list[str] | None = None,
    sets: list[str] | None = None,
    slots: list[str] | None = None,
    min_stars: int = 6,
    min_hits: int = 5,
    include_worn: bool = True,
    limit: int = 20,
    home: Path | None = None,
) -> dict[str, Any]:
    """Ranks pieces below level 16 by the chance their remaining upgrades land on the wanted stats."""
    home = home or instance_home()
    state = _load_state(home)
    weights = _set_weights(home)
    wanted = _wanted(profile, stats)
    owners = _owners(state)
    set_filter = {s.lower() for s in sets or []}
    slot_filter = {_game_slot(s).lower() for s in slots or []}
    rows = []
    for raw in state.get("artifacts") or []:
        if raw["level"] >= 16 or raw["rank"] < min_stars:
            continue
        if not include_worn and raw["id"] in owners:
            continue
        if set_filter and (raw.get("set_name") or raw["set"]).lower() not in set_filter:
            continue
        if slot_filter and raw["slot"].lower() not in slot_filter:
            continue
        artifact = _scored(raw, weights)
        options = [wanted] if wanted else [p for p in PROFILES if artifact["profiles"][p] is not None]
        if wanted and not _main_fits(artifact, wanted):
            continue
        for option in options:
            chances = _hit_distribution_for(artifact, option)
            row = _upgrade_row(artifact, option, min_hits)
            row["worn_by"] = owners.get(raw["id"])
            chance = sum(p for hits, p in chances.items() if hits >= min_hits)
            row["_sort"] = (chance * _set_factor(artifact["set_weight"]), row["expected_hits"])
            rows.append(row)
    best: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: r["_sort"], reverse=True):
        best.setdefault(row["id"], row)
    ranked = list(best.values())[:limit]
    for row in ranked:
        del row["_sort"]
    return {
        "wanted": _wanted_label(wanted, profile, stats),
        "min_hits": min_hits,
        "candidates_checked": len(best),
        "candidates": ranked,
        "how_to_read": (
            "hits = useful substats plus useful rolls into them at level 16. chance_of_min_hits is the chance of "
            "ending with at least min_hits. A level-0 Legendary can end with up to 8 hits, an Epic 7, a Rare 6. "
            "Ranking also weighs the set's tier, so a better set wins between similar odds."
        ),
        "note": NOTE,
    }


def rank_set_pieces(
    set_name: str,
    profile: str = "",
    stats: list[str] | None = None,
    top: int = 3,
    include_worn: bool = True,
    home: Path | None = None,
) -> dict[str, Any]:
    """The best pieces of one set in every slot for a build profile or a list of wanted stats."""
    home = home or instance_home()
    state = _load_state(home)
    entry = set_tools.find_set(set_tools.catalog(home), set_name)
    wanted = _wanted(profile, stats) or PROFILES["attack"]["stats"]
    mains = PROFILES[profile.lower()]["mains"] if profile and not stats else None
    owners = _owners(state)
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for raw in state.get("artifacts") or []:
        if raw["set"] != entry["code"] or (not include_worn and raw["id"] in owners):
            continue
        artifact = _scored(raw, {entry["code"]: (entry["weight"], entry["tier"])})
        if not _main_fits(artifact, wanted, mains):
            continue
        distribution = _hit_distribution_for(artifact, wanted)
        expected_new = sum(h * p for h, p in distribution.items()) - _hits(artifact["subs"], wanted)
        score = _units(artifact["subs"], wanted) + expected_new * RANK_ROLL_FACTOR.get(raw["rank"], 0.2)
        by_slot.setdefault(_slot(raw["slot"]), []).append(
            {**_piece(artifact), "score": round(score, 1), "worn_by": owners.get(raw["id"])})
    slots = {slot: sorted(rows, key=lambda r: -r["score"])[:top] for slot, rows in sorted(by_slot.items())}
    return {
        "set": entry["name"],
        "tier": entry["tier"],
        "pieces_for_bonus": entry["effect_pieces"],
        "wanted": _wanted_label(wanted, profile, stats),
        "slots_covered": len(slots),
        "best_by_slot": slots,
        "note": "score = useful substats in roll units, plus expected future rolls for pieces below level 16. "
                "Use optimize_gear with require_sets to build the exact loadout.",
    }


# Scoring


def _set_weights(home: Path) -> dict[str, tuple[float, str]]:
    return {code: (entry["weight"], entry["tier"]) for code, entry in set_tools.catalog(home).items()}


def _set_factor(weight: float) -> float:
    return SET_FLOOR + (1 - SET_FLOOR) * weight


def _spares(keep_per_set: int, weight: float) -> int:
    # Top sets keep the full number of spares, middling sets one, the bottom tier none.
    if weight >= 0.6:
        return keep_per_set
    return min(1, keep_per_set) if weight >= 0.25 else 0


def _scored(raw: dict[str, Any], weights: dict[str, tuple[float, str]] | None = None) -> dict[str, Any]:
    main = _stat_key(raw["primary_stat"]) if raw.get("primary_stat") else ""
    subs = [(_stat_key(b), float(b.get("value", 0)) + float(b.get("glyph") or 0), int(b.get("rolls") or 0))
            for b in raw.get("substats") or []]
    artifact = {
        "id": raw["id"], "slot": raw["slot"], "set_name": raw.get("set_name") or raw["set"], "rank": raw["rank"],
        "rarity": raw["rarity"], "level": raw["level"], "main": main, "subs": subs, "raw": raw,
    }
    artifact["set_weight"], artifact["set_tier"] = (weights or {}).get(raw["set"], (NO_SET_WEIGHT, "-"))
    factor = _set_factor(artifact["set_weight"])
    artifact["profiles"], artifact["good_chance"] = {}, {}
    for name, profile in PROFILES.items():
        if not _main_fits(artifact, profile["stats"], profile["mains"]):
            artifact["profiles"][name] = None
            continue
        now = _units(subs, profile["stats"])
        distribution = _hit_distribution_for(artifact, profile["stats"])
        expected_new_hits = sum(h * p for h, p in distribution.items()) - _hits(subs, profile["stats"])
        artifact["profiles"][name] = (now + expected_new_hits * RANK_ROLL_FACTOR.get(raw["rank"], 0.2)) * factor
        artifact["good_chance"][name] = sum(p for h, p in distribution.items() if h >= 5)
    fitting = {n: s for n, s in artifact["profiles"].items() if s is not None}
    artifact["best_profile"] = max(fitting, key=fitting.get) if fitting else None
    artifact["best_score"] = fitting[artifact["best_profile"]] if fitting else -1.0
    return artifact


def _units(subs: list[tuple[str, float, int]], stats: tuple[str, ...]) -> float:
    total = 0.0
    flats = {FLAT_OF[s] for s in stats if s in FLAT_OF}
    for key, value, _ in subs:
        if key in stats:
            total += value / ROLL_UNIT[key]
        elif key in flats:
            total += FLAT_WEIGHT * value / ROLL_UNIT[key]
    return total


def _hits(subs: list[tuple[str, float, int]], stats: tuple[str, ...]) -> int:
    return sum(1 + rolls for key, _, rolls in subs if key in stats)


def _hit_distribution_for(artifact: dict[str, Any], stats: tuple[str, ...]) -> dict[int, float]:
    subs = artifact["subs"]
    useful_present = sum(1 for key, _, _ in subs if key in stats)
    pool = [s for s in SUBSTAT_POOL if s != artifact["main"] and s not in {key for key, _, _ in subs}]
    useful_available = sum(1 for s in pool if s in stats)
    remaining = sum(1 for level in UPGRADE_LEVELS if level > artifact["level"])
    start = _hits(subs, stats)
    return {start + h: p for h, p in _hit_distribution(len(subs), useful_present, len(pool), useful_available,
                                                         remaining).items()}


@lru_cache(maxsize=None)
def _hit_distribution(subs: int, useful: int, pool: int, useful_pool: int, upgrades: int) -> dict[int, float]:
    """Exact odds of extra useful hits. Missing substats come with the last upgrades (an Epic rolls at 4, 8, and 12
    and gains its fourth substat at 16; a Rare gains its third at 12), as the account data shows. A new substat is
    drawn from the pool; any other upgrade rolls into one of the current substats at random."""
    if upgrades == 0:
        return {0: 1.0}
    outcomes: dict[int, float] = {}

    def add(chance: float, hit: int, rest: dict[int, float]) -> None:
        for hits, p in rest.items():
            outcomes[hits + hit] = outcomes.get(hits + hit, 0.0) + chance * p

    if MAX_SUBSTATS - subs >= upgrades and pool > 0:
        p_useful = useful_pool / pool
        if p_useful:
            add(p_useful, 1, _hit_distribution(subs + 1, useful + 1, pool - 1, useful_pool - 1, upgrades - 1))
        if p_useful < 1:
            add(1 - p_useful, 0, _hit_distribution(subs + 1, useful, pool - 1, useful_pool, upgrades - 1))
    elif subs:
        p_useful = useful / subs
        if p_useful:
            add(p_useful, 1, _hit_distribution(subs, useful, pool, useful_pool, upgrades - 1))
        if p_useful < 1:
            add(1 - p_useful, 0, _hit_distribution(subs, useful, pool, useful_pool, upgrades - 1))
    else:
        return {0: 1.0}
    return outcomes


def _main_fits(artifact: dict[str, Any], stats: tuple[str, ...], mains: dict[str, tuple[str, ...]] | None = None) -> bool:
    if artifact["slot"] not in MAIN_CHECKED_SLOTS:
        return True
    allowed = (mains or {}).get(artifact["slot"]) if mains else stats
    return artifact["main"] in (allowed or ())


# Rows and helpers


def _sell_row(artifact: dict[str, Any], vault: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = [f"{artifact['rank']}* {RARITY_NAMES.get(artifact['rarity'], artifact['rarity'])} level {artifact['level']}"]
    if artifact["best_profile"] is None:
        reasons.append(f"{artifact['main']} main on {_slot(artifact['slot'])} suits no build")
    else:
        best = artifact["best_profile"]
        useful = [key for key, _, _ in artifact["subs"] if key in PROFILES[best]["stats"]]
        reasons.append(f"best fit {best} build: {len(useful)} useful substat(s) ({', '.join(useful) or 'none'}), "
                       f"score {artifact['best_score']:.1f}")
        if artifact["level"] < 16:
            reasons.append(f"{artifact['good_chance'][best]:.0%} chance of 5+ useful hits if upgraded")
    return {**_piece(artifact), "why": "; ".join(reasons)}


def _upgrade_row(artifact: dict[str, Any], option: str | tuple[str, ...], min_hits: int = 5) -> dict[str, Any]:
    stats = PROFILES[option]["stats"] if isinstance(option, str) else option
    distribution = _hit_distribution_for(artifact, stats)
    return {
        **_piece(artifact),
        "for": option if isinstance(option, str) else ", ".join(option),
        "hits_now": _hits(artifact["subs"], stats),
        "expected_hits": round(sum(h * p for h, p in distribution.items()), 2),
        "chance_of_min_hits": f"{sum(p for h, p in distribution.items() if h >= min_hits):.0%}",
        "upgrades_left": sum(1 for level in UPGRADE_LEVELS if level > artifact["level"]),
    }


def _piece(artifact: dict[str, Any]) -> dict[str, Any]:
    raw = artifact["raw"]
    return {
        "id": artifact["id"],
        "slot": _slot(artifact["slot"]),
        "set": "no set" if artifact["set_name"] == "None" else artifact["set_name"],
        "set_tier": artifact["set_tier"],
        "stars": artifact["rank"],
        "rarity": RARITY_NAMES.get(artifact["rarity"], artifact["rarity"]),
        "level": artifact["level"],
        "main_stat": _bonus_text(raw["primary_stat"]) if raw.get("primary_stat") else None,
        "substats": [_bonus_text(b) + (f" ({b['rolls']} roll{'s' if b['rolls'] != 1 else ''})" if b.get("rolls") else "")
                     for b in raw.get("substats") or []],
    }


def _stat_key(bonus: dict[str, Any]) -> str:
    label = SHORT_STAT.get(bonus.get("stat", ""), bonus.get("stat", ""))
    if label in ("HP", "ATK", "DEF") and not bonus.get("is_absolute", True):
        return f"{label}%"
    return label


def _wanted(profile: str, stats: list[str] | None) -> tuple[str, ...] | None:
    if stats:
        return tuple(_stat_alias(s) for s in stats)
    if profile:
        if profile.lower() not in PROFILES:
            raise TroglyError(f"Profile must be one of: {', '.join(PROFILES)}; or give stats instead.")
        return PROFILES[profile.lower()]["stats"]
    return None


def _wanted_label(wanted: tuple[str, ...] | None, profile: str, stats: list[str] | None) -> str:
    if not wanted:
        return "each piece's best-fitting build profile"
    return profile.lower() if profile and not stats else ", ".join(wanted)


def _stat_alias(name: str) -> str:
    key = name.strip().upper().replace(" ", "")
    aliases = {"HP%": "HP%", "HEALTH%": "HP%", "ATK%": "ATK%", "ATTACK%": "ATK%", "DEF%": "DEF%", "DEFENCE%": "DEF%",
               "DEFENSE%": "DEF%", "SPD": "SPD", "SPEED": "SPD", "C.RATE": "C.RATE", "CRITRATE": "C.RATE",
               "C.DMG": "C.DMG", "CRITDAMAGE": "C.DMG", "ACC": "ACC", "ACCURACY": "ACC", "RES": "RES",
               "RESISTANCE": "RES", "HP": "HP", "ATK": "ATK", "DEF": "DEF"}
    if key not in aliases:
        raise TroglyError(f"'{name}' is not a substat. Use: {', '.join(SUBSTAT_POOL)}.")
    return aliases[key]


def _worn_ids(state: dict[str, Any]) -> set[str]:
    return set(_owners(state))


def _owners(state: dict[str, Any]) -> dict[str, str]:
    return {artifact_id: champion.get("name", "")
            for champion in state.get("champions") or []
            for artifact_id in (champion.get("equipped_artifact_ids") or {}).values()}


def _slot(slot: str) -> str:
    return SLOT_NAMES.get(slot, slot)


def _game_slot(slot: str) -> str:
    return {v.lower(): k for k, v in SLOT_NAMES.items()}.get(slot.lower(), slot)


def _count(items: list[dict[str, Any]], key) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[key(item)] = counts.get(key(item), 0) + 1
    return dict(sorted(counts.items()))
