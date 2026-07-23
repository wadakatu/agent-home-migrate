# Changelog

## 0.2.0 - 2026-07-23

### Added

- Codex and Claude Code config, memory, session, and state migration
- SQLite Online Backup snapshots with integrity verification
- secret-capable configuration audit without printing values
- structured JSON reports and stable automation error codes
- export preflight for home, process, external-location, and encryption checks
- cross-platform CI and a real `age` encryption/decryption round-trip

### Security

- authentication files, caches, logs, runtime clones, and unknown files are excluded by default
- live or indeterminate default-home exports and restores fail closed
- plaintext export of secret-capable configuration requires explicit approval
- bundles use owner-only file permissions and restores reject unsafe paths and symlinks

## 0.1.0 - 2026-07-22

- Initial alpha implementation.
