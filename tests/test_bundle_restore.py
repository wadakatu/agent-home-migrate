from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from agent_home_migrate.bundle import MANIFEST_NAME, create_bundle, verify_bundle
from agent_home_migrate.inventory import scan_all
from agent_home_migrate.models import DEFAULT_INCLUDED_CATEGORIES
from agent_home_migrate.restore import restore_bundle, verify_restored_target
import agent_home_migrate.restore as restore_module
from agent_home_migrate.util import MigrationError

from helpers import make_agent_homes


class BundleRestoreTests(unittest.TestCase):
    def _create(self, root: Path) -> tuple[Path, dict]:
        homes = make_agent_homes(root / "source")
        items = scan_all(homes)
        bundle = root / "state.ahm.zip"
        manifest = create_bundle(
            bundle,
            items,
            selected_categories=DEFAULT_INCLUDED_CATEGORIES,
            provider_versions={"codex": "codex-cli 0.test", "claude": "2.test"},
        )
        return bundle, manifest

    def test_export_excludes_secrets_ephemeral_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            bundle, manifest = self._create(Path(temp_name))
            verified = verify_bundle(bundle)
            self.assertEqual(verified["bundle_id"], manifest["bundle_id"])
            paths = {
                (entry["provider"], entry["relative_path"]): entry
                for entry in manifest["entries"]
            }
            self.assertNotIn(("codex", "auth.json"), paths)
            self.assertNotIn(("codex", "logs_2.sqlite"), paths)
            self.assertNotIn(("codex", "mystery.bin"), paths)
            self.assertNotIn(("claude", ".credentials.json"), paths)
            self.assertNotIn(("claude", "session-env/session-1/env"), paths)
            self.assertEqual(
                paths[("codex", "state_5.sqlite")]["snapshot_method"],
                "sqlite-online-backup",
            )
            self.assertEqual(
                paths[("codex", "memories_1.sqlite")]["snapshot_method"],
                "sqlite-online-backup",
            )

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
    def test_plaintext_bundle_is_owner_only_even_when_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            bundle, _ = self._create(root)
            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)

            bundle.chmod(0o644)
            homes = make_agent_homes(root / "replacement-source")
            create_bundle(
                bundle,
                scan_all(homes),
                selected_categories=DEFAULT_INCLUDED_CATEGORIES,
                provider_versions={"codex": "test", "claude": "test"},
                force=True,
            )
            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
    def test_encrypted_bundle_is_owner_only_even_when_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            bundle = root / "state.ahm.zip.age"
            bundle.write_bytes(b"old bundle")
            bundle.chmod(0o644)

            def fake_age(command: list[str], **_kwargs) -> None:
                encrypted = Path(command[command.index("--output") + 1])
                encrypted.write_bytes(b"encrypted bundle")
                encrypted.chmod(0o644)

            with mock.patch(
                "agent_home_migrate.bundle.shutil.which", return_value="/usr/bin/age"
            ), mock.patch(
                "agent_home_migrate.bundle.subprocess.run", side_effect=fake_age
            ):
                create_bundle(
                    bundle,
                    scan_all(homes),
                    selected_categories=DEFAULT_INCLUDED_CATEGORIES,
                    provider_versions={"codex": "test", "claude": "test"},
                    force=True,
                    age_recipient="age1testrecipient",
                )

            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o600)

    def test_restore_is_dry_run_by_default_then_applies_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            bundle, _ = self._create(root)
            target = root / "target"
            target.mkdir()

            manifest, actions, backup = restore_bundle(bundle, target)
            self.assertIsNone(backup)
            self.assertTrue(all(action.status == "create" for action in actions))
            self.assertFalse((target / ".codex/config.toml").exists())

            manifest, actions, backup = restore_bundle(bundle, target, apply=True)
            self.assertIsNone(backup)
            self.assertTrue((target / ".codex/config.toml").exists())
            self.assertTrue(
                (target / ".claude/projects/-old-project/memory/MEMORY.md").exists()
            )
            checks = verify_restored_target(manifest, target)
            self.assertTrue(all(action.status == "verified" for action in checks))
            with contextlib.closing(
                sqlite3.connect(target / ".codex/state_5.sqlite")
            ) as database:
                self.assertEqual(
                    database.execute("SELECT value FROM records").fetchone()[0], "state"
                )

    def test_conflict_requires_policy_and_replacement_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            bundle, _ = self._create(root)
            target = root / "target"
            config = target / ".codex/config.toml"
            config.parent.mkdir(parents=True)
            config.write_text("local change\n", encoding="utf-8")

            _, actions, _ = restore_bundle(bundle, target)
            self.assertIn("conflict", {action.status for action in actions})
            with self.assertRaises(MigrationError):
                restore_bundle(bundle, target, apply=True)

            manifest, actions, backup_root = restore_bundle(
                bundle,
                target,
                apply=True,
                on_conflict="replace-with-backup",
            )
            self.assertIsNotNone(backup_root)
            assert backup_root is not None
            self.assertEqual(
                (backup_root / "codex/config.toml").read_text(encoding="utf-8"),
                "local change\n",
            )
            self.assertIn("memories = true", config.read_text(encoding="utf-8"))
            self.assertTrue(
                all(
                    action.status == "verified"
                    for action in verify_restored_target(manifest, target)
                )
            )

    def test_unexpected_zip_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            bundle, _ = self._create(root)
            with zipfile.ZipFile(bundle, "a") as archive:
                archive.writestr("payload/codex/unexpected", b"bad")
            with self.assertRaises(MigrationError):
                verify_bundle(bundle)

    def test_age_extension_cannot_silently_contain_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            with self.assertRaises(MigrationError):
                create_bundle(
                    root / "plaintext.age",
                    scan_all(homes),
                    selected_categories=DEFAULT_INCLUDED_CATEGORIES,
                    provider_versions={"codex": "test", "claude": "test"},
                )

    def test_path_traversal_in_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            bundle, _ = self._create(root)
            damaged = root / "damaged.ahm.zip"
            with zipfile.ZipFile(bundle, "r") as source, zipfile.ZipFile(
                damaged, "w"
            ) as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == MANIFEST_NAME:
                        manifest = json.loads(data)
                        manifest["entries"][0]["relative_path"] = "../escape"
                        data = json.dumps(manifest).encode()
                    target.writestr(info, data)
            with self.assertRaises(MigrationError):
                verify_bundle(damaged)

    def test_absolute_symlink_is_never_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "source")
            (homes["codex"] / "skills/external").parent.mkdir(parents=True)
            (homes["codex"] / "skills/external").symlink_to("/tmp/outside")
            bundle = root / "symlink.ahm.zip"
            create_bundle(
                bundle,
                scan_all(homes),
                selected_categories=DEFAULT_INCLUDED_CATEGORIES,
                provider_versions={"codex": "test", "claude": "test"},
            )
            target = root / "target"
            target.mkdir()
            with self.assertRaises(MigrationError):
                restore_bundle(bundle, target)

    def test_partial_restore_is_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            bundle, _ = self._create(root)
            target = root / "target"
            target.mkdir()
            original = restore_module._atomic_write_payload
            calls = 0

            def fail_on_second(archive, entry, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic write failure")
                return original(archive, entry, destination)

            with mock.patch.object(
                restore_module, "_atomic_write_payload", side_effect=fail_on_second
            ):
                with self.assertRaises(MigrationError):
                    restore_bundle(bundle, target, apply=True)
            restored_files = [path for path in target.rglob("*") if path.is_file()]
            self.assertEqual(restored_files, [])


if __name__ == "__main__":
    unittest.main()
