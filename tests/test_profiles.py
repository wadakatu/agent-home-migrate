from __future__ import annotations

import unittest

from agent_home_migrate.models import Category
from agent_home_migrate.profiles import classify


class ProfileTests(unittest.TestCase):
    def test_codex_paths_are_classified(self) -> None:
        cases = {
            "auth.json": Category.SECRET,
            "worktrees/abcd/file": Category.EPHEMERAL,
            ".sandbox_migration": Category.EPHEMERAL,
            "logs_2.sqlite": Category.EPHEMERAL,
            "memories/MEMORY.md": Category.MEMORY,
            "memories_1.sqlite": Category.MEMORY,
            "sessions/2026/rollout.jsonl": Category.SESSION,
            "state_5.sqlite": Category.STATE,
            "skills/demo/SKILL.md": Category.CONFIG,
            "something-new": Category.UNKNOWN,
        }
        for path, category in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify("codex", path).category, category)

    def test_claude_memory_wins_over_project_session(self) -> None:
        self.assertEqual(
            classify("claude", "projects/-repo/memory/MEMORY.md").category,
            Category.MEMORY,
        )
        self.assertEqual(
            classify("claude", "projects/-repo/session.jsonl").category,
            Category.SESSION,
        )
        self.assertEqual(
            classify("claude", ".credentials.json").category,
            Category.SECRET,
        )
        self.assertEqual(
            classify("claude", "paste-cache/fragment.txt").category,
            Category.EPHEMERAL,
        )


if __name__ == "__main__":
    unittest.main()
