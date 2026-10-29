#!/usr/bin/env python3
"""MCP server "trogly-artifacts": finds the best gear for a champion with the Trogly Agent's optimiser, and
which artifacts to sell or upgrade, and
which artifact sets suit a champion and an encounter.

Read-only toward the game. Needs trogly-core for the account read and the saved Agent location. Editing this file
requires restarting the Claude Code session, because MCP servers don't hot-reload.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.exit(
        "Trogly needs the 'mcp' Python package for the Python that Claude Code runs. Install it with:\n"
        f'  "{sys.executable}" -m pip install "mcp>=1.28,<2.0.0"'
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifacts_tools  # noqa: E402
import inventory_tools  # noqa: E402
import set_tools  # noqa: E402
from artifacts_tools import TroglyError  # noqa: E402

mcp = FastMCP("trogly-artifacts")


@mcp.tool()
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
    time_limit_seconds: float = 15,
) -> dict[str, Any]:
    """Find the best gear for a champion from the player's artifacts (reads the live game; up to ~20 seconds).

    champion: name or id. maximize: one stat to make as large as possible (HP, ATK, DEF, SPD, RES, ACC, C.RATE,
    C.DMG; crit rate counts only up to 100%). minimum / maximum: final stats that must be reached or not exceeded,
    e.g. {"SPD": 200, "C.RATE": 100}; crit stats are percentages. require_sets: minimum pieces per set by in-game
    name, e.g. {"Speed": 4}. allowed_sets: only use these sets (plus required ones). By default only the vault and
    the champion's own gear are used; set use_other_champions_gear to borrow from others, protecting champions in
    never_take_from (names or ids). Returns up to `results` loadouts with exact stats, the change from current
    gear, every piece (marking pieces already worn and who loses a borrowed piece), and active set bonuses.
    """
    try:
        return artifacts_tools.optimize_gear(
            champion, maximize, minimum, maximum, require_sets, allowed_sets, use_other_champions_gear,
            never_take_from, exclude_artifacts, results, time_limit_seconds,
        )
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def sell_candidates(
    protected_artifact_ids: list[str] | None = None,
    keep_per_slot: int = 10,
    keep_per_set: int = 2,
    set_keep_min_score: float = 3.0,
    upgrade_chance: float = 0.3,
    limit: int = 50,
) -> dict[str, Any]:
    """Which unworn artifacts to sell, keep, or upgrade first, from the last account read (fast, no game access).

    Pass every artifact id in the player's saved builds as protected_artifact_ids (trogly-core `loadouts` list
    returns them); those are always kept. A piece is kept if it ranks in the vault's top keep_per_slot for its slot
    under any build profile (attack, hp, def, debuff, support), or in the top keep_per_set of its set and slot with
    a score of at least set_keep_min_score. A piece below level 16 with at least upgrade_chance odds of 5+ useful
    hits is listed as upgrade first. Everything else is sell, worst first, each with a reason. sell_ids lists every
    piece to sell. Raise keep_per_slot or keep_per_set to keep more.
    """
    try:
        return inventory_tools.sell_candidates(
            protected_artifact_ids, keep_per_slot, keep_per_set, set_keep_min_score, upgrade_chance, limit)
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def upgrade_candidates(
    profile: str = "",
    stats: list[str] | None = None,
    sets: list[str] | None = None,
    slots: list[str] | None = None,
    min_stars: int = 6,
    min_hits: int = 5,
    include_worn: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    """Artifacts below level 16 ranked by the odds that their remaining upgrades land on the wanted stats.

    Say what is wanted with profile (attack, hp, def, debuff, support) or stats (e.g. ["HP%", "SPD", "DEF%",
    "RES"]); with neither, each piece is judged for the build it suits best. Filter with sets (in-game names, e.g.
    ["Immortal"]), slots, min_stars. A hit is a useful substat or a useful roll into one; each candidate shows
    hits now, expected hits at level 16, and the chance of at least min_hits. Odds assume random rolls.
    """
    try:
        return inventory_tools.upgrade_candidates(
            profile, stats, sets, slots, min_stars, min_hits, include_worn, limit)
    except TroglyError as error:
        return {"error": str(error)}



@mcp.tool()
def set_advice(champion: str = "", encounter: str = "", top: int = 12) -> dict[str, Any]:
    """Which artifact sets are worth it, for a champion and an encounter (fast, no game access).

    value = tier weight (community tier list plus the player's changes) x fit for the champion x encounter
    multiplier. Fit drops for bonuses the champion can't use (ACC on a champion that places no debuffs; on-hit
    effects on a champion that deals no damage). encounter is a boss name ("Borgoth", "Fyro") or a key as in
    trogly-core `loadouts` (stage:<id>, clan_boss:ultra_nightmare, hydra, arena:classic); it reads the bosses' kits: sets whose debuff a boss is immune to are worth
    0, and sets that answer a boss mechanic (Destroy against a boss that needs its MAX HP destroyed) go to the top.
    With no champion, it's the plain tier list.
    """
    try:
        return set_tools.set_advice(champion, encounter, top)
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
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
) -> dict[str, Any]:
    """Stats or sets? Runs the optimiser with no set required, then with each candidate set (the best by
    set_advice, or the sets named), and says whether each set is worth what it costs in the maximized stat.

    The allowed cost depends on the player's gear stage (set_preferences): early = stats before sets, late = sets
    first. Takes up to a minute (one optimiser run per option). Look at the other stats too: the verdict only
    weighs the maximized stat, and minimums still hold.
    """
    try:
        return set_tools.compare_set_options(champion, maximize, minimum, encounter, sets, candidates,
                                             use_other_champions_gear, never_take_from, time_limit_seconds)
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def set_preferences(
    action: str = "show", set_name: str = "", tier: str = "", weight: float | None = None, gear_stage: str = "",
    encounter: str = "", boss: str = "", multiplier: float | None = None, note: str = "",
) -> dict[str, Any]:
    """The player's own view of artifact sets.

    action: show (tier list, gear stage, encounter rules), set_tier (set_name with tier S+/S/A/B/C/D or weight 0-1),
    reset_tier (back to the default), gear_stage (early, mid, or late: how much stat to give up for full sets),
    add_rule (encounter key or kind such as doom_tower or clan_boss, set_name, multiplier, optional boss and note:
    e.g. Destroy x2 vs boss Borgoth), or remove_rule (set_name, optional encounter).
    """
    try:
        return set_tools.set_preferences(action, set_name, tier, weight, gear_stage, encounter, boss, multiplier, note)
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def rank_set_pieces(set_name: str, profile: str = "", stats: list[str] | None = None, top: int = 3,
                    include_worn: bool = True) -> dict[str, Any]:
    """The best pieces of one set in every slot ("my best Stun set"), for a profile (attack, hp, def, debuff,
    support) or wanted stats. Shows each piece, its score, and who wears it. Build the exact loadout afterwards with
    optimize_gear and require_sets."""
    try:
        return inventory_tools.rank_set_pieces(set_name, profile, stats, top, include_worn)
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def optimize_team_gear(
    members: list[dict[str, Any]],
    use_other_champions_gear: bool = False,
    never_take_from: list[str] | None = None,
    time_limit_seconds: float = 10,
) -> dict[str, Any]:
    """Gear a whole team without using any piece twice. members is in priority order (carry first), each like
    optimize_gear's arguments: {"champion": "Thor", "maximize": "ATK", "minimum": {"SPD": 200},
    "require_sets": {"Savage": 4}}. Earlier champions get first pick. Shows every loadout and which pieces move
    between champions. One optimiser run per member."""
    try:
        return artifacts_tools.optimize_team_gear(members, use_other_champions_gear, never_take_from, time_limit_seconds)
    except TroglyError as error:
        return {"error": str(error)}


if __name__ == "__main__":
    mcp.run()
