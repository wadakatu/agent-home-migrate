from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_home_migrate.inventory import scan_all, summarize
from agent_home_migrate.models import Category

from helpers import make_agent_homes


class InventoryTests(unittest.TestCase):
    def test_inventory_classifies_and_prunes_excluded_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            items = scan_all(homes)
            by_key = {(item.provider, item.relative_path): item for item in items}

            self.assertEqual(
                by_key[("codex", "worktrees")].category, Category.EPHEMERAL
            )
            self.assertEqual(by_key[("codex", "worktrees")].kind, "tree")
            self.assertNotIn(("codex", "worktrees/deadbeef/large.tmp"), by_key)
            self.assertEqual(
                by_key[("claude", "projects/-old-project/memory/MEMORY.md")].category,
                Category.MEMORY,
            )
            self.assertEqual(
                by_key[("claude", ".credentials.json")].category, Category.SECRET
            )
            summary = summarize(items)
            self.assertGreater(summary["codex"]["ephemeral"]["bytes"], 0)
            self.assertGreater(summary["claude"]["session"]["entries"], 0)

    def test_full_inventory_descends_into_ephemeral_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            items = scan_all(homes, prune_categories=frozenset())
            paths = {(item.provider, item.relative_path) for item in items}
            self.assertIn(("codex", "worktrees/deadbeef/large.tmp"), paths)
            self.assertIn(("claude", "session-env/session-1/env"), paths)


if __name__ == "__main__":
    unittest.main()

