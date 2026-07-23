from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_home_migrate.cli import main
from agent_home_migrate.models import ProcessState

from helpers import make_agent_homes, write


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

    def test_doctor_and_plan_warn_without_disclosing_config_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            write(
                homes["claude"] / "settings.json",
                '{"env":{"ANTHROPIC_API_KEY":"must-not-be-printed"}}',
            )
            common = [
                "--codex-home",
                str(homes["codex"]),
                "--claude-home",
                str(homes["claude"]),
            ]
            plan_output = io.StringIO()
            with contextlib.redirect_stdout(plan_output):
                self.assertEqual(main([*common, "plan", "--json"]), 0)
            doctor_output = io.StringIO()
            with contextlib.redirect_stdout(doctor_output):
                self.assertEqual(main([*common, "doctor", "--json"]), 0)

            for output in (plan_output, doctor_output):
                report = json.loads(output.getvalue())
                audit = report["secret_config_audit"]
                self.assertTrue(audit["requires_plaintext_approval"])
                self.assertEqual(audit["findings"][0]["field_pattern"], "env")
                self.assertNotIn("ANTHROPIC_API_KEY", output.getvalue())
                self.assertNotIn("must-not-be-printed", output.getvalue())

    def test_plaintext_export_requires_explicit_secret_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            write(
                homes["codex"] / "config.toml",
                '[mcp_servers.demo.env]\nTOKEN = "must-not-be-printed"\n',
            )
            bundle = root / "bundle.ahm.zip"
            common = [
                "--codex-home",
                str(homes["codex"]),
                "--claude-home",
                str(homes["claude"]),
                "export",
                "--output",
                str(bundle),
            ]
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                self.assertEqual(main(common), 2)

            self.assertFalse(bundle.exists())
            self.assertIn("plaintext export blocked", error.getvalue())
            self.assertNotIn("must-not-be-printed", error.getvalue())

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*common, "--allow-plaintext-secrets"]), 0)
            self.assertTrue(bundle.exists())

    def test_doctor_reports_unknown_process_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            common = [
                "--codex-home",
                str(homes["codex"]),
                "--claude-home",
                str(homes["claude"]),
            ]
            output = io.StringIO()

            with mock.patch(
                "agent_home_migrate.cli.running_agent_processes",
                return_value={
                    "codex": ProcessState.UNKNOWN,
                    "claude": ProcessState.UNKNOWN,
                },
            ), contextlib.redirect_stdout(output):
                self.assertEqual(main([*common, "doctor", "--json"]), 0)

            report = json.loads(output.getvalue())
            self.assertEqual(
                report["providers"]["codex"]["process_state"], "unknown"
            )
            self.assertIsNone(report["providers"]["codex"]["running"])
            self.assertTrue(
                any(
                    "process state is unknown" in warning
                    for warning in report["warnings"]
                )
            )

    def test_default_home_export_and_restore_fail_closed_on_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            bundle = root / "bundle.ahm.zip"
            target = root / "target"
            common = [
                "--codex-home",
                str(homes["codex"]),
                "--claude-home",
                str(homes["claude"]),
            ]
            unknown = {
                "codex": ProcessState.UNKNOWN,
                "claude": ProcessState.UNKNOWN,
            }

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            *common,
                            "export",
                            "--output",
                            str(bundle),
                            "--allow-live",
                        ]
                    ),
                    0,
                )

            error = io.StringIO()
            with mock.patch(
                "agent_home_migrate.cli.running_agent_processes", return_value=unknown
            ), mock.patch(
                "agent_home_migrate.cli.is_default_home", return_value=True
            ), contextlib.redirect_stderr(error):
                self.assertEqual(
                    main([*common, "export", "--output", str(root / "blocked.zip")]),
                    2,
                )
                self.assertEqual(
                    main(
                        [
                            "restore",
                            str(bundle),
                            "--target-root",
                            str(target),
                            "--apply",
                        ]
                    ),
                    2,
                )

            self.assertIn("codex=unknown", error.getvalue())
            self.assertFalse((root / "blocked.zip").exists())
            self.assertFalse((target / ".codex/config.toml").exists())

    def test_allow_live_bypasses_unknown_process_detection(self) -> None:
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

            with mock.patch(
                "agent_home_migrate.cli.running_agent_processes",
                side_effect=AssertionError("detection should be bypassed"),
            ), mock.patch(
                "agent_home_migrate.cli.is_default_home", return_value=True
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            *common,
                            "export",
                            "--output",
                            str(bundle),
                            "--allow-live",
                        ]
                    ),
                    0,
                )

            self.assertTrue(bundle.exists())


if __name__ == "__main__":
    unittest.main()
