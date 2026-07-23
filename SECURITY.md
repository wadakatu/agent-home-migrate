# Security policy

## Reporting a vulnerability

Open a GitHub security advisory or contact the maintainers privately. Do not attach a real
`.ahm.zip`, `.codexbundle`, transcript, memory file, database, or home-directory listing.
Create the smallest synthetic fixture that demonstrates the issue.

## Sensitive data

Migration bundles can contain prompts, source code, terminal output, absolute project paths,
images, and secrets accidentally printed during a session. A plaintext bundle should be
handled like shell history plus a source-code snapshot.

Credential files are excluded by default, but included configuration can still embed environment
variables, bearer tokens, or HTTP headers. `doctor` and `plan` audit documented secret-capable
fields without printing their values. Plaintext export is blocked when those fields are present or
the selected configuration cannot be audited, unless `--allow-plaintext-secrets` is explicit.

- Do not commit bundles to Git.
- Do not upload bundles to public issue trackers.
- Prefer `age` encryption before using cloud storage or an untrusted transfer channel.
- Re-authenticate on the destination instead of migrating credential files.
- Keep an independent backup until representative sessions and memories resume correctly.

## Supported trust boundary

The current implementation is intended for bundles created by the same user. It validates
checksums, paths, entry types, symlinks, and restore destinations, but it is not a sandbox for
actively hostile multi-gigabyte archives. Inspect provenance before decrypting or restoring a
bundle received from another person.
