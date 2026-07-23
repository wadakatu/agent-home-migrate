from __future__ import annotations

import contextlib
import io
import tomllib
import unittest
from pathlib import Path

from agent_home_migrate import __version__
from agent_home_migrate.cli import main


class VersionTests(unittest.TestCase):
    def test_pyproject_uses_runtime_version_as_its_source(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        configuration = tomllib.loads(
            (project_root / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertNotIn("version", configuration["project"])
        self.assertIn("version", configuration["project"]["dynamic"])
        self.assertEqual(
            configuration["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "agent_home_migrate.__version__",
        )

    def test_cli_reports_runtime_version(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                main(["--version"])

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue(), f"{__version__}\n")


if __name__ == "__main__":
    unittest.main()
