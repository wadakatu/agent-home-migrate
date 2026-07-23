# agent-home-migrate

`agent-home-migrate` (`ahm`) は、Codex と Claude Code のローカル状態を、
不要な worktree・cache・log・認証情報から分離して移行するためのCLIです。

現在は安全性を優先した Alpha 版です。ライブ環境を唯一のコピーとして扱わず、
Time Machine や restic などの独立した完全バックアップと併用してください。

## 特徴

- `config`、`memory`、`session`、`state`、`secret`、`ephemeral`、`unknown` に分類
- `auth.json`、`.credentials.json` などをデフォルト除外
- 設定内のsecret-capable fieldを値を表示せず構造監査
- Codex worktree、cache、log、Claude session-env などをデフォルト除外
- SHA-256付き、バージョン管理されたZIP bundle
- SQLiteをファイルコピーせず、SQLite Online Backup APIでスナップショット
- 復元はdry-runが既定。書き込みには明示的な `--apply` が必要
- 衝突時は上書きせず、明示的な `replace-with-backup` のみ許可
- `age` があればbundleを公開鍵暗号化可能
- Python標準ライブラリのみで動作

## クイックスタート

リポジトリから直接実行できます。

```console
PYTHONPATH=src python3 -m agent_home_migrate doctor
PYTHONPATH=src python3 -m agent_home_migrate plan
```

インストールする場合:

```console
python3 -m pip install .
ahm doctor
```

### 1. 旧PCを診断する

```console
ahm doctor
ahm plan
```

`plan` はパス、種別、サイズを集計します。例外として、既定でbundleへ含める
`config.toml` / `*.config.toml` / `settings*.json` だけは構造として解析し、
secret-capableなフィールド名と件数を値なしで報告します。セッション、memory、
履歴の本文は読みません。除外ディレクトリは高速化のため一つのtreeとして集計します。
全ファイルの分類を確認したい場合は `ahm plan --full` を使います。

### 2. CodexとClaude Codeを終了してbundleを作る

```console
ahm export --output ~/Backups/agent-state.ahm.zip
```

暗号化する場合:

```console
ahm export \
  --output ~/Backups/agent-state.ahm.zip.age \
  --age-recipient age1example...
```

認証ファイルを除外しても、CodexのMCP・model provider設定やClaude Codeの
`settings.json` の `env` にはトークン、環境変数、HTTP headerを直接設定できます。
`doctor` / `plan` は既知のsecret-capable fieldを値を表示せず警告します。該当フィールドが
ある、または設定を安全に解析できない場合、平文exportは停止するため、原則として
`--age-recipient` を指定してください。

既定で含めるもの:

- Codex/Claude Codeの設定、rules、skills、hooks
- memory
- session、history、session関連画像
- Codexのmemory/state/goals SQLite（Online Backup経由）

既定で含めないもの:

- 認証情報、OAuth lock
- Codex worktrees、cache、logs、tmp
- Claude cache、session-env、shell snapshot、plugin cache
- 分類できなかったファイル

`--include-secrets` は用意されていますが、通常は使わず新PCで再ログインしてください。
使用する場合は `--age-recipient` が必須です。secret-capableな設定を含む平文bundleや、
明示的に認証ファイルを含める平文bundleを、すでに暗号化されたローカルボリュームへ
出力する特殊な場合だけ `--allow-plaintext-secrets` で解除できます。

`CODEX_SQLITE_HOME` / `sqlite_home` が `CODEX_HOME` 外を指す場合や、Claude Codeの
`autoMemoryDirectory` が設定されている場合、現MVPは不完全なbundleを作らずexportを
停止します。`doctor` と `plan` に理由が表示されます。

### 3. bundleを検証する

```console
ahm inspect ~/Backups/agent-state.ahm.zip
ahm verify ~/Backups/agent-state.ahm.zip
```

### 4. 新PCでdry-runする

```console
ahm restore ~/Backups/agent-state.ahm.zip --target-root "$HOME"
```

これは何も書き込みません。内容を確認した後に適用します。

```console
ahm restore ~/Backups/agent-state.ahm.zip \
  --target-root "$HOME" \
  --apply
```

既存ファイルと内容が異なる場合、既定ではエラーになります。どうしても置き換える場合:

```console
ahm restore ~/Backups/agent-state.ahm.zip \
  --target-root "$HOME" \
  --on-conflict replace-with-backup \
  --apply
```

置換前のファイルは `<target-root>/.agent-home-migrate-backups/<bundle-id>/` に残ります。

### 5. 復元結果を照合する

```console
ahm verify ~/Backups/agent-state.ahm.zip --target-root "$HOME"
```

## パスが変わる場合

本ツールは、未知のJSONL/SQLite形式を推測して書き換えません。ユーザー名や
プロジェクトの絶対パスが変わる場合は、まず空のstaging rootへ復元してください。

```console
mkdir -p ~/migration-staging
ahm restore agent-state.ahm.zip --target-root ~/migration-staging --apply
```

その後、セッションのパス変換には
[`codex-claude-transfer`](https://github.com/ahmojo/codex-claude-transfer) の
`--map-cwd` を使う方針です。`ahm` は、バージョン不明の内部形式を一括置換しないことを
安全上の境界にしています。

## 再現可能なPC構築との役割分担

`ahm` が扱うのは変化するローカル状態です。アプリとdotfilesは別レイヤーにします。

- アプリ/CLI: Homebrew Bundleの `Brewfile`、またはnix-darwin
- dotfiles/設定: chezmoi
- Codex/Claudeのローカル状態: `ahm`
- 災害復旧用の完全バックアップ: resticまたはTime Machine

詳しい設計と脅威モデルは [docs/design.md](docs/design.md) を参照してください。

## テスト

```console
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

テストは一時ディレクトリ内の合成データだけを使い、実際の
`~/.codex` / `~/.claude` には触れません。

## 参考仕様

- [Codex environment variables](https://learn.chatgpt.com/docs/config-file/environment-variables)
- [Codex advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference#configtoml)
- [Codex memories](https://learn.chatgpt.com/docs/customization/memories)
- [Claude Code memory](https://code.claude.com/docs/en/memory)
- [Claude Code sessions](https://code.claude.com/docs/en/sessions)
- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code environment variables](https://code.claude.com/docs/en/env-vars)
