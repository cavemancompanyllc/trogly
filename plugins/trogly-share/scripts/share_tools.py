"""Publishing a RAID account snapshot to trog.ly: build it, show what would be sent, then publish only when asked.

The snapshot is a sanitized copy of the account read (`data/state/trogly-state.json`), contract version 1:
champions with their calculated stats and (optionally) equipped gear, plus account level and power. It never contains
the game user id, the game's champion or artifact ids, resources, shards, locks, vault or bathhouse flags, or anything
about how the data was read. The site link, handle, and publish key live in `data/share.json` in the instance; the
key is a secret and is never shown back.

Stdlib only. `share_mcp.py` is the MCP wrapper.
"""

from __future__ import annotations

import gzip
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATE_RELATIVE_PATH = Path("data") / "state" / "trogly-state.json"
KNOWLEDGE_RELATIVE_PATH = Path("data") / "knowledge" / "knowledge.json"
SHARE_RELATIVE_PATH = Path("data") / "share.json"
DEFAULT_SITE = "https://trog.ly"
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30

SECTIONS = ("champions", "gear", "great_hall")
DEFAULT_SECTIONS = ("champions", "gear")
RARITIES = {1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary", 6: "Mythical"}
STATS = ("health", "attack", "defence", "speed", "resistance", "accuracy", "critical_chance", "critical_damage")

# What stays on this PC, listed in every preview so the player sees it.
NEVER_SENT = [
    "your game user id, and the game's champion and artifact ids (replaced with references like c1, a1)",
    "gems, silver, energy, and other resources",
    "shards",
    "locks, vault and bathhouse flags, and markers",
    "unequipped (vault) artifacts",
    "how the data was read (paths, Agent version)",
]


class ShareError(Exception):
    """A problem the player can act on."""


def instance_home() -> Path:
    return Path(os.environ.get("TROGLY_HOME") or Path.cwd())


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_state(home: Path) -> dict[str, Any]:
    path = home / STATE_RELATIVE_PATH
    if not path.exists():
        raise ShareError("No account data yet. Refresh the account with trogly-core's refresh_account first.")
    return _read_json(path)


def load_types(home: Path) -> dict[int, dict[str, Any]]:
    """Champion type names, rarity, faction, and affinity from the knowledge file, when it exists."""
    path = home / KNOWLEDGE_RELATIVE_PATH
    if not path.exists():
        return {}
    return {t["type_id"]: t for t in _read_json(path).get("champion_types") or []}


def build_snapshot(state: dict[str, Any], types: dict[int, dict[str, Any]] | None = None,
                   sections: tuple[str, ...] | list[str] = DEFAULT_SECTIONS) -> dict[str, Any]:
    """The sanitized snapshot, contract version 1. Game ids become per-snapshot references."""
    unknown = [s for s in sections if s not in SECTIONS]
    if unknown:
        raise ShareError(f"Unknown section(s): {', '.join(unknown)}. Choose from {', '.join(SECTIONS)}.")
    if "champions" not in sections:
        raise ShareError("A snapshot needs the champions section.")
    types = types or {}
    artifacts_by_id = {a["id"]: a for a in state.get("artifacts") or []}
    champions = sorted(state.get("champions") or [],
                       key=lambda c: (-c.get("stars", 0), -c.get("level", 0), c.get("name") or ""))

    artifact_refs: dict[str, str] = {}
    artifacts_out: list[dict[str, Any]] = []
    champions_out: list[dict[str, Any]] = []
    for index, champion in enumerate(champions, start=1):
        champion_type = types.get(champion.get("type_id")) or {}
        calculated = champion.get("calculated_stats") or {}
        total = calculated.get("total") or {}
        entry: dict[str, Any] = {
            "ref": f"c{index}",
            "type_id": champion.get("type_id"),
            "name": champion.get("name") or champion_type.get("name") or "",
            "rarity": champion_type.get("rarity") or RARITIES.get(champion.get("rarity"), None),
            "faction": champion_type.get("faction"),
            "affinity": champion_type.get("affinity"),
            "role": champion_type.get("role"),
            "stars": champion.get("stars", 0),
            "level": champion.get("level", 0),
            "awakening": champion.get("awakening_level") or 0,
            "empower": champion.get("empower_level") or 0,
            "blessing": champion.get("blessing_id") or None,
            "masteries": list(champion.get("masteries") or []),
            "skill_levels": dict(champion.get("skill_levels") or {}),
            "stats": {stat: round(total[stat], 4) for stat in STATS if stat in total},
            "sets": [{"name": s.get("name") or s.get("set"), "pieces": s.get("pieces"), "bonus": s.get("bonus")}
                     for s in calculated.get("sets") or [] if s.get("active")],
        }
        if "gear" in sections:
            gear = {}
            for slot, artifact_id in (champion.get("equipped_artifact_ids") or {}).items():
                artifact = artifacts_by_id.get(artifact_id)
                if artifact is None:
                    continue
                if artifact_id not in artifact_refs:
                    artifact_refs[artifact_id] = f"a{len(artifact_refs) + 1}"
                    artifacts_out.append(_artifact(artifact, artifact_refs[artifact_id]))
                gear[slot] = artifact_refs[artifact_id]
            entry["gear"] = gear
        champions_out.append(entry)

    account = state.get("account") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sections": [s for s in SECTIONS if s in sections],
        "account": {"level": account.get("level"), "power": round(account["total_power"]) if account.get("total_power") else None},
        "champions": champions_out,
        "artifacts": artifacts_out,
        "great_hall": state.get("great_hall") or None if "great_hall" in sections else None,
    }


def _artifact(artifact: dict[str, Any], ref: str) -> dict[str, Any]:
    def bonus(value: dict[str, Any]) -> dict[str, Any]:
        out = {"stat": value.get("stat"), "value": value.get("value"), "absolute": bool(value.get("is_absolute"))}
        if value.get("rolls"):
            out["rolls"] = value["rolls"]
        if value.get("glyph"):
            out["glyph"] = value["glyph"]
        return out

    return {
        "ref": ref,
        "slot": artifact.get("slot"),
        "set": artifact.get("set_name") or artifact.get("set"),
        "rank": artifact.get("rank"),
        "rarity": RARITIES.get(artifact.get("rarity"), artifact.get("rarity")),
        "level": artifact.get("level"),
        "primary": bonus(artifact.get("primary_stat") or {}),
        "substats": [bonus(s) for s in artifact.get("substats") or []],
    }


def encode(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def preview(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What would be sent: sections, counts, size, and what stays on this PC."""
    raw = encode(snapshot)
    top = snapshot["champions"][:5]
    return {
        "sections": snapshot["sections"],
        "champions": len(snapshot["champions"]),
        "equipped_artifacts": len(snapshot["artifacts"]),
        "account": snapshot["account"],
        "size_kb": round(len(raw) / 1024),
        "compressed_kb": round(len(gzip.compress(raw)) / 1024),
        "fits": len(raw) <= MAX_SNAPSHOT_BYTES,
        "first_champions": [f"{c['name']} {c['stars']}* L{c['level']}" for c in top],
        "never_sent": NEVER_SENT,
    }


# Site settings: data/share.json holds site_url, handle, and the publish key (a secret).

def load_share(home: Path) -> dict[str, Any]:
    path = home / SHARE_RELATIVE_PATH
    return _read_json(path) if path.exists() else {}


def save_share(home: Path, settings: dict[str, Any]) -> None:
    path = home / SHARE_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def site_url(settings: dict[str, Any]) -> str:
    return (os.environ.get("TROGLY_SITE_URL") or settings.get("site_url") or DEFAULT_SITE).rstrip("/")


def status(home: Path) -> dict[str, Any]:
    settings = load_share(home)
    return {
        "site": site_url(settings),
        "handle": settings.get("handle"),
        "has_publish_key": bool(settings.get("publish_key")),
        "share_url": settings.get("share_url"),
        "published_at": settings.get("published_at"),
    }


def _request(method: str, url: str, body: dict[str, Any] | bytes | None = None, key: str | None = None) -> dict[str, Any]:
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read().decode("utf-8")).get("error")
        except (ValueError, AttributeError):
            message = None
        raise ShareError(message or f"The site answered {error.code}.") from error
    except urllib.error.URLError as error:
        raise ShareError(f"Couldn't reach {url}: {error.reason}") from error


def redeem_invite(home: Path, code: str, handle: str, site: str | None = None) -> dict[str, Any]:
    settings = load_share(home)
    if site:
        settings["site_url"] = site.rstrip("/")
    answer = _request("POST", f"{site_url(settings)}/api/invites/redeem", {"code": code.strip(), "handle": handle.strip()})
    settings.update({"handle": answer["handle"], "publish_key": answer["publish_key"]})
    save_share(home, settings)
    return {"handle": answer["handle"], "saved": True,
            "note": "Your publish key is saved in data/share.json on this PC. Keep that file private."}


def publish(home: Path, sections: tuple[str, ...] | list[str] = DEFAULT_SECTIONS) -> dict[str, Any]:
    settings = load_share(home)
    if not settings.get("publish_key"):
        raise ShareError("No publish key yet. Ask a friend on trog.ly for an invite code, then use redeem_invite.")
    snapshot = build_snapshot(load_state(home), load_types(home), sections)
    raw = encode(snapshot)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ShareError(f"The snapshot is {round(len(raw) / 1024)} KB; the limit is 2 MB. Leave out a section.")
    answer = _request("POST", f"{site_url(settings)}/api/publish", raw, settings["publish_key"])
    settings.update({"share_url": answer["url"], "published_at": answer["published_at"]})
    save_share(home, settings)
    return {"url": answer["url"], "published_at": answer["published_at"], "champions": len(snapshot["champions"])}


def unpublish(home: Path) -> dict[str, Any]:
    settings = load_share(home)
    if not settings.get("publish_key"):
        raise ShareError("Nothing to unpublish from this PC: no publish key is saved.")
    _request("DELETE", f"{site_url(settings)}/api/publish", None, settings["publish_key"])
    save_share(home, {"site_url": settings.get("site_url")} if settings.get("site_url") else {})
    return {"deleted": True, "note": "Your profile, every snapshot, and your publish key are deleted from the site."}


def create_invite(home: Path) -> dict[str, Any]:
    settings = load_share(home)
    if not settings.get("publish_key"):
        raise ShareError("Only a publisher can invite. Redeem an invite first.")
    answer = _request("POST", f"{site_url(settings)}/api/invites", {}, settings["publish_key"])
    return {"code": answer["code"], "expires_at": answer["expires_at"],
            "note": "Send this code to one friend. It works once and expires in 14 days."}
