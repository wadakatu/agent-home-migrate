from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .bundle import open_verified_bundle
from .models import RestoreAction
from .util import (
    BUFFER_SIZE,
    MigrationError,
    ensure_no_symlink_ancestors,
    safe_relative_path,
    sha256_file,
)


CONFLICT_MODES = {"fail", "skip", "replace-with-backup"}


def destination_homes(target_root: Path) -> dict[str, Path]:
    return {
        "codex": target_root / ".codex",
        "claude": target_root / ".claude",
    }


def _target_for(entry: dict[str, Any], homes: dict[str, Path]) -> Path:
    provider = entry["provider"]
    relative = safe_relative_path(entry["relative_path"])
    return homes[provider].joinpath(*relative.parts)


def _safe_symlink_target(provider_home: Path, target: Path, link_target: str) -> None:
    pure = PurePosixPath(link_target)
    if pure.is_absolute() or "\x00" in link_target or "\\" in link_target:
        raise MigrationError(f"unsafe absolute or non-canonical symlink: {target}")
    candidate = (target.parent / Path(*pure.parts)).resolve(strict=False)
    base = provider_home.resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as error:
        raise MigrationError(f"symlink would escape provider home: {target} -> {link_target}") from error


def _entry_matches_target(entry: dict[str, Any], target: Path) -> bool:
    kind = entry["kind"]
    if kind == "symlink":
        return target.is_symlink() and os.readlink(target) == entry["link_target"]
    if target.is_symlink() or not target.is_file():
        return False
    try:
        if target.stat().st_size != entry["size"]:
            return False
        return sha256_file(target) == entry["sha256"]
    except OSError:
        return False


def plan_restore(
    manifest: dict[str, Any],
    target_root: Path,
    *,
    on_conflict: str = "fail",
) -> list[RestoreAction]:
    if on_conflict not in CONFLICT_MODES:
        raise MigrationError(f"invalid conflict mode: {on_conflict}")
    homes = destination_homes(target_root)
    actions: list[RestoreAction] = []
    for entry in manifest["entries"]:
        target = _target_for(entry, homes)
        ensure_no_symlink_ancestors(homes[entry["provider"]], target)
        if entry["kind"] == "symlink":
            _safe_symlink_target(
                homes[entry["provider"]], target, entry["link_target"]
            )
        exists = target.exists() or target.is_symlink()
        if not exists:
            status = "create"
            reason = "target does not exist"
        elif _entry_matches_target(entry, target):
            status = "identical"
            reason = "target already has the same content"
        elif target.is_dir() and not target.is_symlink():
            status = "conflict"
            reason = "a directory exists where the bundle has a file"
        elif on_conflict == "skip":
            status = "skip"
            reason = "different target content; skipped by policy"
        elif on_conflict == "replace-with-backup":
            status = "replace"
            reason = "different target content; backup required"
        else:
            status = "conflict"
            reason = "different target content"
        actions.append(
            RestoreAction(
                entry["provider"],
                entry["relative_path"],
                target,
                status,
                reason,
            )
        )
    return actions


def _atomic_write_payload(archive, entry: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        if entry["kind"] == "symlink":
            temporary = target.parent / f".ahm-link-{uuid.uuid4().hex}"
            os.symlink(entry["link_target"], temporary)
            os.replace(temporary, target)
            temporary = None
        else:
            handle = tempfile.NamedTemporaryFile(
                prefix=".ahm-file-", dir=target.parent, delete=False
            )
            temporary = Path(handle.name)
            digest = hashlib.sha256()
            try:
                with handle:
                    with archive.open(entry["payload_path"], "r") as source:
                        for chunk in iter(lambda: source.read(BUFFER_SIZE), b""):
                            digest.update(chunk)
                            handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if digest.hexdigest() != entry["sha256"]:
                    raise MigrationError(
                        f"payload changed after verification: {entry['provider']}/{entry['relative_path']}"
                    )
                os.chmod(temporary, entry["mode"])
                os.utime(temporary, ns=(entry["mtime_ns"], entry["mtime_ns"]))
                os.replace(temporary, target)
                temporary = None
            finally:
                if not handle.closed:
                    handle.close()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _backup_existing(target: Path, backup: Path) -> None:
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        os.symlink(os.readlink(target), backup)
    elif target.is_file():
        shutil.copy2(target, backup, follow_symlinks=False)
    else:
        raise MigrationError(f"cannot back up non-file conflict: {target}")


def _restore_backup(backup: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup.is_symlink():
        temporary = target.parent / f".ahm-rollback-{uuid.uuid4().hex}"
        os.symlink(os.readlink(backup), temporary)
        os.replace(temporary, target)
        return
    handle = tempfile.NamedTemporaryFile(prefix=".ahm-rollback-", dir=target.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    try:
        shutil.copy2(backup, temporary, follow_symlinks=False)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _rollback(records: list[tuple[Path, Path | None]]) -> None:
    failures: list[str] = []
    for target, backup in reversed(records):
        try:
            if backup is None:
                if target.is_file() or target.is_symlink():
                    target.unlink()
            else:
                _restore_backup(backup, target)
        except OSError as error:
            failures.append(f"{target}: {error}")
    if failures:
        raise MigrationError("restore failed and rollback was incomplete: " + "; ".join(failures))


def restore_bundle(
    bundle: Path,
    target_root: Path,
    *,
    apply: bool = False,
    on_conflict: str = "fail",
    age_identity: Path | None = None,
    age_passphrase: bool = False,
) -> tuple[dict[str, Any], list[RestoreAction], Path | None]:
    target_root = target_root.expanduser().absolute()
    if apply and (not target_root.exists() or not target_root.is_dir()):
        raise MigrationError(f"--target-root must be an existing directory: {target_root}")
    if target_root.exists() and target_root.is_symlink():
        raise MigrationError(f"--target-root must not be a symlink: {target_root}")

    with open_verified_bundle(
        bundle, age_identity=age_identity, age_passphrase=age_passphrase
    ) as (archive, manifest):
        actions = plan_restore(manifest, target_root, on_conflict=on_conflict)
        conflicts = [action for action in actions if action.status == "conflict"]
        if not apply:
            return manifest, actions, None
        if conflicts:
            raise MigrationError(
                f"restore has {len(conflicts)} conflict(s); choose --on-conflict skip "
                "or replace-with-backup"
            )

        try:
            bundle_id = str(uuid.UUID(manifest["bundle_id"]))
        except (KeyError, ValueError, TypeError) as error:
            raise MigrationError("manifest bundle_id is not a UUID") from error
        backup_root = target_root / ".agent-home-migrate-backups" / bundle_id
        replacements = [action for action in actions if action.status == "replace"]
        if replacements and (backup_root.exists() or backup_root.is_symlink()):
            raise MigrationError(f"transaction backup already exists: {backup_root}")

        entries = {
            (entry["provider"], entry["relative_path"]): entry
            for entry in manifest["entries"]
        }
        records: list[tuple[Path, Path | None]] = []
        try:
            for action in actions:
                if action.status not in {"create", "replace"}:
                    continue
                entry = entries[(action.provider, action.relative_path)]
                provider_home = destination_homes(target_root)[action.provider]
                ensure_no_symlink_ancestors(provider_home, action.target)
                backup: Path | None = None
                if action.status == "replace":
                    backup = (
                        backup_root
                        / action.provider
                        / Path(*safe_relative_path(action.relative_path).parts)
                    )
                    ensure_no_symlink_ancestors(target_root, backup)
                    _backup_existing(action.target, backup)
                records.append((action.target, backup))
                _atomic_write_payload(archive, entry, action.target)
        except Exception as error:
            try:
                _rollback(records)
            except MigrationError as rollback_error:
                raise rollback_error from error
            raise MigrationError(f"restore failed; changes were rolled back: {error}") from error
        return manifest, actions, backup_root if replacements else None


def verify_restored_target(
    manifest: dict[str, Any], target_root: Path
) -> list[RestoreAction]:
    homes = destination_homes(target_root.expanduser().absolute())
    results: list[RestoreAction] = []
    for entry in manifest["entries"]:
        target = _target_for(entry, homes)
        ensure_no_symlink_ancestors(homes[entry["provider"]], target)
        if _entry_matches_target(entry, target):
            status = "verified"
            reason = "content checksum matches"
        elif target.exists() or target.is_symlink():
            status = "mismatch"
            reason = "target exists but content differs"
        else:
            status = "missing"
            reason = "target does not exist"
        results.append(
            RestoreAction(
                entry["provider"],
                entry["relative_path"],
                target,
                status,
                reason,
            )
        )
    return results
