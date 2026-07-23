from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_home_migrate.secret_audit import audit_secret_capabilities

from helpers import make_agent_homes, write


class SecretAuditTests(unittest.TestCase):
    def test_known_codex_secret_capabilities_are_counted_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            write(
                homes["codex"] / "config.toml",
                """
[mcp_servers.demo.env]
API_TOKEN = "codex-secret-one"
REGION = "test"

[mcp_servers.demo.http_headers]
Authorization = "codex-secret-two"

[mcp_servers.remote.env_http_headers]
Authorization = "TOKEN_ENV_NAME"

[model_providers.gateway]
experimental_bearer_token = "codex-secret-three"

[shell_environment_policy.set]
PRIVATE_VALUE = "codex-secret-four"

[otel.exporter.otlp-http.headers]
x-api-key = "codex-secret-five"
""".lstrip(),
            )

            audit = audit_secret_capabilities(homes)
            by_pattern = {
                finding.field_pattern: finding.entries for finding in audit.findings
            }

            self.assertEqual(by_pattern["mcp_servers.<id>.env"], 2)
            self.assertEqual(by_pattern["mcp_servers.<id>.http_headers"], 1)
            self.assertEqual(by_pattern["mcp_servers.<id>.env_http_headers"], 1)
            self.assertEqual(
                by_pattern["model_providers.<id>.experimental_bearer_token"], 1
            )
            self.assertEqual(by_pattern["shell_environment_policy.set"], 1)
            self.assertEqual(by_pattern["otel.exporter.<id>.headers"], 1)
            serialized = json.dumps(audit.public_dict())
            for secret in (
                "codex-secret-one",
                "codex-secret-two",
                "codex-secret-three",
                "codex-secret-four",
                "codex-secret-five",
                "TOKEN_ENV_NAME",
            ):
                self.assertNotIn(secret, serialized)

    def test_claude_env_is_counted_without_names_or_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            write(
                homes["claude"] / "settings.json",
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_API_KEY": "claude-secret-one",
                            "ANTHROPIC_AUTH_TOKEN": "claude-secret-two",
                        }
                    }
                ),
            )

            audit = audit_secret_capabilities(homes)
            finding = next(
                finding
                for finding in audit.findings
                if finding.provider == "claude"
            )

            self.assertEqual(finding.field_pattern, "env")
            self.assertEqual(finding.entries, 2)
            serialized = json.dumps(audit.public_dict())
            self.assertNotIn("ANTHROPIC_API_KEY", serialized)
            self.assertNotIn("claude-secret-one", serialized)
            self.assertNotIn("claude-secret-two", serialized)

    def test_parse_failure_is_reported_without_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            write(
                homes["claude"] / "settings.json",
                '{"env":{"TOKEN":"must-not-appear",}}',
            )

            audit = audit_secret_capabilities(homes)

            self.assertTrue(audit.requires_plaintext_approval)
            self.assertEqual(audit.errors[0].reason, "invalid JSON")
            self.assertNotIn("must-not-appear", json.dumps(audit.public_dict()))

    def test_codex_profile_config_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            homes = make_agent_homes(Path(temp_name))
            write(
                homes["codex"] / "private.config.toml",
                '[model_providers.gateway]\nexperimental_bearer_token = "hidden"\n',
            )

            audit = audit_secret_capabilities(homes)

            profile_findings = [
                finding
                for finding in audit.findings
                if finding.relative_path == "private.config.toml"
            ]
            self.assertEqual(len(profile_findings), 1)
            self.assertEqual(
                profile_findings[0].field_pattern,
                "model_providers.<id>.experimental_bearer_token",
            )
            self.assertNotIn("hidden", json.dumps(audit.public_dict()))

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_config_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            homes = make_agent_homes(root / "homes")
            external = write(
                root / "outside.toml",
                '[mcp_servers.demo.env]\nTOKEN = "outside-secret"\n',
            )
            config = homes["codex"] / "config.toml"
            config.unlink()
            config.symlink_to(external)

            audit = audit_secret_capabilities(homes)

            self.assertEqual(audit.errors[0].reason, "not a regular file")
            self.assertNotIn("outside-secret", json.dumps(audit.public_dict()))


if __name__ == "__main__":
    unittest.main()
