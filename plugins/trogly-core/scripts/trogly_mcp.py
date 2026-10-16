#!/usr/bin/env python3
"""MCP server "trogly": lets Claude Code read the player's RAID account through the Trogly Agent.

Read-only toward the game. The Agent reads RAID's memory into `data/state/trogly-state.json` under the instance
(`TROGLY_HOME`); these tools answer questions from that file. Editing this file requires restarting the Claude Code
session, because MCP servers don't hot-reload.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trogly_state  # noqa: E402
from trogly_state import AccountState, TroglyError  # noqa: E402

mcp = FastMCP("trogly")


def _answer(action) -> dict[str, Any]:
    try:
        return action()
    except TroglyError as error:
        return {"error": str(error)}


@mcp.tool()
def refresh_account() -> dict[str, Any]:
    """Read the player's RAID account from the running game into Trogly (takes up to a minute).

    Use when there is no account data yet, when data is older than about 30 minutes, or when the player says they
    changed something in game. RAID must be open and logged in on this PC.
    """
    return _answer(lambda: trogly_state.refresh_account(os.environ.get("TROGLY_AGENT_PATH", "")))


@mcp.tool()
def account_summary() -> dict[str, Any]:
    """Account overview: level, power, champion and artifact counts, key resources, shards, and Great Hall levels."""
    return _answer(lambda: AccountState.load().account_summary())


@mcp.tool()
def find_champions(
    query: str = "", rarity: str = "", min_stars: int = 0, sort_by: str = "speed", limit: int = 25
) -> dict[str, Any]:
    """Search the player's champions.

    query: part of a champion name ("" for all). rarity: Common, Uncommon, Rare, Epic, Legendary, or Mythical.
    min_stars: 1-6. sort_by: speed, health, attack, defence, accuracy, resistance, critical_chance,
    critical_damage, level, or name. Returns compact rows with calculated total stats.
    """
    return _answer(lambda: AccountState.load().find_champions(query, rarity, min_stars, sort_by, limit))


@mcp.tool()
def get_champion(name_or_id: str) -> dict[str, Any]:
    """One champion in full: calculated base and total stats, every equipped artifact with substats, set pieces,
    awakening, blessing, and mastery count. Pass a name or an id from find_champions. If several copies match,
    the result lists them so you can ask which one."""
    return _answer(lambda: AccountState.load().get_champion(name_or_id))


@mcp.tool()
def find_artifacts(
    set_name: str = "",
    slot: str = "",
    main_stat: str = "",
    min_rank: int = 0,
    min_level: int = 0,
    equipped: str = "any",
    limit: int = 25,
) -> dict[str, Any]:
    """Search the player's artifacts and accessories.

    set_name: in-game name or code (Speed, Perception, Savage...). slot: Weapon, Helmet, Shield, Gloves, Chest,
    Boots, Ring, Amulet, Banner. main_stat: e.g. SPD, ATK%, C.RATE (a trailing % means the percent version).
    min_rank: stars 1-6. min_level: 0-16. equipped: any, yes, or no. Best pieces first, with who wears each.
    """
    return _answer(
        lambda: AccountState.load().find_artifacts(set_name, slot, main_stat, min_rank, min_level, equipped, limit)
    )


if __name__ == "__main__":
    mcp.run()
