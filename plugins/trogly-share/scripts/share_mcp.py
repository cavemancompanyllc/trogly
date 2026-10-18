#!/usr/bin/env python3
"""MCP server "trogly-share": publish a sanitized snapshot of the player's account to trog.ly and share the link.

Publishing is explicit: preview_publish shows what would be sent; publish_profile needs confirm=true. Editing this
file requires restarting the Claude Code session, because MCP servers don't hot-reload.
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
import share_tools  # noqa: E402
from share_tools import ShareError  # noqa: E402

mcp = FastMCP("trogly-share")


def _answer(action) -> dict[str, Any]:
    try:
        return action()
    except ShareError as error:
        return {"error": str(error)}


@mcp.tool()
def share_status() -> dict[str, Any]:
    """Whether this PC can publish (has a handle and publish key), the site, and the current share link."""
    return _answer(lambda: share_tools.status(share_tools.instance_home()))


@mcp.tool()
def preview_publish(sections: list[str] | None = None) -> dict[str, Any]:
    """What publishing would send, without sending anything: sections, champion and gear counts, size, and the list of
    things that never leave this PC. sections: champions (always), gear (equipped artifacts), great_hall (opt-in).
    Default: champions and gear."""
    def run() -> dict[str, Any]:
        home = share_tools.instance_home()
        chosen = sections or list(share_tools.DEFAULT_SECTIONS)
        snapshot = share_tools.build_snapshot(share_tools.load_state(home), share_tools.load_types(home), chosen)
        return share_tools.preview(snapshot)

    return _answer(run)


@mcp.tool()
def publish_profile(confirm: bool = False, sections: list[str] | None = None) -> dict[str, Any]:
    """Publish the account snapshot and return the share link. Only after the player has seen preview_publish and said
    yes: pass confirm=true. Replaces the previous snapshot (the site keeps the last three)."""
    if not confirm:
        return {"error": "Not published. Show the player preview_publish first, then call again with confirm=true."}
    return _answer(lambda: share_tools.publish(share_tools.instance_home(), sections or list(share_tools.DEFAULT_SECTIONS)))


@mcp.tool()
def unpublish_profile(confirm: bool = False) -> dict[str, Any]:
    """Delete the player's profile, every snapshot, and their publish key from the site. Permanent: pass confirm=true
    only after the player says so."""
    if not confirm:
        return {"error": "Not deleted. This is permanent; ask the player, then call again with confirm=true."}
    return _answer(lambda: share_tools.unpublish(share_tools.instance_home()))


@mcp.tool()
def redeem_invite(code: str, handle: str, site: str = "") -> dict[str, Any]:
    """Join trog.ly with an invite code from a friend and choose a handle (3-24 characters: lowercase letters, digits,
    _ and -). Saves the publish key on this PC. site: only for testing another server."""
    return _answer(lambda: share_tools.redeem_invite(share_tools.instance_home(), code, handle, site or None))


@mcp.tool()
def create_invite() -> dict[str, Any]:
    """A one-use invite code (valid 14 days) the player can send a friend so they can publish too."""
    return _answer(lambda: share_tools.create_invite(share_tools.instance_home()))


if __name__ == "__main__":
    mcp.run()
