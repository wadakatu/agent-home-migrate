from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_home_migrate.locations import nonstandard_location_warnings

from helpers import make_agent_homes, write


class LocationTests(unittest.TestCase):
    def test_default_locations_have_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            self.assertEqual(
                nonstandard_location_warnings(homes, environment={}), []
            )

    def test_external_claude_memory_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root)
            write(
                homes["claude"] / "settings.json",
                '{"autoMemoryDirectory":"~/private-claude-memory"}\n',
            )
            warnings = nonstandard_location_warnings(homes, environment={})
            self.assertEqual(len(warnings), 1)
            self.assertIn("autoMemoryDirectory", warnings[0])

    def test_external_codex_sqlite_home_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            warnings = nonstandard_location_warnings(
                homes, environment={"CODEX_SQLITE_HOME": "/external/codex-state"}
            )
            self.assertEqual(len(warnings), 1)
            self.assertIn("outside CODEX_HOME", warnings[0])


if __name__ == "__main__":
    unittest.main()

