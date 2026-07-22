from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path


def write(path: Path, data: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def sqlite_database(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path)) as database:
        database.execute("CREATE TABLE records(value TEXT NOT NULL)")
        database.execute("INSERT INTO records(value) VALUES (?)", (value,))
        database.commit()
    return path


def make_agent_homes(root: Path) -> dict[str, Path]:
    codex = root / ".codex"
    claude = root / ".claude"

    write(codex / "config.toml", "[features]\nmemories = true\n")
    write(codex / "AGENTS.md", "Use tests.\n")
    write(codex / "auth.json", '{"token":"do-not-copy"}\n')
    write(
        codex / "sessions/2026/07/22/rollout-test.jsonl",
        '{"type":"session_meta","payload":{"id":"s1","cwd":"/old/project"}}\n',
    )
    write(codex / "memories/MEMORY.md", "Prefer small changes.\n")
    sqlite_database(codex / "memories_1.sqlite", "memory")
    sqlite_database(codex / "state_5.sqlite", "state")
    sqlite_database(codex / "logs_2.sqlite", "diagnostic")
    write(codex / "worktrees/deadbeef/large.tmp", "junk" * 100)
    write(codex / "cache/catalog.json", "{}\n")
    write(codex / "mystery.bin", "unknown")

    write(claude / "settings.json", '{"autoMemoryEnabled":true}\n')
    write(claude / "CLAUDE.md", "Use tests.\n")
    write(claude / ".credentials.json", '{"oauth":"do-not-copy"}\n')
    write(
        claude / "projects/-old-project/session-1.jsonl",
        '{"type":"user","cwd":"/old/project","message":"hello"}\n',
    )
    write(
        claude / "projects/-old-project/memory/MEMORY.md",
        "The test command is unit-test.\n",
    )
    write(claude / "file-history/session-1/file.txt", "before\n")
    write(claude / "session-env/session-1/env", "SECRET=not-a-credential-test\n")
    write(claude / "cache/index", "junk")
    write(claude / "unclassified.dat", "unknown")

    return {"codex": codex, "claude": claude}
