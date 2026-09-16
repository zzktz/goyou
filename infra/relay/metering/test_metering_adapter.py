import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MANAGEMENT_API_URL", "http://127.0.0.1")
os.environ.setdefault("METERING_TOKEN", "test-token")
os.environ.setdefault("LEGACY_RELAY_PASSWORD", "")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import metering_adapter  # noqa: E402


class RelayConfigTests(unittest.TestCase):
    def test_duplicate_ports_are_reported_and_not_listened_twice(self) -> None:
        leases = [
            {"lease_id": "first", "port": 30003, "password": "one"},
            {"lease_id": "second", "port": 30003, "password": "two"},
        ]

        ports, duplicates = metering_adapter.relay_port_snapshot(leases)
        config = metering_adapter.relay_config(leases)

        self.assertEqual(ports, [30003])
        self.assertEqual(duplicates, [30003])
        self.assertEqual(
            [inbound["listen_port"] for inbound in config["inbounds"]], [30003]
        )

    def test_distinct_ports_are_preserved(self) -> None:
        leases = [
            {"lease_id": "first", "port": 30003, "password": "one"},
            {"lease_id": "second", "port": 30004, "password": "two"},
        ]

        ports, duplicates = metering_adapter.relay_port_snapshot(leases)

        self.assertEqual(ports, [30003, 30004])
        self.assertEqual(duplicates, [])


if __name__ == "__main__":
    unittest.main()
