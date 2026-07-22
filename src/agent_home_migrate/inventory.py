from __future__ import annotations

import os
import stat
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .models import Category, InventoryItem
from .profiles import classify
from .util import MigrationError, fast_tree_size


def scan_provider(
    provider: str,
    home: Path,
    *,
    prune_categories: frozenset[Category] = frozenset(
        {Category.EPHEMERAL, Category.SECRET}
    ),
) -> list[InventoryItem]:
    if not home.exists():
        return []
    if not home.is_dir() or home.is_symlink():
        raise MigrationError(f"{provider} home must be a real directory: {home}")

    items: list[InventoryItem] = []
    for root, dirs, files in os.walk(home, topdown=True, followlinks=False):
        root_path = Path(root)

        retained_dirs: list[str] = []
        for name in sorted(dirs):
            source = root_path / name
            relative = source.relative_to(home).as_posix()
            try:
                metadata = source.lstat()
            except OSError as error:
                raise MigrationError(f"cannot inspect {source}: {error}") from error
            classification = classify(provider, relative)

            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(source)
                items.append(
                    InventoryItem(
                        provider,
                        home,
                        relative,
                        source,
                        classification.category,
                        classification.reason,
                        "symlink",
                        len(target.encode()),
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_mtime_ns,
                        target,
                    )
                )
                continue

            if classification.category in prune_categories:
                items.append(
                    InventoryItem(
                        provider,
                        home,
                        relative,
                        source,
                        classification.category,
                        classification.reason,
                        "tree",
                        fast_tree_size(source),
                        stat.S_IMODE(metadata.st_mode),
                        metadata.st_mtime_ns,
                    )
                )
                continue
            retained_dirs.append(name)
        dirs[:] = retained_dirs

        for name in sorted(files):
            source = root_path / name
            relative = source.relative_to(home).as_posix()
            try:
                metadata = source.lstat()
            except OSError as error:
                raise MigrationError(f"cannot inspect {source}: {error}") from error
            classification = classify(provider, relative)
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISREG(metadata.st_mode):
                kind = "file"
                size = metadata.st_size
                target = None
            elif stat.S_ISLNK(metadata.st_mode):
                kind = "symlink"
                target = os.readlink(source)
                size = len(target.encode())
            else:
                kind = "unsupported"
                target = None
                size = metadata.st_size
                classification = type(classification)(
                    Category.UNKNOWN, "socket, device, or unsupported file type"
                )
            items.append(
                InventoryItem(
                    provider,
                    home,
                    relative,
                    source,
                    classification.category,
                    classification.reason,
                    kind,
                    size,
                    mode,
                    metadata.st_mtime_ns,
                    target,
                )
            )
    return sorted(items, key=lambda item: (item.provider, item.relative_path))


def scan_all(
    homes: dict[str, Path],
    *,
    prune_categories: frozenset[Category] = frozenset(
        {Category.EPHEMERAL, Category.SECRET}
    ),
) -> list[InventoryItem]:
    result: list[InventoryItem] = []
    for provider in ("codex", "claude"):
        result.extend(
            scan_provider(
                provider,
                homes[provider],
                prune_categories=prune_categories,
            )
        )
    return result


def summarize(items: Iterable[InventoryItem]) -> dict[str, dict[str, dict[str, int]]]:
    result: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"entries": 0, "bytes": 0})
    )
    for item in items:
        bucket = result[item.provider][item.category.value]
        bucket["entries"] += 1
        bucket["bytes"] += item.size
    return {
        provider: dict(sorted(categories.items()))
        for provider, categories in sorted(result.items())
    }

