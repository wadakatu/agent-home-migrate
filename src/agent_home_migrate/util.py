from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath


BUFFER_SIZE = 1024 * 1024


class MigrationError(RuntimeError):
    """Expected, user-facing migration failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(BUFFER_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def human_size(size: int) -> str:
    if size < 0:
        return "unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or re.fullmatch(r"[A-Za-z]:", path.parts[0] if path.parts else "") is not None
    ):
        raise MigrationError(f"unsafe relative path in bundle: {value!r}")
    if any("\\" in part or "\x00" in part for part in path.parts):
        raise MigrationError(f"non-canonical relative path in bundle: {value!r}")
    return path


def command_version(executable: str) -> str | None:
    resolved = shutil.which(executable)
    if resolved is None:
        return None
    try:
        result = subprocess.run(
            [resolved, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "installed (version unavailable)"
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else "installed (version unavailable)"


def running_agent_processes() -> set[str]:
    """Best-effort process check. Failure yields an empty set, never a false block."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "comm="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    found: set[str] = set()
    for line in result.stdout.splitlines():
        base = Path(line.strip()).name.lower()
        if base in {"codex", "claude", "codex.app"} or base.startswith("codex"):
            found.add("codex" if base.startswith("codex") else "claude")
    return found


def fast_tree_size(path: Path) -> int:
    du = shutil.which("du")
    if du is not None:
        try:
            result = subprocess.run(
                [du, "-sk", str(path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            blocks = int(result.stdout.split()[0])
            return blocks * 1024
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        for name in files:
            try:
                total += (root_path / name).lstat().st_size
            except OSError:
                continue
        dirs[:] = [name for name in dirs if not (root_path / name).is_symlink()]
    return total


def is_default_home(provider: str, path: Path) -> bool:
    expected = Path.home() / (".codex" if provider == "codex" else ".claude")
    try:
        return path.resolve() == expected.resolve()
    except OSError:
        return path.absolute() == expected.absolute()


def ensure_no_symlink_ancestors(base: Path, target: Path) -> None:
    base_abs = base.absolute()
    target_abs = target.absolute()
    try:
        target_abs.relative_to(base_abs)
    except ValueError as error:
        raise MigrationError(f"restore target escapes provider home: {target}") from error

    current = base_abs
    if current.exists() and current.is_symlink():
        raise MigrationError(f"provider home is a symlink: {current}")
    for part in target_abs.relative_to(base_abs).parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise MigrationError(f"restore path crosses symlink ancestor: {current}")


def selected_tool_status() -> dict[str, str | None]:
    return {
        name: command_version(name)
        for name in ("codex", "claude", "age", "cct", "restic", "chezmoi", "brew")
    }
