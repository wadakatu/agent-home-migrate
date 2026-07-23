from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from agent_home_migrate.models import ProcessState
from agent_home_migrate.util import running_agent_processes


class ProcessDetectionTests(unittest.TestCase):
    def test_permission_error_returns_unknown_for_all_agents(self) -> None:
        with mock.patch(
            "agent_home_migrate.util.subprocess.run",
            side_effect=PermissionError("operation not permitted"),
        ):
            states = running_agent_processes()

        self.assertEqual(
            states,
            {
                "codex": ProcessState.UNKNOWN,
                "claude": ProcessState.UNKNOWN,
            },
        )

    def test_success_distinguishes_running_and_stopped(self) -> None:
        completed = subprocess.CompletedProcess(
            ["ps", "-axo", "comm="],
            0,
            stdout="/Applications/Codex.app/Contents/MacOS/codex\n/usr/bin/zsh\n",
            stderr="",
        )
        with mock.patch(
            "agent_home_migrate.util.subprocess.run", return_value=completed
        ):
            states = running_agent_processes()

        self.assertEqual(states["codex"], ProcessState.RUNNING)
        self.assertEqual(states["claude"], ProcessState.STOPPED)


if __name__ == "__main__":
    unittest.main()
