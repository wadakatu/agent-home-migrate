from __future__ import annotations

from pathlib import PurePosixPath

from .models import Category, Classification, ProviderSpec


CODEX = ProviderSpec("codex", ".codex", "CODEX_HOME", "codex")
CLAUDE = ProviderSpec("claude", ".claude", "CLAUDE_HOME", "claude")
PROVIDERS = (CODEX, CLAUDE)


def _under(path: str, *prefixes: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _has_segment(path: str, segment: str) -> bool:
    return segment in PurePosixPath(path).parts


def _result(category: Category, reason: str) -> Classification:
    return Classification(category, reason)


def classify(provider: str, relative_path: str) -> Classification:
    path = relative_path.strip("/")
    if provider == "codex":
        return classify_codex(path)
    if provider == "claude":
        return classify_claude(path)
    return _result(Category.UNKNOWN, "unsupported provider")


def classify_codex(path: str) -> Classification:
    name = PurePosixPath(path).name
    lower = name.lower()

    if path == "auth.json" or _under(path, "mcp-oauth-locks"):
        return _result(Category.SECRET, "credential or OAuth state")

    if _has_segment(path, ".git"):
        return _result(Category.EPHEMERAL, "nested VCS metadata is not needed for restore")
    if lower in {
        ".ds_store",
        ".app-server-state-reconciled-v1",
        ".personality_migration",
        ".sandbox_migration",
    } or lower.endswith(("-wal", "-shm", ".swp")):
        return _result(Category.EPHEMERAL, "temporary or SQLite sidecar file")
    if ".tmp-" in name or name.endswith(".tmp") or ".bak.tmp-" in name:
        return _result(Category.EPHEMERAL, "temporary state file")
    if ".bak" in name or name.endswith("~"):
        return _result(Category.EPHEMERAL, "local backup copy")
    if _under(
        path,
        "worktrees",
        "cache",
        "log",
        "logs",
        "shell_snapshots",
        "tmp",
        ".tmp",
        "ipc",
        "process_manager",
        "node_repl",
        "packages",
        "ambient-suggestions",
    ):
        return _result(Category.EPHEMERAL, "regenerable runtime data")
    if _under(
        path,
        "plugins/cache",
        "plugins/.remote-plugin-install-staging",
        "plugins/.plugin-appserver",
    ):
        return _result(Category.EPHEMERAL, "plugin cache or staging data")
    if _under(path, "computer-use") and path != "computer-use/config.toml":
        return _result(Category.EPHEMERAL, "installed helper application")
    if lower.startswith("logs_") and ".sqlite" in lower:
        return _result(Category.EPHEMERAL, "diagnostic SQLite store")
    if name in {"models_cache.json", "version.json", "installation_id"}:
        return _result(Category.EPHEMERAL, "regenerable installation metadata")

    if _under(path, "memories", "memories_extensions") or (
        lower.startswith("memories_") and ".sqlite" in lower
    ):
        return _result(Category.MEMORY, "Codex local memory")

    if _under(
        path,
        "sessions",
        "archived_sessions",
        "generated_images",
        "visualizations",
    ) or path in {"history.jsonl", "session_index.jsonl"}:
        return _result(Category.SESSION, "Codex conversation history or attachment")

    if _under(path, "sqlite") or (
        (lower.startswith("state_") or lower.startswith("goals_"))
        and ".sqlite" in lower
    ) or path in {".codex-global-state.json", "internal_storage.json"}:
        return _result(Category.STATE, "Codex mutable local state")

    if (
        path in {
            "config.toml",
            "AGENTS.md",
            "AGENTS.override.md",
            "hooks.json",
            "chrome-native-hosts.json",
            "chrome-native-hosts-v2.json",
            "computer-use/config.toml",
        }
        or name.endswith(".config.toml")
        or _under(
            path,
            "hooks",
            "rules",
            "skills",
            "plugins",
            "themes",
            "pets",
            "vendor_imports",
        )
        or name.endswith((".sh", ".zsh", ".bash"))
    ):
        return _result(Category.CONFIG, "Codex durable configuration or extension")

    return _result(Category.UNKNOWN, "no Codex profile rule matched")


def classify_claude(path: str) -> Classification:
    name = PurePosixPath(path).name
    lower = name.lower()

    if path in {".credentials.json", "credentials.json"} or lower.startswith("auth"):
        return _result(Category.SECRET, "credential file")

    if _has_segment(path, ".git"):
        return _result(Category.EPHEMERAL, "nested VCS metadata is not needed for restore")
    if lower in {".ds_store"} or lower.endswith(("-wal", "-shm", ".swp")):
        return _result(Category.EPHEMERAL, "temporary or SQLite sidecar file")
    if name.startswith(".last-") or name.startswith("security_warnings_state_"):
        return _result(Category.EPHEMERAL, "update or warning state")
    if _under(
        path,
        "cache",
        "debug",
        "downloads",
        "session-env",
        "shell-snapshots",
        "paste-cache",
        "ide",
        "security",
        "backups",
        "distilled",
    ) or path == "stats-cache.json":
        return _result(Category.EPHEMERAL, "regenerable runtime data")
    if _under(path, "plugins/cache", "plugins/repos", "plugins/data"):
        return _result(Category.EPHEMERAL, "plugin checkout, cache, or runtime data")

    parts = PurePosixPath(path).parts
    if len(parts) >= 3 and parts[0] == "projects" and "memory" in parts[2:]:
        return _result(Category.MEMORY, "Claude Code project auto memory")

    if _under(
        path,
        "projects",
        "file-history",
        "tasks",
        "todos",
        "plans",
        "session-states",
        "tool-results",
        "usage-data/session-meta",
    ) or path == "history.jsonl":
        return _result(Category.SESSION, "Claude Code conversation or resumable state")

    if _under(path, "usage-data"):
        return _result(Category.STATE, "Claude Code local usage metadata")

    if (
        path in {
            "CLAUDE.md",
            "settings.json",
            "settings.local.json",
            "keybindings.json",
            "statusline.js",
            "statusline.sh",
        }
        or _under(
            path,
            "agents",
            "commands",
            "hooks",
            "skills",
            "rules",
            "plugins/marketplaces",
        )
        or path.startswith("plugins/") and name.endswith(".json")
    ):
        return _result(Category.CONFIG, "Claude Code durable configuration or extension")

    return _result(Category.UNKNOWN, "no Claude Code profile rule matched")
