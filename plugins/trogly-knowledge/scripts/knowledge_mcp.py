#!/usr/bin/env python3
"""MCP server "trogly-knowledge": champion kits and stage details from RAID's own static data.

Read-only toward the game. Needs trogly-core for the saved Agent location. Editing this file requires restarting the
Claude Code session, because MCP servers don't hot-reload.
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
import knowledge_tools  # noqa: E402
from knowledge_tools import Knowledge, TroglyError  # noqa: E402

mcp = FastMCP("trogly-knowledge")


def _answer(action) -> dict[str, Any]:
    try:
        return action()
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def champion_kit(champion: str) -> dict[str, Any]:
    """A champion's skills from the game's own data: name, cooldown, the in-game description, damage formulas, and
    every buff or debuff with its chance and turns. Works for any champion, owned or not, and for bosses."""
    return _answer(lambda: Knowledge.load().champion_kit(champion))


@mcp.tool()
def search_kits(effect: str, owned_only: bool = True, limit: int = 25) -> dict[str, Any]:
    """Find champions whose skills do something, e.g. "Decrease DEF", "Increase SPD", "Revive", "Enfeeble",
    "Block Buffs", "Heal". By default only the player's own champions, with their stars and levels, best first."""
    return _answer(lambda: Knowledge.load().search_kits(effect, owned_only, limit))


@mcp.tool()
def find_stage(location: str, stage: int | None = None, difficulty: str = "") -> dict[str, Any]:
    """A dungeon or other stage from the game's own data: enemies by wave, the boss's full kit, stage modifiers
    (the boss's extra stats), and how many champions the player brings. location: dungeon, region, or boss name
    (e.g. "Phantom Shogun", "Dragon", "Ice Golem", "Doom Tower"). stage: the stage number. difficulty: Normal, Hard,
    Brutal, or Nightmare where the place has them (most dungeons have one track)."""
    return _answer(lambda: Knowledge.load().find_stage(location, stage, difficulty))


@mcp.tool()
def refresh_knowledge() -> dict[str, Any]:
    """Re-read champion kits and stages from the game. Needed only after a game update or when new champions are
    pulled (kits for newly owned champions); takes a few seconds with RAID open."""
    return _answer(lambda: knowledge_tools.export_knowledge(knowledge_tools.instance_home()))


if __name__ == "__main__":
    mcp.run()
