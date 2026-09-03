# CLAUDE.md

このファイルは、このリポジトリで作業する Claude 向けの地図。**全体スキャンの代わりに読む**。

## このリポジトリは何か

MS-DIAL（リピドミクス LC-MS 解析ソフト）のバイナリ出力を読み、LLM から使える形で公開する
**MCP サーバ `ms-data-parser`**。ツール 51・リソース 4・リソーステンプレート 3。
対象形式は `.arf`（アライン後・サンプル別ピーク）/ `.arf2`（スポット代表）/ `.pai2`（個別測定）/
`.dcl`（デコンボリューション済み MS/MS、独自バイナリ）/ `.EIC.aef`（クロマトグラム）。
解析（PCA・前処理/QC・差次的解析）に加え、文献知識と再利用手順の蓄積層（`knowledge/` `playbook/`
`analyses/`）を MCP リソースとして配信する。

クライアントは Claude Desktop / Claude Code と、別リポの WebUI（下記「関連リポジトリ」）。

## コマンド

```bash
C:/Python314/python.exe -m pytest tests -q
```

- 全 **762 件**（`-m unittest discover -s tests -t .` はこれより少ない〔実測 580〕。差は
  pytest 関数形式で書かれた複数のテストファイル（`test_dataset_state.py` `test_console_runner.py`
  `test_mztab_tools.py` 等、mzTab/Console/DatasetState 層で追加）で、`unittest.TestCase` を継承しない
  ため unittest discover では拾えない）。**リポジトリルートから `pytest` で実行する**。
- MCP サーバ起動: `C:/Python314/python.exe server.py`（既定 stdio）。
- パーサ単体の CLI: `python -m lipidmix.arf.reader --file <path> --pca` など（README「Command-line examples」）。

**Python は `C:/Python314/python.exe` を使う**。`.mcp.json` / `.vscode/mcp.json` がこれを絶対パスで
指しており、依存もここに入っている。`.venv/` は存在しない。`.venv-1/` は 2026-07 のローカル LLM 検証用に
残っている別環境で、通常の開発・テストでは使わない。

環境変数（すべて任意・上書き用）: `LIPIDMIX_DATA_DIR`（データ探索先。既定 `<project>/data`）/
`LIPIDMIX_KNOWLEDGE_DIR` `LIPIDMIX_PLAYBOOK_DIR` `LIPIDMIX_ANALYSES_DIR` `LIPIDMIX_REPORTS_DIR`（蓄積先。
NAS 共有運用向け）/ `LIPIDMIX_TRANSPORT` `LIPIDMIX_HOST` `LIPIDMIX_PORT`（HTTP 待受）/ `LIPIDMIX_CAVEAT_MODE` /
`LIPIDMIX_PLOT_OUTPUT`（描画系ツールの戻り値。既定 `image`。Plotly で自分で描く
クライアント＝Use-LLLM は `payload` を置く）。

## 構成と、触るときの鉄則

ルート直下の `.py` は `server.py` と `check.py` の 2 つだけ。実装は `lipidmix/` にある。

```
lipidmix/core/      FastMCP インスタンス・設定・セッション状態・パス解決・共通ヘルパ（依存グラフの leaf）
lipidmix/msdial/    MS-DIAL 固有サイドカー（*_tags.xml / .mddata）・同定・ピーク検証・サンプル因子
lipidmix/analysis/  入力形式に依存しない数値処理（前処理/QC・PCA・差次的解析・エクスポート契約）
lipidmix/plots/     描画 payload の組み立てと matplotlib 描画（volcano / eic / render）
lipidmix/{arf,arf2,pai2,dcl,eic}/   形式ごとの reader.py（パーサ）と tools.py（MCP ツール）
lipidmix/mztab/     mzTab-M リーダ・DatasetState 構築
lipidmix/console/   MS-DIAL Console 実行層（job_manager / runner / output_collector）
lipidmix/handoff/   Console 成果物の受け渡しスキーマ（analysis-job.json）
lipidmix/corpus/    蓄積ノートの純ロジック（knowledge_store / paper_ingest）
lipidmix/tools/     形式に紐づかない MCP 公開層（入口・サンプル検索・目的・レポート・リソース）
```

- **`server.py` は薄いファサード**。import 副作用で `@mcp.tool` を登録し、`from ... import *`
  （各モジュールの `__all__` ＝そのモジュールのツール名）で公開面を再エクスポートするだけ。
  新しいツールを足すときは、実体を該当モジュールに書き `__all__` に載せる。
- **`server.py` をルートから動かさない**。`.mcp.json` / `.vscode/mcp.json` が絶対パスで指している。
- **可変状態の正準は `lipidmix.core.mcp_core`（`DATA_DIR` `KNOWLEDGE_DIR` `ANALYSES_DIR`）と
  `lipidmix.core.session_state`（`session`）**。`server.<name>` はスナップショット束縛にすぎないので、
  差し替え・モンキーパッチは必ず正準モジュール側に当てる。参照も `mcp_core.DATA_DIR` の
  module 修飾で行い、`from ... import DATA_DIR` を書かない。
- **`lipidmix.core.mcp_core` は leaf**。ここから `lipidmix.<形式>.tools` や `lipidmix.tools.*` を
  import してはならない（循環）。
- **セッション状態はパーサ別に分離済み**: `session.arf` / `.arf2` / `.pai2` / `.eic`。あるパーサが
  別スロットを触ってはいけない（`pai2_parser` が ARF の前処理行列を無言破棄した過去のバグの再発防止）。
- **前提状態が無いときは例外でなく機械可読な封筒を返す**:
  `{"error": {"code": "missing_state", "state": ..., "required_tools": [...], "message": ...}}`
  （`lipidmix/core/mcp_errors.py` の `missing_state`）。クライアントはこれを読んでリプレイする契約。
- **reader の MessagePack Key インデックスの正解表は `docs/schema/*.md`**（MS-DIAL の C# クラス定義）。
  インデックス定数を変える前に必ず参照する。推測で直さない。
- **`run_pca` の正準は `lipidmix/analysis/pca.py`**。`lipidmix/arf/reader.py` の同名は後方互換の
  再エクスポートで、ARF テストが `patch.object(server.arf_reader, "run_pca", ...)` で module 属性
  としてこの束縛を差し替えてモックしている。**消すとモックが効かなくなり、テストは緑のまま
  実物の scikit-learn PCA が走り出す。**
- **差次的エクスポートの列定義は `lipidmix/analysis/export_contract.py` が唯一の正準**。
  ARF（`arf_export_differential`）と mzTab-M（`dataset_export_differential`）の両経路がここを
  共有しており、**別リポジトリ（massbank-context）との契約**でもある。列の追加・改名・並べ替えは
  `CONTRACT_VERSION` の引き上げと下流の同時更新なしにやってはいけない。
- **ツールの戻り値を肥大させない**。戻り値はそのまま LLM の文脈を占め、結論が埋没する。
  守るべき決まりごと:
  - JSON は `lipidmix.core.serialization.json_payload()` で返す（`json.dumps(..., indent=2)`
    を書かない。実測で戻り値の 15〜57% が空白だった）。
  - 行が並ぶ一覧は `arf2/reader.py format_spots_as_table()` の TSV（列名 1 回）。
  - float は丸めてから返す（既定 repr は 17 桁出る）。座標点列は `round_floats()`。
  - 全ツールに `structured_output=False` を付ける。付けないと FastMCP が outputSchema を
    導出し、MCP が同じ内容を content と structuredContent の**両方**で送る（＝2 倍）。
  - 座標配列など巨大な中間データは payload から外してセッションに保持する
    （図保存ツールがそこから読む）。
  - 図は座標を LLM に渡すより**サーバで描いて画像で返す**ほうが 2 桁安い
    （volcano 実測: 点列 183,578 字 ≒数万トークン → PNG 327 画像トークン）。
    画像トークンは `幅×高さ/750` なので dpi は上げない（`plots/render.py`）。

## ドキュメントの地図（用途別に読み分ける）

| 知りたいこと | 見る場所 |
|---|---|
| ツールの引数・用途（`USAGE.md` 自体は Console/DatasetState 系ツールが未反映で実測 51 件から陳腐化） | `USAGE.md` |
| 出力フィールドの**意味**（行の粒度・脂質名文法・必須注意） | `docs/output_format/core.md` ＋ トピック別（`arf` `arf2` `pai2` `dcl` `eic` `identity`）。MCP リソース `lipidmix://docs/output-format[/{topic}]` としても配信 |
| ツールが**どのファイルのどの関数をどの順に呼ぶか** | `docs/workflow/`（9 文書・35 ツール分。範囲外 16 ツールは対象外）。行番号は書かない規約 |
| MessagePack の Key 番号 | `docs/schema/*.md` |
| 設計判断の経緯・調査で判明した事実 | `docs/HISTRY.md`（綴りはこのまま。**追跡外＝ローカル専用ログ**） |
| 進行中/完了タスク | `docs/task.md`（**追跡外**。ステータス = TODO/DOING/DONE/HOLD） |
| 過去の設計書・計画書 | `docs/superpowers/{specs,plans,notes}/` |

## テストの規約

- 腐敗防止テストが 2 つ効いている。壊すと落ちる:
  - `tests/test_workflow_docs.py` — `docs/workflow/` が挙げるパス・関数名を AST で実在検証し、
    対象ツール数を実登録数と突き合わせる。
  - `tests/test_package_layout.py` — 移動で静かに壊れる `BASE_DIR` 起点の解決を縛る。
- `tests/test_server_registration.py` がツール/リソースの登録数と `ToolAnnotations` を検証する。
- **fixture はテスト自身が作る**。`analyses/` `knowledge/` の実ファイルに依存させない
  （追跡外なのでユーザ環境依存の不安定テストになる。tmp に作って `ANALYSES_DIR` を差し替える流儀）。

## 作業の記録と Git

- 調査・実装をしたら `docs/HISTRY.md` に追記し、`docs/task.md` のステータスを更新する。
- `check.py` はスクラッチ（疑似ワークスペース）。一時検証コードをここに書き、通ったら適切な
  モジュールへ移して中身を消す。何もここに依存させない。
- `main` へのマージは `--no-ff`、`Merge <branch>: <日本語の要約>` 形式のマージコミット。
- マージ済みブランチは**ローカルのみ削除**し `origin` 側は残す。`push` は指示があっても都度確認する。

## 踏みやすい罠

- `.gitignore` に `*.txt` があるため、**新規の .txt は無言で追跡漏れする**（`git add -f` が要る）。
- `docs/HISTRY.md` `docs/task.md` `data/` `analyses/` `archives/` は追跡外。`knowledge/` は
  種ノートのみ追跡、`playbook/` は版管理対象。クリーンチェックアウトに無い前提で書く。
- `archives/` は 2026-07 に退避したローカル LLM エージェント・解釈精度評価のコード置き場
  （`local_llm_agent/` `interp_eval/` ほか）。**ライブ側からの参照はゼロ**。動かすには
  ルート＋当該フォルダを `PYTHONPATH` に載せる必要がある参照用の退避物。現行開発では触らない。
- `.arf` は同一フォルダに `DriftSpots.arf` と `PeakProperties.arf` が併存しうる。リゾルバは
  `PeakProperties.arf` を自動選択し、複数バッチが混在する場合はファイル名の
  `AlignmentResult_<timestamp>` で最新バッチを選ぶ。

## 関連リポジトリ

`C:\Users\yuu18\Use-LLLM` — このサーバをローカル LLM ＋ WebUI から操作する**別リポの独立システム**。
本体の変更では基本的に触らない。往復（`missing_state` 封筒の解釈やツールカタログ）を疑うときだけ、
そちらのメモリ（`use-lllm-*`）を参照する。
