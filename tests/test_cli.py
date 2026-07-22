from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from agent_home_migrate.cli import main

from helpers import make_agent_homes


class CliTests(unittest.TestCase):
    def test_plan_json_and_export_verify_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            bundle = root / "bundle.ahm.zip"
            common = [
                "--codex-home",
                str(homes["codex"]),
                "--claude-home",
                str(homes["claude"]),
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([*common, "plan", "--json"]), 0)
            self.assertIn('"memory"', output.getvalue())

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main([*common, "export", "--output", str(bundle), "--json"]),
                    0,
                )
                self.assertEqual(main(["verify", str(bundle), "--json"]), 0)


if __name__ == "__main__":
    unittest.main()

