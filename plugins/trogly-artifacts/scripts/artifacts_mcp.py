#!/usr/bin/env python3
"""MCP server "trogly-artifacts": finds the best gear for a champion with the Trogly Agent's optimiser.

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


if __name__ == "__main__":
    mcp.run()
