"""Trogly's ledger: account history, goals, teams, loadouts, and notes in `data/trogly.db` (stdlib sqlite3).

trogly-core is the only code that opens this database. Other plugins reach it through trogly-core's MCP tools.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trogly_state import (
    CALCULATED_STATS,
    KEY_RESOURCES,
    AccountState,
    TroglyError,
    instance_home,
    rarity_name,
)

DB_RELATIVE_PATH = Path("data") / "trogly.db"
GOAL_STATUSES = ("open", "done", "dropped")
NOTE_KINDS = ("decision", "session")
DIFF_STATS = ("speed", "health", "attack", "defence", "accuracy", "resistance", "critical_chance", "critical_damage")

# Each entry upgrades the schema by one version. Never edit an entry once released; add a new one.
MIGRATIONS = [
    """
    CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        read_at TEXT NOT NULL UNIQUE,
        imported_at TEXT NOT NULL,
        level INTEGER,
        total_power INTEGER,
        champion_count INTEGER NOT NULL,
        artifact_count INTEGER NOT NULL,
        resources_json TEXT NOT NULL,
        shards_json TEXT NOT NULL
    );
    CREATE TABLE snapshot_champions (
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
        champion_id TEXT NOT NULL,
        name TEXT NOT NULL,
        rarity INTEGER,
        stars INTEGER,
        level INTEGER,
        awakening_level INTEGER,
        empower_level INTEGER,
        gear_ids_json TEXT NOT NULL,
        mastery_count INTEGER,
        stats_json TEXT,
        PRIMARY KEY (snapshot_id, champion_id)
    );
    CREATE TABLE goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        champion_id TEXT,
        target TEXT,
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dropped')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        purpose TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE team_members (
        team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        champion_id TEXT NOT NULL,
        role TEXT,
        PRIMARY KEY (team_id, position)
    );
    CREATE TABLE loadouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        champion_id TEXT NOT NULL,
        name TEXT NOT NULL,
        artifact_ids_json TEXT NOT NULL,
        target_stats TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (champion_id, name)
    );
    CREATE TABLE notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK (kind IN ('decision', 'session')),
        topic TEXT,
        body TEXT NOT NULL,
        next_steps TEXT,
        created_at TEXT NOT NULL
    );
    """,
]


def db_path(home: Path | None = None) -> Path:
    return (home or instance_home()) / DB_RELATIVE_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, home: Path | None = None):
        self.home = home or instance_home()
        path = db_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.connection:
                self.connection.executescript(script)
                self.connection.execute(f"PRAGMA user_version = {index}")

    # Snapshots and history

    def import_snapshot(self, state: AccountState) -> dict[str, Any]:
        """Stores one account read. Reads already imported (same read time) are skipped."""
        read_at = state.raw.get("last_updated")
        if not read_at:
            raise TroglyError("The account data has no read time, so it can't be added to history.")
        existing = self.connection.execute("SELECT id FROM snapshots WHERE read_at = ?", (read_at,)).fetchone()
        if existing:
            return {"snapshot_id": existing["id"], "imported": False}
        account = state.raw.get("account") or {}
        resources = account.get("resources") or {}
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO snapshots (read_at, imported_at, level, total_power, champion_count, artifact_count,"
                " resources_json, shards_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    read_at,
                    _now(),
                    account.get("level"),
                    round(account.get("total_power") or 0),
                    len(state.champions),
                    len(state.artifacts_by_id),
                    json.dumps({key: round(resources[key]) for key in KEY_RESOURCES if key in resources}),
                    json.dumps(account.get("shards") or {}),
                ),
            )
            snapshot_id = cursor.lastrowid
            self.connection.executemany(
                "INSERT INTO snapshot_champions (snapshot_id, champion_id, name, rarity, stars, level, awakening_level,"
                " empower_level, gear_ids_json, mastery_count, stats_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id,
                        champion["id"],
                        champion.get("name", ""),
                        champion.get("rarity"),
                        champion.get("stars"),
                        champion.get("level"),
                        champion.get("awakening_level"),
                        champion.get("empower_level"),
                        json.dumps(sorted((champion.get("equipped_artifact_ids") or {}).values())),
                        len(champion.get("masteries") or []),
                        json.dumps((champion.get("calculated_stats") or {}).get("total")),
                    )
                    for champion in state.champions
                ],
            )
        return {"snapshot_id": snapshot_id, "imported": True}

    def history(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, read_at, level, total_power, champion_count, artifact_count FROM snapshots"
            " ORDER BY read_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def diff(self, since: str = "previous") -> dict[str, Any]:
        """Compares the latest snapshot with an earlier one: 'previous', a snapshot id, or a date (YYYY-MM-DD)."""
        latest = self.connection.execute("SELECT * FROM snapshots ORDER BY read_at DESC LIMIT 1").fetchone()
        if not latest:
            raise TroglyError("No account history yet. Refresh the account to record the first snapshot.")
        earlier = self._resolve_earlier(latest, since)
        if not earlier:
            return {
                "latest": latest["read_at"],
                "message": "Only one snapshot is recorded so far. Refresh again later to see what changed.",
            }
        old = self._champions(earlier["id"])
        new = self._champions(latest["id"])
        changed = []
        for champion_id, now in new.items():
            before = old.get(champion_id)
            if before is None:
                continue
            changes = _champion_changes(before, now)
            if changes:
                changed.append({"id": champion_id, "name": now["name"], "changes": changes})
        added = [_summary_row(new[i]) for i in new.keys() - old.keys()]
        removed = [_summary_row(old[i]) for i in old.keys() - new.keys()]
        return {
            "from": earlier["read_at"],
            "to": latest["read_at"],
            "account": {
                "level": _delta(earlier["level"], latest["level"]),
                "total_power": _delta(earlier["total_power"], latest["total_power"]),
                "champions": _delta(earlier["champion_count"], latest["champion_count"]),
                "artifacts": _delta(earlier["artifact_count"], latest["artifact_count"]),
                "resources": _dict_delta(json.loads(earlier["resources_json"]), json.loads(latest["resources_json"])),
                "shards": _dict_delta(json.loads(earlier["shards_json"]), json.loads(latest["shards_json"])),
            },
            "champions_added": sorted(added, key=lambda c: (-(c["stars"] or 0), c["name"])),
            "champions_removed": sorted(removed, key=lambda c: c["name"]),
            "champions_changed": sorted(changed, key=lambda c: c["name"]),
            "note": "Stat changes come from Trogly's calculated stats.",
        }

    def _resolve_earlier(self, latest: sqlite3.Row, since: str) -> sqlite3.Row | None:
        since = (since or "previous").strip()
        if since == "previous":
            query, args = "SELECT * FROM snapshots WHERE read_at < ? ORDER BY read_at DESC LIMIT 1", (latest["read_at"],)
        elif since.isdigit():
            query, args = "SELECT * FROM snapshots WHERE id = ? AND id != ?", (int(since), latest["id"])
        else:
            # The last snapshot taken on or before the given date.
            query, args = (
                "SELECT * FROM snapshots WHERE substr(read_at, 1, 10) <= ? AND id != ? ORDER BY read_at DESC LIMIT 1",
                (since[:10], latest["id"]),
            )
        return self.connection.execute(query, args).fetchone()

    def _champions(self, snapshot_id: int) -> dict[str, sqlite3.Row]:
        rows = self.connection.execute("SELECT * FROM snapshot_champions WHERE snapshot_id = ?", (snapshot_id,))
        return {row["champion_id"]: row for row in rows}

    # Goals

    def goals(self, action: str, **fields: Any) -> dict[str, Any]:
        if action == "list":
            status = fields.get("status") or "open"
            query = "SELECT * FROM goals" + ("" if status == "all" else " WHERE status = ?") + " ORDER BY created_at"
            rows = self.connection.execute(query, () if status == "all" else (status,)).fetchall()
            return {"goals": [dict(row) for row in rows]}
        if action == "add":
            if not fields.get("title"):
                raise TroglyError("A goal needs a title.")
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT INTO goals (title, champion_id, target, notes, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (fields["title"], fields.get("champion_id") or None, fields.get("target") or None,
                     fields.get("notes") or None, _now(), _now()),
                )
            return {"added": self._goal(cursor.lastrowid)}
        if action == "update":
            goal_id = fields.get("goal_id")
            if not goal_id or not self._goal(goal_id):
                raise TroglyError(f"No goal with id {goal_id}. List goals to find its id.")
            status = fields.get("status")
            if status and status not in GOAL_STATUSES:
                raise TroglyError(f"Status must be one of: {', '.join(GOAL_STATUSES)}.")
            updates = {k: v for k, v in fields.items() if k in ("title", "target", "status", "notes") and v}
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                with self.connection:
                    self.connection.execute(
                        f"UPDATE goals SET {assignments}, updated_at = ? WHERE id = ?",
                        (*updates.values(), _now(), goal_id),
                    )
            return {"updated": self._goal(goal_id)}
        raise TroglyError("Goal action must be list, add, or update.")

    def _goal(self, goal_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return dict(row) if row else None

    # Teams

    def teams(self, action: str, state: AccountState | None = None, **fields: Any) -> dict[str, Any]:
        if action == "list":
            rows = self.connection.execute(
                "SELECT t.id, t.name, t.purpose, t.updated_at, COUNT(m.position) AS members FROM teams t"
                " LEFT JOIN team_members m ON m.team_id = t.id GROUP BY t.id ORDER BY t.name"
            ).fetchall()
            return {"teams": [dict(row) for row in rows]}
        name = (fields.get("name") or "").strip()
        if not name:
            raise TroglyError("Give the team's name.")
        if action == "save":
            if state is None:
                raise TroglyError("Account data is needed to save a team.")
            members = fields.get("champions") or []
            if not members:
                raise TroglyError("A team needs at least one champion.")
            resolved = [_resolve_one(state, member) for member in members]
            roles = fields.get("roles") or []
            with self.connection:
                existing = self.connection.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
                if existing:
                    team_id = existing["id"]
                    self.connection.execute(
                        "UPDATE teams SET purpose = ?, notes = ?, updated_at = ? WHERE id = ?",
                        (fields.get("purpose") or None, fields.get("notes") or None, _now(), team_id),
                    )
                    self.connection.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
                else:
                    team_id = self.connection.execute(
                        "INSERT INTO teams (name, purpose, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (name, fields.get("purpose") or None, fields.get("notes") or None, _now(), _now()),
                    ).lastrowid
                self.connection.executemany(
                    "INSERT INTO team_members (team_id, position, champion_id, role) VALUES (?, ?, ?, ?)",
                    [(team_id, i + 1, c["id"], roles[i] if i < len(roles) else None) for i, c in enumerate(resolved)],
                )
            return {"saved": self._team(name, state)}
        if action == "get":
            team = self._team(name, state)
            if not team:
                raise TroglyError(f"No saved team called '{name}'. List teams to see the names.")
            return team
        if action == "delete":
            with self.connection:
                deleted = self.connection.execute("DELETE FROM teams WHERE name = ?", (name,)).rowcount
            if not deleted:
                raise TroglyError(f"No saved team called '{name}'.")
            return {"deleted": name}
        raise TroglyError("Team action must be list, get, save, or delete.")

    def _team(self, name: str, state: AccountState | None) -> dict[str, Any] | None:
        team = self.connection.execute("SELECT * FROM teams WHERE name = ?", (name,)).fetchone()
        if not team:
            return None
        members = []
        for row in self.connection.execute(
            "SELECT * FROM team_members WHERE team_id = ? ORDER BY position", (team["id"],)
        ):
            champion = _champion_by_id(state, row["champion_id"])
            members.append(
                {
                    "position": row["position"],
                    "role": row["role"],
                    **(state.champion_row(champion) if champion else {"id": row["champion_id"], "missing": True}),
                }
            )
        return {**dict(team), "members": members}

    # Loadouts

    def loadouts(self, action: str, state: AccountState | None = None, **fields: Any) -> dict[str, Any]:
        if state is None:
            raise TroglyError("Account data is needed for loadouts.")
        champion_ref = fields.get("champion")
        champion = _resolve_one(state, champion_ref) if champion_ref else None
        if action == "list":
            query, args = "SELECT * FROM loadouts", ()
            if champion:
                query, args = "SELECT * FROM loadouts WHERE champion_id = ?", (champion["id"],)
            rows = self.connection.execute(query + " ORDER BY champion_id, name", args).fetchall()
            return {"loadouts": [_loadout_row(row, state) for row in rows]}
        if not champion:
            raise TroglyError("Say which champion the loadout is for.")
        name = (fields.get("name") or "").strip()
        if not name:
            raise TroglyError("Give the loadout a name, for example 'Clan Boss' or 'Arena'.")
        if action == "save":
            artifact_ids = fields.get("artifact_ids") or list((champion.get("equipped_artifact_ids") or {}).values())
            unknown = [i for i in artifact_ids if i not in state.artifacts_by_id]
            if unknown:
                raise TroglyError(f"These artifact ids are not on the account: {', '.join(unknown)}.")
            with self.connection:
                self.connection.execute(
                    "INSERT INTO loadouts (champion_id, name, artifact_ids_json, target_stats, notes, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (champion_id, name) DO UPDATE SET"
                    " artifact_ids_json = excluded.artifact_ids_json, target_stats = excluded.target_stats,"
                    " notes = excluded.notes",
                    (champion["id"], name, json.dumps(artifact_ids), fields.get("target_stats") or None,
                     fields.get("notes") or None, _now()),
                )
            return {"saved": self._loadout(champion["id"], name, state)}
        if action == "compare":
            loadout = self._loadout(champion["id"], name, state)
            if not loadout:
                raise TroglyError(f"{champion['name']} has no loadout called '{name}'.")
            return loadout
        if action == "delete":
            with self.connection:
                deleted = self.connection.execute(
                    "DELETE FROM loadouts WHERE champion_id = ? AND name = ?", (champion["id"], name)
                ).rowcount
            if not deleted:
                raise TroglyError(f"{champion['name']} has no loadout called '{name}'.")
            return {"deleted": name, "champion": champion["name"]}
        raise TroglyError("Loadout action must be list, save, compare, or delete.")

    def _loadout(self, champion_id: str, name: str, state: AccountState) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM loadouts WHERE champion_id = ? AND name = ?", (champion_id, name)
        ).fetchone()
        if not row:
            return None
        champion = _champion_by_id(state, champion_id)
        worn = set((champion or {}).get("equipped_artifact_ids", {}).values())
        pieces = []
        for artifact_id in json.loads(row["artifact_ids_json"]):
            artifact = state.artifacts_by_id.get(artifact_id)
            owner = state.owner_by_artifact.get(artifact_id)
            if not artifact:
                status = "no longer on the account"
            elif artifact_id in worn:
                status = "equipped"
            elif owner:
                status = f"worn by {owner['name']} (id {owner['id']})"
            else:
                status = "in the vault"
            pieces.append({**(state.artifact_detail(artifact, include_owner=False) if artifact else {"id": artifact_id}),
                           "status": status})
        return {
            **_loadout_row(row, state),
            "pieces": pieces,
            "fully_equipped": all(p["status"] == "equipped" for p in pieces),
        }

    # Notes

    def notes(self, action: str, **fields: Any) -> dict[str, Any]:
        if action == "list":
            kind = fields.get("kind") or "session"
            limit = int(fields.get("limit") or 5)
            rows = self.connection.execute(
                "SELECT * FROM notes WHERE kind = ? ORDER BY created_at DESC, id DESC LIMIT ?", (kind, limit)
            ).fetchall()
            return {"notes": [dict(row) for row in rows]}
        if action == "add":
            kind = fields.get("kind") or "session"
            if kind not in NOTE_KINDS:
                raise TroglyError(f"Note kind must be one of: {', '.join(NOTE_KINDS)}.")
            if not fields.get("body"):
                raise TroglyError("A note needs a body.")
            with self.connection:
                cursor = self.connection.execute(
                    "INSERT INTO notes (kind, topic, body, next_steps, created_at) VALUES (?, ?, ?, ?, ?)",
                    (kind, fields.get("topic") or None, fields["body"], fields.get("next_steps") or None, _now()),
                )
            return {"added": dict(self.connection.execute("SELECT * FROM notes WHERE id = ?", (cursor.lastrowid,)).fetchone())}
        raise TroglyError("Note action must be list or add.")


def with_ledger(action, home: Path | None = None):
    with closing(Ledger(home)) as ledger:
        return action(ledger)


def _resolve_one(state: AccountState, reference: str) -> dict[str, Any]:
    matches = state.resolve_champion(str(reference))
    if not matches:
        raise TroglyError(f"No champion matches '{reference}'.")
    if len(matches) > 1 and not any(c.get("name", "").lower() == str(reference).lower() for c in matches):
        names = ", ".join(sorted({c["name"] for c in matches})[:8])
        raise TroglyError(f"'{reference}' matches several champions ({names}). Use the full name or the id.")
    # Several copies of the same champion: the most built one.
    return matches[0]


def _champion_by_id(state: AccountState | None, champion_id: str) -> dict[str, Any] | None:
    if state is None:
        return None
    return next((c for c in state.champions if c.get("id") == champion_id), None)


def _loadout_row(row: sqlite3.Row, state: AccountState) -> dict[str, Any]:
    champion = _champion_by_id(state, row["champion_id"])
    return {
        "champion": champion["name"] if champion else f"id {row['champion_id']} (no longer on the account)",
        "champion_id": row["champion_id"],
        "name": row["name"],
        "pieces_saved": len(json.loads(row["artifact_ids_json"])),
        "target_stats": row["target_stats"],
        "notes": row["notes"],
        "created_at": row["created_at"],
    }


def _summary_row(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["champion_id"], "name": row["name"], "rarity": rarity_name(row["rarity"] or 0),
            "stars": row["stars"], "level": row["level"]}


def _champion_changes(before: sqlite3.Row, now: sqlite3.Row) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for column in ("stars", "level", "awakening_level", "empower_level", "mastery_count"):
        if before[column] != now[column]:
            changes[column] = _delta(before[column], now[column])
    old_gear, new_gear = set(json.loads(before["gear_ids_json"])), set(json.loads(now["gear_ids_json"]))
    if old_gear != new_gear:
        changes["gear"] = {"pieces_changed": len(new_gear - old_gear), "pieces_removed": len(old_gear - new_gear)}
    old_stats = json.loads(before["stats_json"] or "null") or {}
    new_stats = json.loads(now["stats_json"] or "null") or {}
    labels = {key: (label, is_percent) for key, label, is_percent in CALCULATED_STATS}
    stat_changes = {}
    for key in DIFF_STATS:
        old_value, new_value = old_stats.get(key), new_stats.get(key)
        if old_value is None or new_value is None:
            continue
        label, is_percent = labels[key]
        scale = 100 if is_percent else 1
        if round(old_value * scale) != round(new_value * scale):
            stat_changes[label] = _delta(round(old_value * scale), round(new_value * scale))
    if stat_changes:
        changes["stats"] = stat_changes
    return changes


def _delta(old: Any, new: Any) -> dict[str, Any]:
    result = {"from": old, "to": new}
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        result["change"] = new - old
    return result


def _dict_delta(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {key: _delta(old.get(key, 0), new.get(key, 0)) for key in sorted(old.keys() | new.keys())
            if old.get(key, 0) != new.get(key, 0)}
