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
from trogly_db import Ledger, with_ledger  # noqa: E402
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
    changed something in game. RAID must be open and logged in on this PC. Each refresh is also recorded in the
    account history used by account_diff.
    """

    def refresh() -> dict[str, Any]:
        result = trogly_state.refresh_account(os.environ.get("TROGLY_AGENT_PATH", ""))
        result["history"] = with_ledger(lambda ledger: ledger.import_snapshot(AccountState.load()))
        return result

    return _answer(refresh)


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


def _with_state(action) -> dict[str, Any]:
    """Runs a ledger action with the current account loaded, recording that account read in history first."""

    def run(ledger: Ledger) -> dict[str, Any]:
        state = AccountState.load()
        ledger.import_snapshot(state)
        return action(ledger, state)

    return _answer(lambda: with_ledger(run))


@mcp.tool()
def account_diff(since: str = "previous") -> dict[str, Any]:
    """What changed on the account: level, power, resources, shards, new and removed champions, and per-champion
    changes (stars, level, awakening, masteries, gear swaps, calculated stats).

    since: "previous" (the refresh before the latest), a date YYYY-MM-DD (the last refresh on or before it), or a
    snapshot id from account_history. Refresh first if the player wants changes up to right now.
    """
    return _with_state(lambda ledger, _: ledger.diff(since))


@mcp.tool()
def account_history(limit: int = 10) -> dict[str, Any]:
    """Recorded account refreshes, newest first, with their ids for account_diff."""
    return _with_state(lambda ledger, _: {"snapshots": ledger.history(limit)})


@mcp.tool()
def goals(
    action: str = "list", title: str = "", goal_id: int = 0, champion: str = "", target: str = "",
    status: str = "", notes: str = "",
) -> dict[str, Any]:
    """The player's RAID goals ("6-star Thor", "Phantom Shogun 20 by November").

    action: list (status: open, done, dropped, or all; default open), add (title required; champion, target, notes
    optional), or update (goal_id required; title, target, status, notes). Check open goals at the start of a
    planning conversation, and offer to add one when the player states something they're working towards.
    """

    def run(ledger: Ledger, state: AccountState) -> dict[str, Any]:
        champion_id = ""
        if champion:
            matches = state.resolve_champion(champion)
            if not matches:
                raise TroglyError(f"No champion matches '{champion}'.")
            champion_id = matches[0]["id"]
        return ledger.goals(action, title=title, goal_id=goal_id, champion_id=champion_id, target=target,
                            status=status, notes=notes)

    return _with_state(run)


@mcp.tool()
def teams(
    action: str = "list", name: str = "", champions: list[str] | None = None, roles: list[str] | None = None,
    purpose: str = "", notes: str = "",
) -> dict[str, Any]:
    """Saved teams, shown with each member's current calculated stats.

    action: list, get (name), save (name and champions as names or ids, in position order; roles, purpose such as
    "Phantom Shogun 20" or "Tag Arena team 1", and notes optional; saving an existing name replaces it), or delete
    (name). Offer to save a team once the player settles on one.
    """
    return _with_state(lambda ledger, state: ledger.teams(
        action, state, name=name, champions=champions or [], roles=roles or [], purpose=purpose, notes=notes))


@mcp.tool()
def loadouts(
    action: str = "list", champion: str = "", name: str = "", artifact_ids: list[str] | None = None,
    target_stats: str = "", notes: str = "",
) -> dict[str, Any]:
    """Saved gear plans per champion.

    action: list (optionally for one champion), save (champion and name; artifact_ids default to the gear the
    champion wears now; target_stats free text such as "SPD 230+, ACC 250+"), compare (champion and name: where
    each saved piece is now: equipped, in the vault, or worn by someone else), or delete.
    """
    return _with_state(lambda ledger, state: ledger.loadouts(
        action, state, champion=champion, name=name, artifact_ids=artifact_ids or [], target_stats=target_stats,
        notes=notes))


@mcp.tool()
def notes(action: str = "list", kind: str = "session", topic: str = "", body: str = "", next_steps: str = "",
          limit: int = 5) -> dict[str, Any]:
    """Notes that carry over between conversations.

    kind "session": at the end of a planning conversation, add what was worked on (body) and what to do next
    (next_steps); at the start of one, list the latest. kind "decision": record a choice and why (topic, body), for
    example why a champion was built for Clan Boss rather than Arena. action: list or add.
    """
    return _answer(lambda: with_ledger(lambda ledger: ledger.notes(
        action, kind=kind, topic=topic, body=body, next_steps=next_steps, limit=limit)))


if __name__ == "__main__":
    mcp.run()
