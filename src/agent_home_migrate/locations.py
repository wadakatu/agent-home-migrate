from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Mapping


def _expanded(value: str) -> Path:
    return Path(value).expanduser().absolute()


def nonstandard_location_warnings(
    homes: dict[str, Path], *, environment: Mapping[str, str] | None = None
) -> list[str]:
    """Detect supported agents writing valuable state outside their configured home.

    The MVP fails export on these warnings instead of silently producing an
    incomplete bundle.
    """

    env = os.environ if environment is None else environment
    warnings: list[str] = []

    codex_home = homes["codex"]
    configured_sqlite: list[tuple[Path, str]] = []
    configs = [codex_home / "config.toml", *sorted(codex_home.glob("*.config.toml"))]
    for config in configs:
        if not config.is_file():
            continue
        try:
            with config.open("rb") as handle:
                parsed = tomllib.load(handle)
            value = parsed.get("sqlite_home")
            if value is not None:
                if isinstance(value, str):
                    configured_sqlite.append((config, value))
                else:
                    warnings.append(
                        f"Codex sqlite_home in {config.name} is not a string; location is ambiguous"
                    )
        except (OSError, tomllib.TOMLDecodeError) as error:
            warnings.append(
                f"Codex {config.name} could not be parsed for sqlite_home: {error}"
            )
    if not configured_sqlite and env.get("CODEX_SQLITE_HOME"):
        configured_sqlite.append(
            (Path("<CODEX_SQLITE_HOME>"), env["CODEX_SQLITE_HOME"])
        )
    for source, configured in configured_sqlite:
        if not configured.startswith(("/", "~")):
            warnings.append(
                f"Codex uses a relative sqlite_home in {source.name}; ahm cannot "
                "determine the launch-directory-specific state location"
            )
        elif _expanded(configured).resolve(strict=False) != codex_home.resolve(
            strict=False
        ):
            warnings.append(
                f"Codex SQLite state selected by {source.name} is outside CODEX_HOME "
                f"and is not covered by this MVP: {_expanded(configured)}"
            )

    claude_home = homes["claude"]
    settings = claude_home / "settings.json"
    if settings.is_file():
        try:
            parsed = json.loads(settings.read_text(encoding="utf-8"))
            value = parsed.get("autoMemoryDirectory") if isinstance(parsed, dict) else None
            if value is not None:
                if isinstance(value, str):
                    warnings.append(
                        "Claude Code uses autoMemoryDirectory, which is not covered by this "
                        f"MVP: {_expanded(value)}"
                    )
                else:
                    warnings.append(
                        "Claude Code autoMemoryDirectory is not a string; location is ambiguous"
                    )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            warnings.append(
                f"Claude settings.json could not be parsed for autoMemoryDirectory: {error}"
            )
    return warnings
