from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class SecretCapabilityFinding:
    provider: str
    relative_path: str
    field_pattern: str
    entries: int

    def public_dict(self) -> dict[str, str | int]:
        return asdict(self)


@dataclass(frozen=True)
class SecretAuditError:
    provider: str
    relative_path: str
    reason: str

    def public_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SecretAudit:
    findings: tuple[SecretCapabilityFinding, ...]
    errors: tuple[SecretAuditError, ...]

    @property
    def requires_plaintext_approval(self) -> bool:
        return bool(self.findings or self.errors)

    def public_dict(self) -> dict[str, Any]:
        return {
            "findings": [finding.public_dict() for finding in self.findings],
            "errors": [error.public_dict() for error in self.errors],
            "requires_plaintext_approval": self.requires_plaintext_approval,
        }

    def warning_messages(self) -> list[str]:
        warnings = [
            f"{finding.provider}/{finding.relative_path} contains secret-capable "
            f"field {finding.field_pattern!r} ({finding.entries} entries); values "
            "were not displayed"
            for finding in self.findings
        ]
        warnings.extend(
            f"{error.provider}/{error.relative_path} could not be audited for "
            f"embedded secrets: {error.reason}"
            for error in self.errors
        )
        return warnings


_CODEX_NESTED_FIELDS = (
    ("mcp_servers", "env"),
    ("mcp_servers", "http_headers"),
    ("mcp_servers", "env_http_headers"),
    ("model_providers", "http_headers"),
    ("model_providers", "env_http_headers"),
    ("model_providers", "experimental_bearer_token"),
)


def _entry_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (Mapping, list, tuple, set)):
        return len(value)
    if isinstance(value, str):
        return 1 if value else 0
    return 1


def _nested_field_count(document: Mapping[str, Any], section: str, field: str) -> int:
    containers = document.get(section)
    if not isinstance(containers, Mapping):
        return 0
    total = 0
    for container in containers.values():
        if isinstance(container, Mapping) and field in container:
            total += _entry_count(container[field])
    return total


def _otel_header_count(document: Mapping[str, Any], section: str) -> int:
    otel = document.get("otel")
    if not isinstance(otel, Mapping):
        return 0
    exporters = otel.get(section)
    if not isinstance(exporters, Mapping):
        return 0
    total = 0
    for exporter in exporters.values():
        if isinstance(exporter, Mapping) and "headers" in exporter:
            total += _entry_count(exporter["headers"])
    return total


def _codex_findings(
    relative_path: str, document: Mapping[str, Any]
) -> list[SecretCapabilityFinding]:
    findings: list[SecretCapabilityFinding] = []
    for section, field in _CODEX_NESTED_FIELDS:
        count = _nested_field_count(document, section, field)
        if count:
            findings.append(
                SecretCapabilityFinding(
                    "codex",
                    relative_path,
                    f"{section}.<id>.{field}",
                    count,
                )
            )

    shell_policy = document.get("shell_environment_policy")
    if isinstance(shell_policy, Mapping) and "set" in shell_policy:
        count = _entry_count(shell_policy["set"])
        if count:
            findings.append(
                SecretCapabilityFinding(
                    "codex",
                    relative_path,
                    "shell_environment_policy.set",
                    count,
                )
            )

    for section in ("exporter", "trace_exporter"):
        count = _otel_header_count(document, section)
        if count:
            findings.append(
                SecretCapabilityFinding(
                    "codex",
                    relative_path,
                    f"otel.{section}.<id>.headers",
                    count,
                )
            )
    return findings


def _claude_findings(
    relative_path: str, document: Mapping[str, Any]
) -> list[SecretCapabilityFinding]:
    if "env" not in document:
        return []
    count = _entry_count(document["env"])
    if not count:
        return []
    return [SecretCapabilityFinding("claude", relative_path, "env", count)]


def _codex_config_paths(home: Path) -> list[Path]:
    candidates = [home / "config.toml", *sorted(home.glob("*.config.toml"))]
    return [path for path in candidates if path.is_symlink() or path.exists()]


def _claude_config_paths(home: Path) -> list[Path]:
    candidates = [home / "settings.json", home / "settings.local.json"]
    return [path for path in candidates if path.is_symlink() or path.exists()]


def audit_secret_capabilities(homes: dict[str, Path]) -> SecretAudit:
    """Inspect only documented secret-capable config fields without exposing values."""

    findings: list[SecretCapabilityFinding] = []
    errors: list[SecretAuditError] = []

    codex_home = homes["codex"]
    if codex_home.is_symlink() or (codex_home.exists() and not codex_home.is_dir()):
        errors.append(
            SecretAuditError("codex", ".", "home is not a safe real directory")
        )
        codex_paths: list[Path] = []
    else:
        codex_paths = _codex_config_paths(codex_home)
    for path in codex_paths:
        relative = path.relative_to(codex_home).as_posix()
        if path.is_symlink() or not path.is_file():
            errors.append(SecretAuditError("codex", relative, "not a regular file"))
            continue
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except tomllib.TOMLDecodeError:
            errors.append(SecretAuditError("codex", relative, "invalid TOML"))
            continue
        except OSError:
            errors.append(SecretAuditError("codex", relative, "file could not be read"))
            continue
        findings.extend(_codex_findings(relative, document))

    claude_home = homes["claude"]
    if claude_home.is_symlink() or (claude_home.exists() and not claude_home.is_dir()):
        errors.append(
            SecretAuditError("claude", ".", "home is not a safe real directory")
        )
        claude_paths: list[Path] = []
    else:
        claude_paths = _claude_config_paths(claude_home)
    for path in claude_paths:
        relative = path.relative_to(claude_home).as_posix()
        if path.is_symlink() or not path.is_file():
            errors.append(SecretAuditError("claude", relative, "not a regular file"))
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            errors.append(SecretAuditError("claude", relative, "not valid UTF-8"))
            continue
        except json.JSONDecodeError:
            errors.append(SecretAuditError("claude", relative, "invalid JSON"))
            continue
        except OSError:
            errors.append(SecretAuditError("claude", relative, "file could not be read"))
            continue
        if not isinstance(document, Mapping):
            errors.append(
                SecretAuditError("claude", relative, "JSON root is not an object")
            )
            continue
        findings.extend(_claude_findings(relative, document))

    return SecretAudit(tuple(findings), tuple(errors))
