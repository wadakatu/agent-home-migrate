from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_home_migrate.bundle import create_bundle, verify_bundle
from agent_home_migrate.inventory import scan_all
from agent_home_migrate.models import DEFAULT_INCLUDED_CATEGORIES
from agent_home_migrate.restore import restore_bundle, verify_restored_target
from helpers import make_agent_homes


class AgeIntegrationTests(unittest.TestCase):
    def test_real_age_encrypted_round_trip(self) -> None:
        age = shutil.which("age")
        age_keygen = shutil.which("age-keygen")
        missing = [
            name
            for name, executable in (("age", age), ("age-keygen", age_keygen))
            if executable is None
        ]
        if missing:
            message = f"required age tools are unavailable: {', '.join(missing)}"
            if os.environ.get("AHM_REQUIRE_AGE") == "1":
                self.fail(message)
            self.skipTest(message)

        assert age is not None
        assert age_keygen is not None
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            identity = root / "identity.txt"
            subprocess.run(
                [age_keygen, "-o", str(identity)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            recipient = subprocess.run(
                [age_keygen, "-y", str(identity)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertTrue(recipient.startswith("age1"))

            homes = make_agent_homes(root / "source")
            bundle = root / "agent-state.ahm.zip.age"
            exported_manifest = create_bundle(
                bundle,
                scan_all(homes),
                selected_categories=DEFAULT_INCLUDED_CATEGORIES,
                provider_versions={"codex": "integration", "claude": "integration"},
                age_recipient=recipient,
            )

            self.assertTrue(bundle.is_file())
            self.assertNotEqual(bundle.read_bytes()[:4], b"PK\x03\x04")
            verified_manifest = verify_bundle(bundle, age_identity=identity)
            self.assertEqual(
                verified_manifest["bundle_id"], exported_manifest["bundle_id"]
            )

            staging = root / "staging"
            staging.mkdir()
            restored_manifest, actions, backup = restore_bundle(
                bundle,
                staging,
                apply=True,
                age_identity=identity,
            )
            self.assertIsNone(backup)
            self.assertTrue(actions)
            self.assertTrue(all(action.status == "create" for action in actions))

            results = verify_restored_target(restored_manifest, staging)
            self.assertTrue(results)
            self.assertTrue(all(result.status == "verified" for result in results))
            self.assertEqual(
                (staging / ".codex/memories/MEMORY.md").read_text(encoding="utf-8"),
                "Prefer small changes.\n",
            )
            self.assertTrue(
                (staging / ".claude/projects/-old-project/session-1.jsonl").is_file()
            )


if __name__ == "__main__":
    unittest.main()
