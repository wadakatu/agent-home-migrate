from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Category(StrEnum):
    CONFIG = "config"
    MEMORY = "memory"
    SESSION = "session"
    STATE = "state"
    SECRET = "secret"
    EPHEMERAL = "ephemeral"
    UNKNOWN = "unknown"


class ProcessState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


DEFAULT_INCLUDED_CATEGORIES = frozenset(
    {Category.CONFIG, Category.MEMORY, Category.SESSION, Category.STATE}
)


@dataclass(frozen=True)
class Classification:
    category: Category
    reason: str


@dataclass(frozen=True)
class InventoryItem:
    provider: str
    home: Path
    relative_path: str
    source_path: Path
    category: Category
    reason: str
    kind: str
    size: int
    mode: int
    mtime_ns: int
    link_target: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Return metadata without leaking the absolute source path."""
        result = asdict(self)
        result.pop("home")
        result.pop("source_path")
        result["category"] = self.category.value
        return result


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    default_dirname: str
    env_var: str
    executable: str


@dataclass(frozen=True)
class RestoreAction:
    provider: str
    relative_path: str
    target: Path
    status: str
    reason: str

    def public_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "relative_path": self.relative_path,
            "target": str(self.target),
            "status": self.status,
            "reason": self.reason,
        }
