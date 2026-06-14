# NAS常駐デプロイ手順

ローカルで開発し、研究室NAS(Docker)で常駐させ、メンバー全員が同じ知識ストアへ
接続する構成。コード=git管理、蓄積される状態=NAS共有ボリューム、で分離している。

```
[ローカルPC] ──git push──▶ [GitHub/社内git] ──git pull──▶ [NAS: Docker常駐]
  stdio で開発                                              streamable-http :8000
  paths=リポジトリ直下                                       paths=/state (NASボリューム)
                                                                  ▲
                                            [メンバーのClaude] http://<NAS-IP>:8000/mcp
```

## 状態とコードの分離

| 種類 | 置き場 | 制御 |
|------|--------|------|
| コード(`server.py`等) | gitリポジトリ → イメージ内 | `git pull` + `docker build` |
| `knowledge/`(知識＋レビュー待ちinbox) | NASボリューム `/state/knowledge` | `LIPIDMIX_KNOWLEDGE_DIR` |
| `analyses/`(解析記録・objective) | NASボリューム `/state/analyses` | `LIPIDMIX_ANALYSES_DIR` |
| `playbook/`(版管理された手順) | イメージ内(コード扱い) | gitで更新 |
| MS-DIAL出力データ | NASボリューム `/data`(読取専用) | `LIPIDMIX_DATA_DIR` |

環境変数が未設定なら全てリポジトリ直下を使う = **ローカル開発の挙動は従来通り**。

## NAS側 初回セットアップ

1. NASでリポジトリを取得(Synology Container Manager / SSH):
   ```sh
   git clone <repo-url> /volume1/lipidmix/app
   cd /volume1/lipidmix/app
   ```
2. 永続ディレクトリを作り、初期知識をシード(任意):
   ```sh
   mkdir -p /volume1/lipidmix/state/knowledge /volume1/lipidmix/state/analyses /volume1/lipidmix/data
   cp -rn knowledge/* /volume1/lipidmix/state/knowledge/   # 既存ノートを引き継ぐ場合
   cp -rn analyses/*  /volume1/lipidmix/state/analyses/
   ```
3. `docker-compose.yml` の `volumes:` 左側パスを実環境に合わせる(上記は Synology 例)。
4. 起動:
   ```sh
   docker compose up -d --build
   ```
5. 疎通確認: `curl -i http://localhost:8000/mcp` がHTTP応答を返せばOK。

## 開発→反映フロー

```sh
# ローカル: 編集 → コミット → push
git add -A && git commit -m "..." && git push

# NAS: 反映
cd /volume1/lipidmix/app && git pull && docker compose up -d --build
```

`/state` と `/data` はボリュームなので、再ビルドしても蓄積された知識・解析記録は消えない。

## メンバー側 接続設定(Claude Code)

各メンバーのマシンで一度だけ:
```sh
claude mcp add --transport http ms-data-parser http://<NAS-IP>:8000/mcp
```
Claude Desktop の場合は設定の MCP servers に同URL(streamable-http)を登録する。

## 注意点

- **LAN内限定で運用すること**。このサーバーに認証は無いので、NASのファイアウォール/
  リバースプロキシで研究室ネットワーク外からのアクセスを遮断する。
- **同時書き込み**: 単一プロセスで全リクエストを捌くので衝突は起きにくいが、knowledge
  ノートの promote/reject はファイル単位の read-modify-write。同一inbox項目を複数人が
  同時に処理すると競合しうる。レビュー運用ルールで実質回避できる範囲。
- **`docs/output_format.md` は必ずコミットしておく**(ゲートウェイ手順が参照する必須リソース。
  `.gitignore` を修正済みなので `git add docs/output_format.md` 可能)。
