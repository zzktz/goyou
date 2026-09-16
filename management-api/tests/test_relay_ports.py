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


if __name__ == "__main__":
    unittest.main()
