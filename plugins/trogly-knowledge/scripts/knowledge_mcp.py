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
def find_stage(location: str, stage: int | None = None, difficulty: str = "", enemies: list[str] | None = None) -> dict[str, Any]:
    """A dungeon or other stage from the game's own data: enemies by wave, the boss's full kit, stage modifiers
    (the boss's extra stats), and how many champions the player brings. location: dungeon, region, or boss name
    (e.g. "Phantom Shogun", "Dragon", "Ice Golem", "Doom Tower"). stage: the stage number. difficulty: Normal, Hard,
    Brutal, or Nightmare where the place has them (most dungeons have one track). With a stage, `encounter` is the
    key to save builds under (trogly-core `loadouts`). enemies: a few enemy names the player sees, to pick between
    places that share a name, such as Doom Tower's rotations; when several still match, the answer lists each one's
    lineup for that stage."""
    return _answer(lambda: Knowledge.load().find_stage(location, stage, difficulty, enemies))


@mcp.tool()
def team_candidates(location: str, stage: int, difficulty: str = "", min_stars: int = 6, limit: int = 40,
                    enemies: list[str] | None = None) -> dict[str, Any]:
    """Everything needed to pick a team for one stage: team size, waves, the boss's full kit and affinity matchup,
    stage modifiers, enemy affinities per wave, and the player's owned champions (best copy of each, min_stars and up)
    ranked by how they hit the boss (strong, normal, weak), or on a stage without a boss by how many of the wave
    enemies they hit strong or weak (hits_on_enemies), then awakening and stars. Each candidate has calculated stats,
    masteries by name (capstone = tier 6), blessing (in-game name and skill text when known, else the game code),
    leader skill with works_here for this stage's area,
    and a kit summary: skills with cooldowns, buffs it places on allies, debuffs on enemies, other abilities (heal,
    revive, cleanse, turn meter, ...), and what its damage scales with. enemies: as for find_stage, to pick Doom
    Tower's rotation."""
    return _answer(lambda: Knowledge.load().team_candidates(location, stage, difficulty, min_stars, limit,
                                                            enemies=enemies))


@mcp.tool()
def arena_candidates(mode: str = "classic", min_stars: int = 5, limit: int = 60) -> dict[str, Any]:
    """The player's champions for arena teams, fastest first. mode: classic (one team of 4) or tag (three teams of
    3, no champion twice). Each candidate has calculated stats, its aura and whether it works in arena, its turn meter
    and SPD skills with booked cooldowns, and a kit summary. Comes with the mode's team rules."""
    return _answer(lambda: Knowledge.load().arena_candidates(mode, min_stars, limit))


@mcp.tool()
def speed_tune(
    team: list[dict[str, Any]],
    area: str = "Arena",
    enemies: list[dict[str, Any]] | None = None,
    turns: int = 12,
) -> dict[str, Any]:
    """Turn order for one of the player's teams, played out tick by tick the way the game does it: SPD with the
    leader's aura, turn meter fills and reductions, Increase/Decrease SPD, extra turns, and booked cooldowns.

    team: the champions in slot order, leader first, each {"champion": name or id} plus optional "priority" (skills
    in the order the champion should use them, e.g. ["A3", "A1"]), "target" (ally for ally-target skills), and
    "speed" (try a different SPD, e.g. after a gear change). area: where the fight is, for the aura: Arena (Classic),
    Arena3X3 (Tag), LiveArena, Dungeon, or "" for none. enemies: optional [{"name", "speed"}] to see who moves first;
    they only use basic attacks. turns: how many turns to play out (all sides together).
    """
    return _answer(lambda: Knowledge.load().speed_tune(team, area, enemies, turns))


@mcp.tool()
def clan_boss_teams(
    teams: list[Any] | None = None,
    count: int = 5,
    runs: int = 10,
    difficulty: str = "Nightmare",
    affinity: str = "Void",
    analysis: str = "claude",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Simulates Clan Boss battles in code (no AI per turn) from the game's own rules and data, and writes an HTML
    report to publish as an Artifact.

    teams: optional list of teams to compare; each is a list of champion names (leader first) or
    {"label", "champions": [...]}, and a champion can be {"champion", "priority": ["A3", "A1"], "never": ["A2"],
    "delay": {"A3": 2}} to set its skill order. Without teams, Trogly suggests `count` teams from the roster: it plays
    every five-champion team from the best boss champions a few times and keeps the strongest distinct ones (include /
    exclude force or forbid champions). runs: battles per team (seeded, so teams face the same dice). difficulty: Easy,
    Normal, Hard, Brutal, Nightmare, UltraNightmare. affinity: Void, Magic, Force, Spirit. analysis: "claude" (you
    explain the returned digest), "ollama" (the local model writes the analysis; relay it), or "opus" (give the digest
    to the battle-analyst agent). Publish report_file with the Artifact tool as is.
    """
    return _answer(lambda: Knowledge.load().clan_boss_teams(teams, count, runs, difficulty, affinity, analysis, include,
                                                            exclude, title))


@mcp.tool()
def refresh_knowledge() -> dict[str, Any]:
    """Re-read champion kits and stages from the game. Needed only after a game update or when new champions are
    pulled (kits for newly owned champions); takes a few seconds with RAID open."""
    return _answer(lambda: knowledge_tools.export_knowledge(knowledge_tools.instance_home()))


if __name__ == "__main__":
    mcp.run()
