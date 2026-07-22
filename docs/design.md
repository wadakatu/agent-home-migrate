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
