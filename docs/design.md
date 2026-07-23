# Design

## Goal

新しいPCをクリーンに構築しつつ、Codex/Claude Codeのローカルなmemoryとsessionを
取りこぼさない。構成の再現と、変化する状態の復元を分離する。

## Non-goals

- OS全体のクローン
- package managerやdotfiles managerの再実装
- Codex/Claude Codeの非公開形式を推測した変換
- 認証トークンの同期
- 独自暗号実装

## Architecture

```text
Codex home ─┐                    ┌─ manifest.json
            ├─ inventory ─ plan ├─ payload/codex/...
Claude home ┘                    └─ payload/claude/...
                                  │
                                  └─ optional age encryption

bundle ─ verify ─ restore plan ─ explicit --apply ─ target homes
                                      │
                                      └─ conflict backup + atomic replace
```

### Provider adapters

`profiles.py` にCodexとClaude Codeの分類規則を置く。規則はファイル内容ではなく
相対パスだけを見る。未知のパスは `unknown` としてデフォルト除外する。

分類の優先順位:

1. secret
2. ephemeral
3. memory
4. session
5. state
6. config
7. unknown

memoryはsessionディレクトリの内側に置かれる場合があるため、実装では
provider固有の例外を優先する。

Claude Codeでは `projects/<project>/<session>.jsonl` をresumable transcriptとして保持する。
一方、`sessions/` のprocess registry、`telemetry/`、plugin cleanup marker、
`plugins/marketplaces/` のcloneは再生成可能なruntime dataとして除外する。
`plugins/known_marketplaces.json` などの宣言的metadataはconfigとして保持し、marketplaceを
新PCで再取得できる状態を残す。

### Secret-capability audit

パス分類だけでは、既定で含める設定ファイル内の秘密値を検出できない。そのため
`config.toml`、Codex profile config、Claude Codeの `settings*.json` に限り、標準ライブラリで
構造解析する。監査対象は公式仕様上、値を直接保持できる既知フィールドに固定する。

- Codex: MCP/model providerの `env`、HTTP header、bearer token、shell環境設定、OTEL header
- Claude Code: `settings*.json` の `env`

値や環境変数名、MCP server/providerの識別子は出力せず、正規化したフィールドpatternと
entry件数だけを報告する。値の形式から秘密情報を推測したり、自動redactionしたりしない。
設定が解析不能な場合も安全側に倒し、平文exportには暗号化または
`--allow-plaintext-secrets` による明示承認を要求する。

### Bundle schema

ZIPのルートには `manifest.json` と `payload/` だけを許可する。

```text
manifest.json
payload/codex/<relative path>
payload/claude/<relative path>
```

manifestには次を記録する。

- schema (`agent-home-migrate/v1`)
- bundle id
- 作成時刻、作成ツールのバージョン
- OS/Python/Codex/Claude Codeのバージョン
- 選択したcategory
- 各entryのprovider、相対パス、category、size、mode、mtime、SHA-256
- SQLiteにOnline Backupを使ったか
- symlinkの場合はlink target

絶対のagent home、ホスト名、ユーザー名はmanifestへ積極的に追加しない。ただし
session本文自体にはプロジェクトの絶対パスや秘密情報が含まれ得るため、bundle全体を
機密データとして扱う。

### SQLite

SQLiteのmain databaseを通常コピーするとWALとの不整合が起きる可能性がある。
`.sqlite`、`.sqlite3`、`.db` はPythonの `sqlite3.Connection.backup()` で一時DBへ
スナップショットし、それをbundleへ格納する。`-wal` と `-shm` は格納しない。
Online Backupに失敗した場合はraw copyへフォールバックせず、export全体を失敗させる。

### Restore safety

- dry-runが既定
- bundle全entryのチェックサム検証後にのみ書き込み開始
- ZIP slip、絶対パス、`..`、予期しないentryを拒否
- symlinkをたどらず、復元するsymlinkもagent home外を指すものは拒否
- 既存と同一内容はno-op
- 差分があれば既定はfail
- 置換は `replace-with-backup` のみ
- ファイルは同一ディレクトリの一時ファイルからatomic rename
- 途中失敗時は、このtransactionで作成・置換したファイルをロールバック

### Live process safety

`ps` によるprocess検出はagentごとに `running` / `stopped` / `unknown` を返す。
`ps` の権限エラー、timeout、非ゼロ終了、実行不能を空のprocess一覧へ変換しない。
デフォルトhomeへのexportと適用restoreは `running` と `unknown` のどちらでも停止する。
明示的なstaging homeは対象外とし、検出不能でも続行する唯一の操作は
`--allow-live` による明示承認とする。

### Export preflight

export sourceとして指定されたCodex/Claude homeは、両方が存在する通常のdirectoryであり、
symlinkではないことを必須とする。このhome safety checkはoverride不可とし、欠落したhomeを
空データとして扱うbundleや、意図しないsymlink先のexportを防ぐ。

`doctor --json`は同じhome safety checkに加え、標準homeのprocess state、標準外データ保存先、
secret-capable設定に対する`age`の有無を評価し、`export_preflight.ready`と構造化blockerを返す。
これは危険なoverrideなしで安全なexportを開始するための前提条件を示し、出力先やrecipient
など実際のexport引数の妥当性までは保証しない。

### Encryption verification

通常のテスト環境で`age`が利用できない場合、実CLIを使う統合テストは明示的にskipする。
GitHub Actionsの専用jobでは`age`を導入し、鍵生成、暗号化bundle作成、identityによる
検証・復号、staging rootへの復元、復元後checksum照合を一続きで実行する。
専用jobは`AHM_REQUIRE_AGE=1`を設定し、`age`または`age-keygen`が見つからない状態を
skipではなく失敗として扱う。

### Agent automation contract

各サブコマンドの`--json`は、成功reportをstdoutへ出す。コマンド解析後に
`MigrationError`または割り込みが発生した場合は、`ok: false`と安定したerror codeを持つ
JSON envelopeをstderrへ出す。restore conflictや復元照合の不一致は、有効なreportを
stdoutへ返したうえで終了コード3とする。これによりagentは人間向け文言の解析ではなく、
出力stream、error code、process exit codeを組み合わせて判断できる。

## Version policy

Codex/Claude Codeの保存形式は安定APIではない。bundleはsource versionを記録するが、
通常の復元は内容を書き換えないため、未知バージョンでもbyte-preservingなstaging復元は
可能とする。

パス変更のように内容変換が必要な操作は本MVPでは行わない。専用ツールか、将来追加する
バージョン指定adapterに限定する。adapterはfixtureによるround-tripテストを必須とする。

`CODEX_SQLITE_HOME` / `sqlite_home` やClaude Codeの `autoMemoryDirectory` によって
データが標準home外へ置かれている場合、MVPはexportを停止する。未対応データを黙って
欠落させるより、明示的なprovider root対応を追加してから再実行する方針とする。

## Reproducible workstation repository

公開コードと個人データを分ける。

```text
public migration tool/template
  ├─ ahm source
  ├─ Brewfile example
  ├─ chezmoi bootstrap example
  └─ CI synthetic fixtures

private workstation config
  ├─ Brewfile
  ├─ chezmoi source state
  └─ encrypted ahm/restic backups (never normal Git)
```

厳密なpackage version固定が必要な利用者は、Brewfileレイヤーをnix-darwin/Home Managerへ
置き換えられる。mutable stateのbundle設計は変わらない。

## Future work

- `cct` bundleの同梱/連携
- version-gatedなCodex/Claude project path adapter
- restic repositoryへの直接格納
- signed bundle manifest
- Windows path/ACL fixture
- install済みplugin一覧を宣言的設定へ抽出するexporter
