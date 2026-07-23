# Release process

`agent-home-migrate`の実移行は、GitHub release tagを固定したインストールを前提とする。
release tag、CLI、bundle manifest、Python distribution metadataは同じversionを示す必要がある。

## 1. Release preparation

1. `src/agent_home_migrate/__init__.py`の`__version__`を更新する。
2. `CHANGELOG.md`とREADMEの固定tag例を同じversionへ更新する。
3. 全テスト、package build、インストール後のversion照合を実行する。
4. release preparation PRをマージし、mainの全CI jobが成功したことを確認する。

`pyproject.toml`は`agent_home_migrate.__version__`を動的に読み込むため、versionを
重複定義しない。

## 2. Tag and GitHub Release

mainのrelease commitを明示してannotated tagを作る。

```console
git switch main
git pull --ff-only
git status --short
git tag -a v0.2.0 <release-commit-sha> -m 'Release v0.2.0'
git push origin v0.2.0
gh release create v0.2.0 --verify-tag --generate-notes --prerelease
```

Alpha期間中はGitHub Releaseをpre-releaseとして公開する。tagは公開後に移動・再利用しない。

## 3. Tagged-install smoke test

release tagから隔離インストールし、versionを確認する。

```console
uv tool install --force 'git+https://github.com/wadakatu/agent-home-migrate.git@v0.2.0'
ahm --version
# 0.2.0
```

その後、一時的な合成homeまたは独立したstaging rootを使って
`doctor`、`plan`、暗号化export、`verify`、dry-run restore、適用restore、
復元後`verify`を確認する。実際の`~/.codex`や`~/.claude`をrelease smoke testに使わない。
