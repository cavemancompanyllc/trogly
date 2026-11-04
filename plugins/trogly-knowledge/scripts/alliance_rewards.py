"""Alliance Hydra and Chimera chests: which chest simulated damage earns at each difficulty, from the game's code.

StaticAllianceData.GetRewardRange (0x182CA2840) turns damage into a percent of the difficulty's alliance-wide health
(damage / health x 100) and takes the first of HydraRewardRanges with low <= percent <= high
(AllianceAttackRewardRange.IsInRange). The Chimera (GetChimeraRewardRange 0x182CA26A0) uses its difficulty's own bands
and low <= percent < high, with a 1e-9 margin (IsInRangeCheckWithEpsilon). No band means no chest. The band then names
the chest through the difficulty's RewardTypeIdByRange. Stdlib only.
"""

from __future__ import annotations

import json
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# AllianceRewardTypeId order, lowest id first. Ranking chests by it is an assumption; the contents are in the answer.
CHEST_ORDER = ("D", "C", "B", "A", "S", "SS", "SR", "Ultimate", "US", "USS", "USR", "USSR")
EPSILON = 1e-9
SAMPLES = 2000
NO_CHEST = "none"
TEMPLATE = Path(__file__).resolve().parent / "rewards_template.html"
REPORT_DIRECTORY = Path("data") / "reports"
# FlexibleRewardType values past MythicalPrize, for knowledge files exported before the Agent named them.
LATER_ITEM_TYPES = {
    "12": "CursedCityGroupA", "13": "CursedCityGroupB", "14": "CursedCityGroupC", "15": "CursedCityGroupD",
    "16": "UserAvatar", "17": "UserFrame", "22": "RelicStones", "23": "RelicMeteor", "24": "RelicStoneAndCarbon",
    "25": "RelicCraftRareMaterial", "26": "RelicCraftEpicMaterial", "27": "RelicCraftLegendaryMaterial",
}


class RewardError(Exception):
    """The knowledge file can't answer a reward question."""


def tables(knowledge: Any, boss: str) -> list[dict[str, Any]]:
    """Each difficulty's health, bands and chest by band, in the game's order."""
    bosses = knowledge.raw.get("alliance_bosses") or {}
    if boss == "hydra":
        entries = [(d, bosses.get("hydra_reward_ranges") or []) for d in bosses.get("hydra") or []]
    else:
        entries = [(d.get("boss") or {}, d.get("reward_ranges") or []) for d in bosses.get("chimera") or []]
    found = [{"difficulty": d["difficulty"], "health": d.get("health") or 0, "ranges": ranges,
              "chest_by_range": d.get("chest_by_range") or {}} for d, ranges in entries if d.get("difficulty")]
    if not found:
        raise RewardError(f"This knowledge file has no {boss.title()} chest tables. Run refresh_knowledge with an Agent "
                          "built on or after 2026-11-03.")
    return found


def band(percent: float, ranges: list[dict[str, Any]], chimera: bool) -> str | None:
    """The first band holding `percent`, as the game's List.Find does."""
    for entry in ranges:
        low, high = entry["low_percent"], entry["high_percent"]
        if chimera:
            above_low = abs(percent - low) <= EPSILON or percent - low > EPSILON
            if above_low and high - percent > EPSILON:
                return entry["range"]
        elif low <= percent <= high:
            return entry["range"]
    return None


def chest(damage: float, table: dict[str, Any], chimera: bool) -> str:
    if table["health"] <= 0:
        return NO_CHEST
    found = band(damage / table["health"] * 100, table["ranges"], chimera)
    return table["chest_by_range"].get(found, NO_CHEST) if found else NO_CHEST


def chest_rank(name: str) -> int:
    return CHEST_ORDER.index(name) + 1 if name in CHEST_ORDER else 0


def chest_odds(per_run: list[float], table: dict[str, Any], chimera: bool, keys: int = 1,
               seed: int = 1) -> dict[str, float]:
    """Chance of each chest. One key uses the simulated battles as they are; more keys add up that many battles drawn
    from them (the period's damage at one difficulty is assumed to be the sum of its keys)."""
    if keys <= 1:
        totals = list(per_run)
    else:
        rng = random.Random(seed)
        totals = [sum(rng.choice(per_run) for _ in range(keys)) for _ in range(SAMPLES)]
    counts: dict[str, int] = {}
    for total in totals:
        name = chest(total, table, chimera)
        counts[name] = counts.get(name, 0) + 1
    return {name: round(count / len(totals), 3)
            for name, count in sorted(counts.items(), key=lambda item: -chest_rank(item[0]))}


def outlook(per_run: list[float], table: dict[str, Any], chimera: bool, keys: int = 1, seed: int = 1) -> dict[str, Any]:
    odds = chest_odds(per_run, table, chimera, keys, seed)
    health = table["health"] or 1
    ordered = sorted(per_run)
    return {
        "difficulty": table["difficulty"],
        "damage_per_key": round(statistics.mean(per_run)),
        "damage_p10": round(ordered[int(0.1 * (len(ordered) - 1))]),
        "damage_p90": round(ordered[int(round(0.9 * (len(ordered) - 1)))]),
        "percent_of_health_per_key": round(statistics.mean(per_run) / health * 100, 4),
        "chest_odds": odds,
        "most_likely_chest": max(odds, key=lambda name: (odds[name], chest_rank(name))),
        "expected_chest_rank": round(sum(chest_rank(name) * p for name, p in odds.items()), 2),
    }


def bands_view(table: dict[str, Any]) -> list[dict[str, Any]]:
    """What each chest needs at one difficulty, in damage."""
    return [{"chest": table["chest_by_range"].get(entry["range"], NO_CHEST),
             "from_damage": round(table["health"] * entry["low_percent"] / 100),
             "to_damage": round(table["health"] * entry["high_percent"] / 100)}
            for entry in table["ranges"] if entry["range"] in table["chest_by_range"]]


def contents(knowledge: Any, boss: str, names: set[str]) -> dict[str, Any]:
    """What each named chest holds: fixed items, then rolled items with each one's chance per roll.

    FlexibleReward.RewardProbsByIndex (InitializeProbabilities 0x182C27AA0) is an item's Probability over the sum of
    all items without a date window, so the weights need not add to 100. When every weight is 0 the client has no odds
    (the server rolls), and `chance` is left out."""
    chests = (knowledge.raw.get("alliance_bosses") or {}).get(f"{boss}_rewards") or []
    found: dict[str, Any] = {}
    for entry in chests:
        name = entry.get("chest")
        if name not in names or name in found:
            continue
        rolled = entry.get("rolled") or {}
        pool = rolled.get("items") or []
        total = sum(item.get("probability") or 0 for item in pool)
        found[name] = {
            "items": [item_view(knowledge, item) for item in entry.get("items") or []],
            "rolls": rolled.get("count", 0),
            "roll_limits": rolled.get("roll_limits") or {},
            "rolled_from": [item_view(knowledge, item, total) for item in pool],
            "odds_known": total > 0,
        }
    return found


def item_view(knowledge: Any, item: dict[str, Any], weight_total: float = 0) -> dict[str, Any]:
    low, high = item.get("min_count", 0), item.get("max_count", 0)
    kind = str(item.get("type"))
    view: dict[str, Any] = {"type": LATER_ITEM_TYPES.get(kind, kind),
                            "count": low if high <= low else f"{low}-{high}"}
    if weight_total > 0:
        view["chance"] = round((item.get("probability") or 0) / weight_total, 4)
    hero = item.get("hero_id")
    if hero is not None:
        view["champion"] = champion_name(knowledge, hero)
    for key in ("resource", "black_market_item", "hero_grade", "artifact_rank", "rarity", "artifact_kind",
                "artifact_set", "a", "b"):
        if item.get(key) is not None:
            view[key] = item[key]
    return view


def champion_name(knowledge: Any, hero_id: int) -> Any:
    """A type id is the champion's base id x 10 plus its grade, so any grade of the same base names it."""
    found = knowledge.types.get(hero_id)
    if found is None:
        found = next((t for type_id, t in knowledge.types.items() if type_id // 10 == hero_id // 10), None)
    return found["name"] if found else hero_id


def assumptions(boss: str, keys: int) -> list[str]:
    rule = ("low <= percent < high (1e-9 margin), with the difficulty's own bands" if boss == "chimera"
            else "low <= percent <= high, with the Hydra's shared bands")
    notes = [
        f"Chest band: damage as a percent of the difficulty's alliance-wide health, first band with {rule} "
        "(game code, StaticAllianceData).",
        "Chests are ranked by the game's chest id order (D lowest, USSR highest); compare the contents too.",
    ]
    if keys > 1:
        notes.append(f"{keys} keys: the chest is assumed to come from the sum of their damage at one difficulty "
                     "(the player's data keeps one damage total and one chest per difficulty per period; the server "
                     "does the sum, so it can't be read from the client).")
    return notes


def write_report(answer: dict[str, Any], boss: str, home: Path, title: str | None = None) -> Path:
    """Fills the chest report template with a rewards answer and saves it; the session publishes it as an Artifact."""
    name = "Hydra" if boss == "hydra" else "Chimera"
    data = {"title": title or f"{name} Chest Outlook", "boss": name,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), **answer}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__TROGLY_DATA__", payload)
    html = html.replace("<title>Chest Outlook</title>", f"<title>{escape(data['title'])}</title>", 1)
    target = home / REPORT_DIRECTORY / f"{boss}-chests.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
