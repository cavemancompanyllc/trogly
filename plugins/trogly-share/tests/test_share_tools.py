import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import share_tools  # noqa: E402
from share_tools import ShareError  # noqa: E402

STATE = {
    "user_id": "um160530640",
    "account": {"level": 83, "total_power": 4271538.44, "resources": {"Gem": 1626, "Silver": 3489577}, "shards": {"Mystery": 12}},
    "champions": [
        {"id": "14224", "name": "Arbiter", "type_id": 3566, "rarity": 5, "stars": 6, "level": 60, "locked": True,
         "in_storage": False, "in_bathhouse": False, "marker": "0", "awakening_level": 0, "empower_level": 0,
         "blessing_id": "", "masteries": ["500212"], "skill_levels": {"35601": 4},
         "equipped_artifact_ids": {"Weapon": "11813", "Boots": "18578"},
         "calculated_stats": {"total": {"health": 50544, "speed": 218, "critical_chance": 0.25},
                              "precise": {"speed": 218.2},
                              "sets": [{"set": "AttackSpeed", "name": "Speed", "pieces": 2, "active": True, "bonus": "2 Set: SPD +12%"},
                                       {"set": "Lifesteal", "name": "Lifesteal", "pieces": 1, "active": False, "bonus": "4 Set"}]}},
        {"id": "7", "name": "Warmaiden", "type_id": 100, "rarity": 2, "stars": 3, "level": 30, "equipped_artifact_ids": {}},
    ],
    "artifacts": [
        {"id": "11813", "set": "AttackSpeed", "set_name": "Speed", "slot": "Weapon", "rarity": 5, "rank": 6, "level": 16,
         "primary_stat": {"stat": "Attack", "value": 265, "is_absolute": True, "glyph": 0, "rolls": 0},
         "substats": [{"stat": "Speed", "value": 18, "is_absolute": True, "glyph": 2, "rolls": 3}]},
        {"id": "18578", "set": "AttackSpeed", "set_name": "Speed", "slot": "Boots", "rarity": 5, "rank": 6, "level": 16,
         "primary_stat": {"stat": "Speed", "value": 45, "is_absolute": True}, "substats": []},
        {"id": "999", "set": "Offense", "set_name": "Offense", "slot": "Helmet", "rarity": 4, "rank": 5, "level": 8,
         "primary_stat": {"stat": "Health", "value": 1000, "is_absolute": True}, "substats": []},
    ],
    "great_hall": {"Void": {"Health": 3}},
    "relics": [{"id": "1"}],
    "extraction": {"source": "direct-memory"},
}
TYPES = {3566: {"type_id": 3566, "name": "Arbiter", "rarity": "Legendary", "faction": "HighElves", "affinity": "Void", "role": "Support"}}


def test_snapshot_never_contains_private_fields():
    snapshot = share_tools.build_snapshot(STATE, TYPES)
    text = json.dumps(snapshot)
    for secret in ("um160530640", "14224", "11813", "18578", "Gem", "Silver", "Mystery", "locked", "in_storage",
                   "in_bathhouse", "marker", "direct-memory", "relics"):
        assert secret not in text, secret


def test_snapshot_uses_references_and_only_equipped_gear():
    snapshot = share_tools.build_snapshot(STATE, TYPES)
    arbiter = snapshot["champions"][0]
    assert arbiter["ref"] == "c1" and arbiter["name"] == "Arbiter" and arbiter["affinity"] == "Void"
    assert arbiter["gear"] == {"Weapon": "a1", "Boots": "a2"}
    assert [a["ref"] for a in snapshot["artifacts"]] == ["a1", "a2"]  # the unequipped helmet stays home
    assert snapshot["artifacts"][0]["primary"] == {"stat": "Attack", "value": 265, "absolute": True}
    assert snapshot["artifacts"][0]["substats"][0] == {"stat": "Speed", "value": 18, "absolute": True, "rolls": 3, "glyph": 2}
    assert arbiter["sets"] == [{"name": "Speed", "pieces": 2, "bonus": "2 Set: SPD +12%"}]
    assert arbiter["stats"]["speed"] == 218  # the champion screen's figure
    assert snapshot["account"] == {"level": 83, "power": 4271538}
    assert snapshot["great_hall"] is None


def test_sections_are_opt_in_and_validated():
    without_gear = share_tools.build_snapshot(STATE, TYPES, ["champions"])
    assert "gear" not in without_gear["champions"][0] and without_gear["artifacts"] == []
    with_hall = share_tools.build_snapshot(STATE, TYPES, ["champions", "gear", "great_hall"])
    assert with_hall["great_hall"] == {"Void": {"Health": 3}}
    with pytest.raises(ShareError):
        share_tools.build_snapshot(STATE, TYPES, ["gear"])
    with pytest.raises(ShareError):
        share_tools.build_snapshot(STATE, TYPES, ["champions", "resources"])


def test_preview_lists_what_stays_home():
    preview = share_tools.preview(share_tools.build_snapshot(STATE, TYPES))
    assert preview["champions"] == 2 and preview["equipped_artifacts"] == 2 and preview["fits"]
    assert any("resources" in item for item in preview["never_sent"])


class FakeSite(BaseHTTPRequestHandler):
    received: list = []

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        FakeSite.received.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/api/invites/redeem":
            if body["code"] != "GOOD":
                return self._reply(400, {"error": "That invite code isn't valid or has expired."})
            return self._reply(200, {"handle": body["handle"], "publish_key": "secret-key"})
        if self.path == "/api/publish":
            return self._reply(200, {"url": "http://site/s/abc", "published_at": "2026-10-18T10:00:00Z"})
        return self._reply(404, {"error": "no"})

    def do_DELETE(self):  # noqa: N802
        FakeSite.received.append((self.path, self.headers.get("Authorization"), None))
        self._reply(200, {"deleted": True})

    def log_message(self, *args):
        pass


@pytest.fixture
def site(tmp_path, monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), FakeSite)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeSite.received = []
    (tmp_path / "data" / "state").mkdir(parents=True)
    (tmp_path / "data" / "state" / "trogly-state.json").write_text(json.dumps(STATE), encoding="utf-8")
    monkeypatch.setenv("TROGLY_SITE_URL", f"http://127.0.0.1:{server.server_port}")
    yield tmp_path
    server.shutdown()


def test_redeem_publish_unpublish_round_trip(site):
    with pytest.raises(ShareError, match="isn't valid"):
        share_tools.redeem_invite(site, "BAD", "mike")
    with pytest.raises(ShareError, match="No publish key"):
        share_tools.publish(site)

    assert share_tools.redeem_invite(site, "GOOD", "mike")["handle"] == "mike"
    assert share_tools.status(site)["has_publish_key"]
    assert "secret-key" not in json.dumps(share_tools.status(site))

    published = share_tools.publish(site)
    assert published["url"] == "http://site/s/abc"
    path, auth, body = FakeSite.received[-1]
    assert (path, auth, body["schema_version"]) == ("/api/publish", "Bearer secret-key", 1)

    assert share_tools.unpublish(site)["deleted"]
    assert not share_tools.status(site)["has_publish_key"]
