import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters")

from app.main import (  # noqa: E402
    RELAY_PORT,
    allocate_relay_port,
    normalize_relay_ports,
    relay_port_is_available,
    relay_health_payload,
    allocate_from_relay_candidates,
)


def timestamp(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class RelayPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE leases (
                   id TEXT PRIMARY KEY,
                   relay_port INTEGER,
                   expires_at TEXT NOT NULL,
                   revoked_at TEXT
               )"""
        )
        self.connection.execute(
            """CREATE TABLE relay_health (
                   id INTEGER PRIMARY KEY,
                   last_seen_at TEXT NOT NULL,
                   process_running INTEGER NOT NULL,
                   listen_ports TEXT NOT NULL,
                   duplicate_ports TEXT NOT NULL,
                   lease_count INTEGER NOT NULL,
                   error TEXT
               )"""
        )

    def tearDown(self) -> None:
        self.connection.close()

    def add_lease(
        self,
        lease_id: str,
        port: int | None,
        *,
        expires_in: float = 1,
        revoked: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO leases(id, relay_port, expires_at, revoked_at) VALUES (?, ?, ?, ?)",
            (lease_id, port, timestamp(expires_in), revoked),
        )

    def test_normalize_releases_stale_and_duplicate_ports(self) -> None:
        self.add_lease("kept", 30003)
        self.add_lease("duplicate", 30003)
        self.add_lease("expired", 30004, expires_in=-1)
        self.add_lease("reserved", RELAY_PORT)

        normalize_relay_ports(self.connection)

        rows = {
            row["id"]: row["relay_port"]
            for row in self.connection.execute("SELECT id, relay_port FROM leases")
        }
        self.assertEqual(rows["kept"], 30003)
        self.assertIsNone(rows["duplicate"])
        self.assertIsNone(rows["expired"])
        self.assertIsNone(rows["reserved"])

    def test_allocator_ignores_expired_ports_and_skips_reserved_port(self) -> None:
        self.add_lease("active", 30000)
        self.add_lease("expired", 30001, expires_in=-1)

        self.assertEqual(allocate_relay_port(self.connection), 30001)
        self.assertIsNone(
            self.connection.execute(
                "SELECT relay_port FROM leases WHERE id = 'expired'"
            ).fetchone()["relay_port"]
        )

    def test_port_availability_detects_another_active_lease(self) -> None:
        self.add_lease("owner", 30003)

        self.assertFalse(relay_port_is_available(self.connection, 30003, "other"))
        self.assertTrue(relay_port_is_available(self.connection, 30003, "owner"))

    def test_relay_health_reports_missing_process_and_ports(self) -> None:
        result = relay_health_payload(self.connection, {30003})

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["heartbeat_stale"])
        self.assertEqual(result["missing_ports"], [30003])

        self.connection.execute(
            """INSERT INTO relay_health VALUES
               (1, ?, 1, '[30003]', '[]', 1, NULL)""",
            (timestamp(0),),
        )
        result = relay_health_payload(self.connection, {30003})

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["heartbeat_stale"])
        self.assertEqual(result["missing_ports"], [])


class MultiRelayPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE relays (
                   id TEXT PRIMARY KEY,
                   name TEXT NOT NULL,
                   host TEXT NOT NULL,
                   port_start INTEGER NOT NULL,
                   port_end INTEGER NOT NULL,
                   method TEXT NOT NULL,
                   legacy_port INTEGER NOT NULL DEFAULT 0,
                   region TEXT NOT NULL DEFAULT '',
                   weight INTEGER NOT NULL DEFAULT 100,
                   enabled INTEGER NOT NULL DEFAULT 1,
                   draining INTEGER NOT NULL DEFAULT 0,
                   token_hash TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        self.connection.execute(
            """CREATE TABLE leases (
                   id TEXT PRIMARY KEY,
                   relay_id TEXT,
                   relay_port INTEGER,
                   expires_at TEXT NOT NULL,
                   revoked_at TEXT
               )"""
        )
        for relay_id, start, end in (("one", 30000, 30002), ("two", 30000, 30002)):
            self.connection.execute(
                """INSERT INTO relays(
                       id, name, host, port_start, port_end, method,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'chacha20-ietf-poly1305', ?, ?)""",
                (relay_id, relay_id, f"{relay_id}.example.com", start, end, timestamp(0), timestamp(0)),
            )

    def tearDown(self) -> None:
        self.connection.close()

    def test_same_port_can_be_used_by_different_relays(self) -> None:
        self.connection.execute(
            "INSERT INTO leases(id, relay_id, relay_port, expires_at) VALUES ('one-lease', 'one', 30000, ?)",
            (timestamp(1),),
        )

        self.assertFalse(relay_port_is_available(self.connection, 30000, "other", "one"))
        self.assertTrue(relay_port_is_available(self.connection, 30000, "other", "two"))
        self.assertEqual(allocate_relay_port(self.connection, "two"), 30000)

    def test_normalize_repairs_ports_per_relay(self) -> None:
        for lease_id, relay_id in (("one-kept", "one"), ("one-duplicate", "one"), ("two-same", "two")):
            self.connection.execute(
                "INSERT INTO leases(id, relay_id, relay_port, expires_at) VALUES (?, ?, 30001, ?)",
                (lease_id, relay_id, timestamp(1)),
            )

        normalize_relay_ports(self.connection)

        rows = {
            row["id"]: row["relay_port"]
            for row in self.connection.execute("SELECT id, relay_port FROM leases")
        }
        one_values = [value for key, value in rows.items() if key.startswith("one-")]
        self.assertEqual(len(one_values), 2)
        self.assertIn(30001, one_values)
        self.assertIn(None, one_values)
        self.assertEqual(rows["two-same"], 30001)

    def test_allocator_falls_back_when_first_relay_is_full(self) -> None:
        for index, port in enumerate((30000, 30001, 30002)):
            self.connection.execute(
                "INSERT INTO leases(id, relay_id, relay_port, expires_at) VALUES (?, 'one', ?, ?)",
                (f"full-{index}", port, timestamp(1)),
            )

        candidates = self.connection.execute("SELECT * FROM relays ORDER BY id").fetchall()
        relay, port = allocate_from_relay_candidates(self.connection, candidates)

        self.assertEqual(relay["id"], "two")
        self.assertEqual(port, 30000)


if __name__ == "__main__":
    unittest.main()
