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
    @staticmethod
    def _tool_status(*, age: str | None = None) -> dict[str, str | None]:
        return {
            "codex": None,
            "claude": None,
            "age": age,
            "cct": None,
            "restic": None,
            "chezmoi": None,
            "brew": None,
        }

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

    def test_doctor_reports_structured_export_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            write(
                homes["codex"] / "config.toml",
                '[mcp_servers.demo.env]\nTOKEN = "must-not-be-printed"\n',
            )
            output = io.StringIO()
            stopped = {
                "codex": ProcessState.STOPPED,
                "claude": ProcessState.STOPPED,
            }

            with mock.patch(
                "agent_home_migrate.cli.running_agent_processes",
                return_value=stopped,
            ), mock.patch(
                "agent_home_migrate.cli.selected_tool_status",
                return_value=self._tool_status(),
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--codex-home",
                            str(homes["codex"]),
                            "--claude-home",
                            str(homes["claude"]),
                            "doctor",
                            "--json",
                        ]
                    ),
                    0,
                )

            report = json.loads(output.getvalue())
            preflight = report["export_preflight"]
            self.assertFalse(preflight["ready"])
            self.assertTrue(preflight["requires_encryption"])
            self.assertEqual(
                [blocker["code"] for blocker in preflight["blockers"]],
                ["age_unavailable"],
            )
            self.assertNotIn("TOKEN", output.getvalue())
            self.assertNotIn("must-not-be-printed", output.getvalue())

    def test_doctor_preflight_is_ready_for_safe_custom_homes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            output = io.StringIO()
            stopped = {
                "codex": ProcessState.STOPPED,
                "claude": ProcessState.STOPPED,
            }

            with mock.patch(
                "agent_home_migrate.cli.running_agent_processes",
                return_value=stopped,
            ), mock.patch(
                "agent_home_migrate.cli.selected_tool_status",
                return_value=self._tool_status(),
            ), contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "--codex-home",
                            str(homes["codex"]),
                            "--claude-home",
                            str(homes["claude"]),
                            "doctor",
                            "--json",
                        ]
                    ),
                    0,
                )

            preflight = json.loads(output.getvalue())["export_preflight"]
            self.assertTrue(preflight["ready"])
            self.assertFalse(preflight["requires_encryption"])
            self.assertEqual(preflight["blockers"], [])

    def test_export_refuses_missing_or_symlink_source_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            missing_claude = root / "missing-claude"
            symlink_claude = root / "claude-link"
            symlink_claude.symlink_to(homes["claude"], target_is_directory=True)

            for label, claude_home, expected in (
                ("missing", missing_claude, "claude home does not exist"),
                ("symlink", symlink_claude, "claude home is not a safe real directory"),
            ):
                with self.subTest(label=label):
                    bundle = root / f"{label}.zip"
                    error = io.StringIO()
                    with contextlib.redirect_stderr(error):
                        exit_code = main(
                            [
                                "--codex-home",
                                str(homes["codex"]),
                                "--claude-home",
                                str(claude_home),
                                "export",
                                "--output",
                                str(bundle),
                                "--allow-live",
                            ]
                        )
                    self.assertEqual(exit_code, 2)
                    self.assertFalse(bundle.exists())
                    self.assertIn(expected, error.getvalue())

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

    def test_json_mode_formats_runtime_error_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            write(
                homes["claude"] / "settings.json",
                '{"env":{"ANTHROPIC_API_KEY":"must-not-be-printed"}}',
            )
            output = io.StringIO()
            error = io.StringIO()

            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                exit_code = main(
                    [
                        "--codex-home",
                        str(homes["codex"]),
                        "--claude-home",
                        str(homes["claude"]),
                        "export",
                        "--output",
                        str(root / "blocked.zip"),
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(output.getvalue(), "")
            report = json.loads(error.getvalue())
            self.assertFalse(report["ok"])
            self.assertEqual(report["error"]["code"], "migration_error")
            self.assertIn("plaintext export blocked", report["error"]["message"])
            self.assertNotIn("ANTHROPIC_API_KEY", error.getvalue())
            self.assertNotIn("must-not-be-printed", error.getvalue())

    def test_json_mode_formats_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            error = io.StringIO()

            with mock.patch(
                "agent_home_migrate.cli.running_agent_processes",
                side_effect=KeyboardInterrupt,
            ), contextlib.redirect_stderr(error):
                exit_code = main(
                    [
                        "--codex-home",
                        str(homes["codex"]),
                        "--claude-home",
                        str(homes["claude"]),
                        "doctor",
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 130)
            report = json.loads(error.getvalue())
            self.assertFalse(report["ok"])
            self.assertEqual(report["error"]["code"], "interrupted")
            self.assertEqual(report["error"]["message"], "interrupted")

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
            ), mock.patch(
                "agent_home_migrate.cli.is_default_home", return_value=True
            ), contextlib.redirect_stdout(output):
                self.assertEqual(main([*common, "doctor", "--json"]), 0)

            report = json.loads(output.getvalue())
            self.assertEqual(
                report["providers"]["codex"]["process_state"], "unknown"
            )
            self.assertIsNone(report["providers"]["codex"]["running"])
            self.assertEqual(
                [
                    blocker["code"]
                    for blocker in report["export_preflight"]["blockers"]
                    if blocker["provider"] == "codex"
                ],
                ["process_unknown"],
            )
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
