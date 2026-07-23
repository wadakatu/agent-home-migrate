from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .bundle import create_bundle, verify_bundle
from .inventory import scan_all, summarize
from .locations import nonstandard_location_warnings
from .models import Category, DEFAULT_INCLUDED_CATEGORIES, ProcessState, RestoreAction
from .profiles import PROVIDERS
from .restore import restore_bundle, verify_restored_target
from .secret_audit import SecretAudit, audit_secret_capabilities
from .util import (
    MigrationError,
    command_version,
    human_size,
    is_default_home,
    running_agent_processes,
    selected_tool_status,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _homes(args: argparse.Namespace) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for provider in PROVIDERS:
        explicit = getattr(args, f"{provider.name}_home", None)
        configured = explicit or os.environ.get(provider.env_var)
        result[provider.name] = (
            _path(configured) if configured else Path.home() / provider.default_dirname
        ).absolute()
    return result


def _print_json(value: Any, *, file: TextIO | None = None) -> None:
    print(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        file=sys.stdout if file is None else file,
    )


def _print_runtime_error(code: str, message: str, *, json_mode: bool) -> None:
    if json_mode:
        _print_json(
            {
                "ok": False,
                "error": {
                    "code": code,
                    "message": message,
                },
            },
            file=sys.stderr,
        )
    else:
        print(f"error: {message}", file=sys.stderr)


def _home_safety_blockers(homes: dict[str, Path]) -> list[dict[str, str | None]]:
    blockers: list[dict[str, str | None]] = []
    for provider, home in homes.items():
        if not home.exists():
            blockers.append(
                {
                    "code": "home_missing",
                    "provider": provider,
                    "message": f"{provider} home does not exist: {home}",
                }
            )
        elif not home.is_dir() or home.is_symlink():
            blockers.append(
                {
                    "code": "home_unsafe",
                    "provider": provider,
                    "message": f"{provider} home is not a safe real directory: {home}",
                }
            )
    return blockers


def _export_preflight(
    homes: dict[str, Path],
    process_states: dict[str, ProcessState],
    tools: dict[str, str | None],
    secret_audit: SecretAudit,
    location_warnings: list[str],
) -> dict[str, Any]:
    blockers = _home_safety_blockers(homes)
    for provider, home in homes.items():
        state = process_states[provider]
        if not is_default_home(provider, home) or state == ProcessState.STOPPED:
            continue
        blockers.append(
            {
                "code": (
                    "process_running"
                    if state == ProcessState.RUNNING
                    else "process_unknown"
                ),
                "provider": provider,
                "message": (
                    f"{provider} is running"
                    if state == ProcessState.RUNNING
                    else f"{provider} process state could not be determined"
                ),
            }
        )
    for warning in location_warnings:
        blockers.append(
            {
                "code": "nonstandard_location",
                "provider": None,
                "message": warning,
            }
        )
    requires_encryption = secret_audit.requires_plaintext_approval
    if requires_encryption and tools.get("age") is None:
        blockers.append(
            {
                "code": "age_unavailable",
                "provider": None,
                "message": (
                    "secret-capable configuration requires encrypted export, but age "
                    "is not installed"
                ),
            }
        )
    return {
        "ready": not blockers,
        "requires_encryption": requires_encryption,
        "blockers": blockers,
    }


def _assert_safe_source_homes(homes: dict[str, Path]) -> None:
    blockers = _home_safety_blockers(homes)
    if blockers:
        raise MigrationError(
            "export source home safety check failed: "
            + "; ".join(str(blocker["message"]) for blocker in blockers)
        )


def _bundle_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    categories: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"entries": 0, "bytes": 0})
    )
    for entry in manifest["entries"]:
        bucket = categories[entry["provider"]][entry["category"]]
        bucket["entries"] += 1
        bucket["bytes"] += entry["size"]
    return {
        "schema": manifest["schema"],
        "bundle_id": manifest["bundle_id"],
        "created_at": manifest["created_at"],
        "source": manifest["source"],
        "selected_categories": manifest["selected_categories"],
        "entries": len(manifest["entries"]),
        "bytes": sum(entry["size"] for entry in manifest["entries"]),
        "categories": {
            provider: dict(sorted(values.items()))
            for provider, values in sorted(categories.items())
        },
    }


def _print_category_summary(summary: dict[str, dict[str, dict[str, int]]]) -> None:
    for provider, categories in summary.items():
        print(f"{provider}:")
        for category, values in categories.items():
            print(
                f"  {category:10} {values['entries']:7} entries  "
                f"{human_size(values['bytes']):>12}"
            )


def _print_secret_audit(audit: SecretAudit) -> None:
    warnings = audit.warning_messages()
    if not warnings:
        return
    print("Sensitive config audit:")
    for warning in warnings:
        print(f"  - {warning}")


def command_doctor(args: argparse.Namespace) -> int:
    homes = _homes(args)
    secret_audit = audit_secret_capabilities(homes)
    process_states = running_agent_processes()
    tools = selected_tool_status()
    providers: dict[str, Any] = {}
    for spec in PROVIDERS:
        home = homes[spec.name]
        disk_base = home if home.exists() else home.parent
        try:
            free = shutil.disk_usage(disk_base).free
        except OSError:
            free = None
        process_state = process_states[spec.name]
        providers[spec.name] = {
            "home": str(home),
            "exists": home.exists(),
            "is_directory": home.is_dir(),
            "is_symlink": home.is_symlink(),
            "version": tools[spec.executable],
            "process_state": process_state.value,
            "running": (
                None
                if process_state == ProcessState.UNKNOWN
                else process_state == ProcessState.RUNNING
            ),
            "free_bytes": free,
        }
    location_warnings = nonstandard_location_warnings(homes)
    export_preflight = _export_preflight(
        homes, process_states, tools, secret_audit, location_warnings
    )
    warnings: list[str] = []
    for name, data in providers.items():
        if not data["exists"]:
            warnings.append(f"{name} home does not exist")
        elif not data["is_directory"] or data["is_symlink"]:
            warnings.append(f"{name} home is not a safe real directory")
        if data["running"]:
            warnings.append(f"{name} appears to be running; quit it before export/restore")
        elif data["process_state"] == ProcessState.UNKNOWN.value:
            warnings.append(
                f"{name} process state is unknown because process detection failed; "
                "default-home export/restore requires --allow-live until detection works"
            )
    if tools["age"] is None:
        warnings.append("age is not installed; encrypted bundle operations are unavailable")
    if tools["cct"] is None:
        warnings.append("cct is not installed; path-changing session handoff is unavailable")
    warnings.extend(location_warnings)
    warnings.extend(secret_audit.warning_messages())

    report = {
        "ahm_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "providers": providers,
        "tools": tools,
        "secret_config_audit": secret_audit.public_dict(),
        "export_preflight": export_preflight,
        "warnings": warnings,
    }
    if args.json:
        _print_json(report)
    else:
        print(f"agent-home-migrate {__version__}")
        print(f"Python: {report['python']}")
        for name, data in providers.items():
            print(
                f"{name}: home={data['home']} exists={data['exists']} "
                f"process_state={data['process_state']} "
                f"version={data['version'] or 'not installed'}"
            )
        print("Optional tools:")
        for name in ("age", "cct", "restic", "chezmoi", "brew"):
            print(f"  {name}: {tools[name] or 'not installed'}")
        print(
            "Safe export preflight: "
            + ("ready" if export_preflight["ready"] else "blocked")
        )
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"  - {warning}")
    return 0 if not any("not a safe" in warning for warning in warnings) else 2


def command_plan(args: argparse.Namespace) -> int:
    homes = _homes(args)
    secret_audit = audit_secret_capabilities(homes)
    prune = frozenset() if args.full else frozenset({Category.EPHEMERAL, Category.SECRET})
    items = scan_all(homes, prune_categories=prune)
    summary = summarize(items)
    unknown = [item.public_dict() for item in items if item.category == Category.UNKNOWN]
    report = {
        "homes": {name: str(path) for name, path in homes.items()},
        "summary": summary,
        "default_included_categories": sorted(
            category.value for category in DEFAULT_INCLUDED_CATEGORIES
        ),
        "unknown_entries": unknown,
        "pruned_excluded_trees": not args.full,
        "location_warnings": nonstandard_location_warnings(homes),
        "secret_config_audit": secret_audit.public_dict(),
    }
    if args.json:
        _print_json(report)
    else:
        _print_category_summary(summary)
        if unknown:
            print(f"Unknown entries ({len(unknown)}, excluded by default):")
            for entry in unknown[:20]:
                print(
                    f"  {entry['provider']}/{entry['relative_path']} "
                    f"({human_size(entry['size'])})"
                )
            if len(unknown) > 20:
                print(f"  ... and {len(unknown) - 20} more; use --json for all paths")
        _print_secret_audit(secret_audit)
    return 0


def _selected_categories(args: argparse.Namespace) -> frozenset[Category]:
    selected = set(DEFAULT_INCLUDED_CATEGORIES)
    if args.no_state:
        selected.discard(Category.STATE)
    if args.include_secrets:
        selected.add(Category.SECRET)
    if args.include_unknown:
        selected.add(Category.UNKNOWN)
    if args.include_ephemeral:
        selected.add(Category.EPHEMERAL)
    return frozenset(selected)


def _assert_agents_stopped(homes: dict[str, Path], allow_live: bool) -> None:
    if allow_live:
        return
    process_states = running_agent_processes()
    blocked = [
        f"{provider}={process_states[provider].value}"
        for provider, home in homes.items()
        if is_default_home(provider, home)
        and process_states[provider] != ProcessState.STOPPED
    ]
    if blocked:
        raise MigrationError(
            "agent process safety check failed for default homes: "
            + ", ".join(blocked)
            + "; quit the agents and retry where process detection works, or use "
            "--allow-live at your own risk"
        )


def command_export(args: argparse.Namespace) -> int:
    homes = _homes(args)
    _assert_safe_source_homes(homes)
    _assert_agents_stopped(homes, args.allow_live)
    secret_audit = audit_secret_capabilities(homes)
    if args.include_secrets and args.age_recipient is None and not args.allow_plaintext_secrets:
        raise MigrationError(
            "--include-secrets requires --age-recipient; use "
            "--allow-plaintext-secrets only for an already encrypted local volume"
        )
    if (
        secret_audit.requires_plaintext_approval
        and args.age_recipient is None
        and not args.allow_plaintext_secrets
    ):
        raise MigrationError(
            "plaintext export blocked by sensitive config audit "
            f"({len(secret_audit.findings)} secret-capable fields, "
            f"{len(secret_audit.errors)} audit errors); run 'ahm plan' for field "
            "patterns, then use --age-recipient, or --allow-plaintext-secrets only "
            "for an already encrypted local volume"
        )
    location_warnings = nonstandard_location_warnings(homes)
    if location_warnings:
        raise MigrationError(
            "non-standard data location could make the bundle incomplete: "
            + "; ".join(location_warnings)
        )
    selected = _selected_categories(args)
    prune = frozenset(
        category
        for category in (Category.EPHEMERAL, Category.SECRET)
        if category not in selected
    )
    items = scan_all(homes, prune_categories=prune)
    versions = {
        "codex": command_version("codex"),
        "claude": command_version("claude"),
    }
    manifest = create_bundle(
        args.output,
        items,
        selected_categories=selected,
        provider_versions=versions,
        force=args.force,
        age_recipient=args.age_recipient,
    )
    summary = _bundle_summary(manifest)
    if args.json:
        _print_json(
            {
                "output": str(args.output),
                **summary,
                "secret_config_audit": secret_audit.public_dict(),
            }
        )
    else:
        print(f"Created: {args.output}")
        print(f"Bundle id: {summary['bundle_id']}")
        print(f"Entries: {summary['entries']}")
        print(f"Uncompressed payload: {human_size(summary['bytes'])}")
        _print_category_summary(summary["categories"])
        _print_secret_audit(secret_audit)
    return 0


def _decryption_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "age_identity": getattr(args, "age_identity", None),
        "age_passphrase": getattr(args, "age_passphrase", False),
    }


def command_inspect(args: argparse.Namespace) -> int:
    manifest = verify_bundle(args.bundle, **_decryption_kwargs(args))
    summary = _bundle_summary(manifest)
    if args.json:
        _print_json(summary)
    else:
        print(f"Schema: {summary['schema']}")
        print(f"Bundle id: {summary['bundle_id']}")
        print(f"Created: {summary['created_at']}")
        print(f"Entries: {summary['entries']}")
        print(f"Payload: {human_size(summary['bytes'])}")
        _print_category_summary(summary["categories"])
    return 0


def _summarize_actions(actions: list[RestoreAction]) -> dict[str, int]:
    return dict(sorted(Counter(action.status for action in actions).items()))


def _print_actions(actions: list[RestoreAction], *, limit: int = 50) -> None:
    for action in actions[:limit]:
        print(
            f"{action.status:10} {action.provider}/{action.relative_path} - {action.reason}"
        )
    if len(actions) > limit:
        print(f"... and {len(actions) - limit} more")
    print("Summary: " + ", ".join(
        f"{status}={count}" for status, count in _summarize_actions(actions).items()
    ))


def command_restore(args: argparse.Namespace) -> int:
    target_root = args.target_root.expanduser().absolute()
    if args.apply:
        target_homes = {
            "codex": target_root / ".codex",
            "claude": target_root / ".claude",
        }
        _assert_agents_stopped(target_homes, args.allow_live)
    manifest, actions, backup_root = restore_bundle(
        args.bundle,
        target_root,
        apply=args.apply,
        on_conflict=args.on_conflict,
        **_decryption_kwargs(args),
    )
    report = {
        "bundle_id": manifest["bundle_id"],
        "dry_run": not args.apply,
        "target_root": str(target_root),
        "summary": _summarize_actions(actions),
        "actions": [action.public_dict() for action in actions],
        "backup_root": str(backup_root) if backup_root else None,
    }
    if args.json:
        _print_json(report)
    else:
        print("Dry run; no files were written." if not args.apply else "Restore applied.")
        _print_actions(actions)
        if backup_root:
            print(f"Conflict backups: {backup_root}")
    return 3 if any(action.status == "conflict" for action in actions) else 0


def command_verify(args: argparse.Namespace) -> int:
    manifest = verify_bundle(args.bundle, **_decryption_kwargs(args))
    if args.target_root is None:
        summary = _bundle_summary(manifest)
        if args.json:
            _print_json({"verified": True, **summary})
        else:
            print(
                f"Bundle verified: {summary['entries']} entries, "
                f"{human_size(summary['bytes'])}"
            )
        return 0

    actions = verify_restored_target(manifest, args.target_root)
    failed = [action for action in actions if action.status != "verified"]
    report = {
        "bundle_id": manifest["bundle_id"],
        "target_root": str(args.target_root.expanduser().absolute()),
        "verified": not failed,
        "summary": _summarize_actions(actions),
        "actions": [action.public_dict() for action in actions],
    }
    if args.json:
        _print_json(report)
    else:
        _print_actions(actions)
    return 0 if not failed else 3


def _add_decryption_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--age-identity", type=_path, help="age identity file")
    parser.add_argument(
        "--age-passphrase",
        action="store_true",
        help="let age prompt for the bundle passphrase",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ahm",
        description="Safely migrate local Codex and Claude Code state.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--codex-home", type=_path, help="override CODEX_HOME")
    parser.add_argument("--claude-home", type=_path, help="override CLAUDE_HOME")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check homes, versions, and tools")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    plan = subparsers.add_parser("plan", help="classify and size migration data")
    plan.add_argument("--full", action="store_true", help="do not prune excluded trees")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(handler=command_plan)

    export = subparsers.add_parser("export", help="create a verified migration bundle")
    export.add_argument("--output", type=_path, required=True)
    export.add_argument("--no-state", action="store_true", help="exclude mutable state DBs")
    export.add_argument("--include-secrets", action="store_true")
    export.add_argument(
        "--allow-plaintext-secrets",
        action="store_true",
        help=(
            "allow plaintext output containing credential files, secret-capable "
            "config, or config that could not be audited"
        ),
    )
    export.add_argument("--include-unknown", action="store_true")
    export.add_argument("--include-ephemeral", action="store_true")
    export.add_argument("--age-recipient", help="encrypt output to this age recipient")
    export.add_argument(
        "--allow-live",
        action="store_true",
        help="continue when an agent is running or process detection is unavailable",
    )
    export.add_argument("--force", action="store_true")
    export.add_argument("--json", action="store_true")
    export.set_defaults(handler=command_export)

    inspect = subparsers.add_parser("inspect", help="verify and summarize a bundle")
    inspect.add_argument("bundle", type=_path)
    inspect.add_argument("--json", action="store_true")
    _add_decryption_flags(inspect)
    inspect.set_defaults(handler=command_inspect)

    restore = subparsers.add_parser("restore", help="plan or apply a bundle restore")
    restore.add_argument("bundle", type=_path)
    restore.add_argument("--target-root", type=_path, required=True)
    restore.add_argument(
        "--on-conflict",
        choices=("fail", "skip", "replace-with-backup"),
        default="fail",
    )
    restore.add_argument("--apply", action="store_true")
    restore.add_argument(
        "--allow-live",
        action="store_true",
        help="continue when an agent is running or process detection is unavailable",
    )
    restore.add_argument("--json", action="store_true")
    _add_decryption_flags(restore)
    restore.set_defaults(handler=command_restore)

    verify = subparsers.add_parser("verify", help="verify a bundle or restored target")
    verify.add_argument("bundle", type=_path)
    verify.add_argument("--target-root", type=_path)
    verify.add_argument("--json", action="store_true")
    _add_decryption_flags(verify)
    verify.set_defaults(handler=command_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_mode = bool(getattr(args, "json", False))
    try:
        return int(args.handler(args))
    except MigrationError as error:
        _print_runtime_error("migration_error", str(error), json_mode=json_mode)
        return 2
    except KeyboardInterrupt:
        _print_runtime_error("interrupted", "interrupted", json_mode=json_mode)
        return 130
