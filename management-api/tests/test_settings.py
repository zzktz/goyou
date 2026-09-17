import os
import sqlite3
import sys
import unittest
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import (  # noqa: E402
    REGISTRATION_SETTING_KEY,
    registration_enabled,
    set_system_setting,
)


class SystemSettingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE system_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_setting_is_upserted_when_seed_row_is_missing(self) -> None:
        set_system_setting(self.connection, REGISTRATION_SETTING_KEY, "0")
        self.connection.commit()

        self.assertFalse(registration_enabled(self.connection))

        set_system_setting(self.connection, REGISTRATION_SETTING_KEY, "1")
        self.connection.commit()
        self.assertTrue(registration_enabled(self.connection))


if __name__ == "__main__":
    unittest.main()
