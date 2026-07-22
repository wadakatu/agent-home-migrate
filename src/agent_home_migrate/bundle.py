from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from . import __version__
from .models import Category, InventoryItem
from .util import (
    BUFFER_SIZE,
    MigrationError,
    safe_relative_path,
    sha256_bytes,
)


SCHEMA = "agent-home-migrate/v1"
MANIFEST_NAME = "manifest.json"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _zip_info(name: str, mode: int, mtime_ns: int) -> zipfile.ZipInfo:
    timestamp = datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=UTC)
    # ZIP cannot represent dates before 1980 and stores local-like date tuples.
    year = min(max(timestamp.year, 1980), 2107)
    info = zipfile.ZipInfo(
        name,
        date_time=(year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, timestamp.second),
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((stat.S_IFREG | mode) & 0xFFFF) << 16
    info.create_system = 3
    return info


def _copy_with_hash(source: BinaryIO, target: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: source.read(BUFFER_SIZE), b""):
        digest.update(chunk)
        target.write(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _looks_like_sqlite(path: Path) -> bool:
    if path.name.endswith(("-wal", "-shm")):
        return False
    if path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    source_uri = source.resolve().as_uri() + "?mode=ro"
    try:
        with contextlib.closing(
            sqlite3.connect(source_uri, uri=True, timeout=30)
        ) as source_db:
            with contextlib.closing(
                sqlite3.connect(destination, timeout=30)
            ) as destination_db:
                source_db.backup(destination_db)
                check = destination_db.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise MigrationError(
                        f"SQLite integrity_check failed for {source}: {check!r}"
                    )
    except sqlite3.Error as error:
        raise MigrationError(f"SQLite Online Backup failed for {source}: {error}") from error


def _write_item(
    archive: zipfile.ZipFile,
    item: InventoryItem,
    temp_dir: Path,
) -> dict[str, Any]:
    relative = safe_relative_path(item.relative_path).as_posix()
    payload_path = f"payload/{item.provider}/{relative}"
    metadata = item.public_dict()
    metadata["payload_path"] = payload_path if item.kind == "file" else None

    if item.kind == "symlink":
        assert item.link_target is not None
        metadata["sha256"] = sha256_bytes(b"symlink\x00" + item.link_target.encode())
        metadata["snapshot_method"] = "symlink-metadata"
        return metadata
    if item.kind != "file":
        raise MigrationError(
            f"cannot export unsupported inventory item: {item.provider}/{relative}"
        )

    source = item.source_path
    before = source.lstat()
    snapshot_method = "byte-copy"
    read_path = source
    if _looks_like_sqlite(source):
        read_path = temp_dir / f"sqlite-{uuid.uuid4().hex}.db"
        _snapshot_sqlite(source, read_path)
        snapshot_method = "sqlite-online-backup"

    try:
        info = _zip_info(payload_path, item.mode, item.mtime_ns)
        with read_path.open("rb") as source_handle:
            with archive.open(info, "w", force_zip64=True) as target_handle:
                digest, size = _copy_with_hash(source_handle, target_handle)
    finally:
        if read_path != source:
            read_path.unlink(missing_ok=True)

    if snapshot_method == "byte-copy":
        after = source.lstat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ino != after.st_ino
        ):
            raise MigrationError(
                f"source changed while exporting: {item.provider}/{relative}; "
                "quit the agent and retry"
            )

    metadata["sha256"] = digest
    metadata["size"] = size
    metadata["snapshot_method"] = snapshot_method
    return metadata


def create_bundle(
    output: Path,
    items: list[InventoryItem],
    *,
    selected_categories: frozenset[Category],
    provider_versions: dict[str, str | None],
    force: bool = False,
    age_recipient: str | None = None,
) -> dict[str, Any]:
    if age_recipient is not None and output.suffix != ".age":
        raise MigrationError("encrypted output filename must end in .age")
    if age_recipient is None and output.suffix == ".age":
        raise MigrationError(".age output requires --age-recipient")
    if output.exists() and not force:
        raise MigrationError(f"output already exists (use --force to replace): {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    selected = [
        item
        for item in items
        if item.category in selected_categories and item.kind in {"file", "symlink"}
    ]
    selected.sort(key=lambda item: (item.provider, item.relative_path))
    if not selected:
        raise MigrationError("no files matched the selected categories")

    with tempfile.TemporaryDirectory(prefix="ahm-export-", dir=output.parent) as temp_name:
        temp_dir = Path(temp_name)
        plain_bundle = temp_dir / "bundle.ahm.zip"
        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "bundle_id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC).isoformat(),
            "created_by": {"name": "agent-home-migrate", "version": __version__},
            "source": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "providers": provider_versions,
            },
            "selected_categories": sorted(category.value for category in selected_categories),
            "entries": [],
        }
        try:
            with zipfile.ZipFile(
                plain_bundle,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                manifest["entries"] = [
                    _write_item(archive, item, temp_dir) for item in selected
                ]
                manifest_bytes = _json_bytes(manifest)
                info = _zip_info(MANIFEST_NAME, 0o600, 315532800_000_000_000)
                archive.writestr(info, manifest_bytes)
        except Exception:
            plain_bundle.unlink(missing_ok=True)
            raise

        if age_recipient is None:
            os.replace(plain_bundle, output)
        else:
            age = shutil.which("age")
            if age is None:
                raise MigrationError("age is not installed; cannot encrypt bundle")
            encrypted = temp_dir / "bundle.age"
            try:
                subprocess.run(
                    [
                        age,
                        "--encrypt",
                        "--recipient",
                        age_recipient,
                        "--output",
                        str(encrypted),
                        str(plain_bundle),
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError as error:
                raise MigrationError(f"age encryption failed with exit code {error.returncode}") from error
            os.replace(encrypted, output)
    return manifest


@contextlib.contextmanager
def readable_bundle_path(
    bundle: Path,
    *,
    age_identity: Path | None = None,
    age_passphrase: bool = False,
) -> Iterator[Path]:
    if not bundle.exists() or not bundle.is_file():
        raise MigrationError(f"bundle does not exist: {bundle}")
    if bundle.suffix != ".age":
        yield bundle
        return

    age = shutil.which("age")
    if age is None:
        raise MigrationError("age is not installed; cannot decrypt bundle")
    if age_identity is None and not age_passphrase:
        raise MigrationError(
            "encrypted bundle requires --age-identity or --age-passphrase"
        )
    if age_identity is not None and age_passphrase:
        raise MigrationError("choose either --age-identity or --age-passphrase")

    with tempfile.TemporaryDirectory(prefix="ahm-decrypt-") as temp_name:
        decrypted = Path(temp_name) / "bundle.ahm.zip"
        command = [age, "--decrypt", "--output", str(decrypted)]
        if age_identity is not None:
            command.extend(["--identity", str(age_identity)])
        command.append(str(bundle))
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as error:
            raise MigrationError(f"age decryption failed with exit code {error.returncode}") from error
        yield decrypted


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError as error:
        raise MigrationError("bundle is missing manifest.json") from error
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"invalid manifest.json: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise MigrationError(f"unsupported bundle schema: {manifest.get('schema')!r}")
    if not isinstance(manifest.get("entries"), list):
        raise MigrationError("manifest entries must be a list")
    try:
        uuid.UUID(manifest["bundle_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise MigrationError("manifest bundle_id is not a UUID") from error
    return manifest


def verify_open_archive(archive: zipfile.ZipFile) -> dict[str, Any]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise MigrationError("bundle contains duplicate ZIP entries")
    for name in names:
        safe_relative_path(name)

    manifest = _read_manifest(archive)
    expected_payloads: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for raw_entry in manifest["entries"]:
        if not isinstance(raw_entry, dict):
            raise MigrationError("manifest entry must be an object")
        provider = raw_entry.get("provider")
        relative = raw_entry.get("relative_path")
        kind = raw_entry.get("kind")
        digest = raw_entry.get("sha256")
        if provider not in {"codex", "claude"}:
            raise MigrationError(f"invalid provider in manifest: {provider!r}")
        if not isinstance(relative, str):
            raise MigrationError("manifest entry has no relative_path")
        safe_relative_path(relative)
        key = (provider, relative)
        if key in seen_keys:
            raise MigrationError(f"duplicate manifest path: {provider}/{relative}")
        seen_keys.add(key)
        try:
            Category(raw_entry.get("category"))
        except (TypeError, ValueError) as error:
            raise MigrationError(
                f"invalid category for {provider}/{relative}: {raw_entry.get('category')!r}"
            ) from error
        if not isinstance(digest, str) or len(digest) != 64:
            raise MigrationError(f"invalid SHA-256 for {provider}/{relative}")
        mode = raw_entry.get("mode")
        mtime_ns = raw_entry.get("mtime_ns")
        size = raw_entry.get("size")
        if not isinstance(mode, int) or mode < 0 or mode > 0o7777:
            raise MigrationError(f"invalid mode for {provider}/{relative}")
        if not isinstance(mtime_ns, int) or mtime_ns < 0:
            raise MigrationError(f"invalid mtime for {provider}/{relative}")
        if not isinstance(size, int) or size < 0:
            raise MigrationError(f"invalid size for {provider}/{relative}")

        if kind == "symlink":
            link_target = raw_entry.get("link_target")
            if not isinstance(link_target, str):
                raise MigrationError(f"symlink has no target: {provider}/{relative}")
            actual = sha256_bytes(b"symlink\x00" + link_target.encode())
            if actual != digest:
                raise MigrationError(f"symlink checksum mismatch: {provider}/{relative}")
        elif kind == "file":
            payload = raw_entry.get("payload_path")
            expected = f"payload/{provider}/{relative}"
            if payload != expected:
                raise MigrationError(f"invalid payload path for {provider}/{relative}")
            expected_payloads.add(expected)
            try:
                info = archive.getinfo(expected)
            except KeyError as error:
                raise MigrationError(f"bundle payload is missing: {expected}") from error
            expected_size = size
            if info.file_size != expected_size:
                raise MigrationError(f"size mismatch: {provider}/{relative}")
            calculated = hashlib.sha256()
            with archive.open(info, "r") as handle:
                for chunk in iter(lambda: handle.read(BUFFER_SIZE), b""):
                    calculated.update(chunk)
            if calculated.hexdigest() != digest:
                raise MigrationError(f"checksum mismatch: {provider}/{relative}")
        else:
            raise MigrationError(f"unsupported entry kind: {kind!r}")

    actual_payloads = {name for name in names if name != MANIFEST_NAME}
    if actual_payloads != expected_payloads:
        extras = sorted(actual_payloads - expected_payloads)
        missing = sorted(expected_payloads - actual_payloads)
        raise MigrationError(f"unexpected bundle entries; extras={extras}, missing={missing}")
    return manifest


def verify_bundle(
    bundle: Path,
    *,
    age_identity: Path | None = None,
    age_passphrase: bool = False,
) -> dict[str, Any]:
    with readable_bundle_path(
        bundle, age_identity=age_identity, age_passphrase=age_passphrase
    ) as readable:
        try:
            with zipfile.ZipFile(readable, "r") as archive:
                return verify_open_archive(archive)
        except zipfile.BadZipFile as error:
            raise MigrationError(f"invalid ZIP bundle: {error}") from error


@contextlib.contextmanager
def open_verified_bundle(
    bundle: Path,
    *,
    age_identity: Path | None = None,
    age_passphrase: bool = False,
) -> Iterator[tuple[zipfile.ZipFile, dict[str, Any]]]:
    with readable_bundle_path(
        bundle, age_identity=age_identity, age_passphrase=age_passphrase
    ) as readable:
        try:
            with zipfile.ZipFile(readable, "r") as archive:
                manifest = verify_open_archive(archive)
                yield archive, manifest
        except zipfile.BadZipFile as error:
            raise MigrationError(f"invalid ZIP bundle: {error}") from error
