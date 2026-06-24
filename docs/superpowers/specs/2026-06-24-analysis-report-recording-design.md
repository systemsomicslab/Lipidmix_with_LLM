# 解析・解釈レポート記録機能 設計

- 日付: 2026-06-24
- analysis_id 連携: 既存 objective レコード（`analyses/<analysis_id>.md`）と同一IDで突合
- ステータス: 設計確定（実装計画はこの後 writing-plans で作成）

## 目的と背景

MS-DIAL ロジオミクスを LLM/MCP で解析するこのシステムに、**解析・解釈のレポートを md ファイルに記録する機能**を追加する。

レポートの第一目的は **成果物＋記録の両方**：

- 成果物: 研究者が読む、まとまった「結果報告書」
- 記録: 後のセッション/LLM が経過・確定所見を読み戻し、作業を再開・引き継ぐための作業記録

所見が出るたびに追記でき、最終的に人間が読める成果物にもなる。既存の蓄積層と次のように対応する：

| 層 | 役割 | 配置 |
| --- | --- | --- |
| `analyses/<analysis_id>.md` | 実験**目的**（objective、入力側） | プロジェクト内 `analyses/` |
| **`reports/<analysis_id>.md`（本機能）** | 解析**所見・解釈**（出力側） | 解析フォルダ配下（不可なら退避先） |
| `knowledge/`, `playbook/` | 実験横断の再利用知識 | 共有（NAS可） |

目的（`analyses/`）と所見（`reports/`）は同一 `analysis_id` で突合する。

## アーキテクチャ方針

既存の `knowledge_store` 基盤を再利用する（新モジュールは作らない）。レポートは frontmatter ＋ 本文の md であり、書き出しは既存の `knowledge_store.write_note()`（ディレクトリロック・frontmatter 整形込み）をそのまま使える。新規に必要なのは:

1. 書き込み先フォルダの解決（解析フォルダ → 不可なら退避先）
2. `analysis_id` からの読み戻し・一覧
3. セッションの PCA 結果からの図PNG生成

これらを `server.py` 内の薄いヘルパー＋MCPツールとして足す。既存パターン（`_state_dir`、`_resolve_objective_file`）と完全に揃える。レポートに索引・集計・`[[link]]` 展開などが本当に必要になった時点で、専用 `report_store.py` への切り出しを検討する（現時点では YAGNI）。

## ① データモデル

### ファイル配置

- 既定: `<解析フォルダ>/reports/<analysis_id>.md`（`load_dataset` で渡したフォルダ＝現在の `DATA_DIR` 配下）
- 退避先: 書き込み不可なら `LIPIDMIX_REPORTS_DIR`（未設定時は `<project>/reports`）配下の同名ファイル
- 図: 採用した `reports/` 配下の `figures/<analysis_id>_pca.png`

### frontmatter スキーマ

```yaml
type: report
analysis_id: 2026-06-13-neg-lipidome-trt-vs-ctrl
dataset: "NEG / 2_lipidome_lcms"
date: 2026-06-24          # 最終更新日（上書き時に更新）
status: draft             # draft / final
knowledge_refs: [slug1, slug2]   # 本文で引用した knowledge/playbook の slug
```

### 本文の推奨セクション雛形

ツールは見出しだけを雛形として提示し、中身は LLM が自由記述する（frontmatter ＋ 推奨セクション雛形方式）。

- `## 目的`（confirmed_objective の要約）
- `## 実施した解析`（実行したツール・フィルタ条件）
- `## 主要な所見`（データ事実 ＋ 根拠: m/z・RT・PC寄与・S/N 等）
- `## 解釈`（所見の生物学的解釈、引用 knowledge の `source` 明記）
- `## 注意点・コンフリクト`（data vs literature の不一致、新規性候補、`claim_strength: speculative` の旗）
- `## 結論`

この雛形は MCP instructions（GATEWAY）の「コンフリクトは平均せず前景化」「出典明記」方針と整合させる。

## ② 書き込み先の解決と上書きセマンティクス

### 書き込み先解決ヘルパー `_resolve_report_dir()`

`server.py`、既存 `_state_dir` と同流儀:

1. 現在の `DATA_DIR`（解析フォルダ）配下 `reports/` を試す
2. 書き込みテスト（一時ファイル作成）に失敗したら `LIPIDMIX_REPORTS_DIR`（未設定時 `<project>/reports`）へフォールバック
3. 採用したパスを返す。図は `<reports>/figures/` に置く

### 上書きセマンティクス

`write_report` は毎回フル本文を受け取り `<reports>/<analysis_id>.md` を上書きする（`knowledge_store.write_note` がディレクトリロックで原子的に書き出す）。frontmatter の `date` を更新日に更新。所見の蓄積は LLM が本文を再生成して上書きする方式（ファイル内の追記ロジックは持たない）。

## ③ ツール仕様（3＋1ツール）

- `write_report(analysis_id, dataset, body, status="draft", knowledge_refs=None)`
  frontmatter 組立 ＋ `write_note` で上書き。採用パス（解析フォルダ/退避先どちらか）を戻り値で明示する。
- `read_report(analysis_id)`
  解析フォルダ → 退避先の順に `<analysis_id>.md` を探して本文を返す。無ければ案内文字列。
- `list_reports()`
  両方の `reports/` を走査し、`analysis_id` / `date` / `status` の1行索引を返す。
- `save_pca_figure(analysis_id, title=None)`
  直近のセッション PCA 結果から PNG を生成し `figures/<analysis_id>_pca.png` に保存、本文埋め込み用の相対パス（`![PCA](figures/<analysis_id>_pca.png)`）を返す。

### 実装上の注意：`save_pca_figure` のパーサ非依存化

`save_pca_figure` をパーサ種別によらず動かすため、`pai2` / `arf` / `arf_re_pca` の各 PCA 実行時に「描画に必要な最小データ」を `session.last_pca_plot` に保存しておく：

- PC1/PC2 スコア座標、サンプル名、説明分散比、タイトル

現状 `pai2` は `session.pca_result` を持つが `arf` 系はローカル変数止まりなので、共通の `session.last_pca_plot` を各所で埋める小改修を入れる。これにより `save_pca_figure` はセッション状態だけを見て図を再生成できる。

## ④ エラー処理

- 書き込み不可の二重失敗（解析フォルダも退避先も不可）→ 例外メッセージで試行パスを明示し、`LIPIDMIX_REPORTS_DIR` 設定を促す
- `read_report` / `save_pca_figure` で対象が無い場合（レポート未作成、セッションに PCA 結果なし）→ エラーではなく案内文字列（既存ツールの流儀：「先に〜してください」）
- `analysis_id` の未指定・不正文字 → `make_slug` で安全化してファイル名衝突を防止
- `save_pca_figure`：`session.last_pca_plot` が未設定なら「先に arf_parser / pai2_parser 等で PCA を実行してください」

## ⑤ テスト方針

`tests/`、`knowledge_store` の既存テストと同流儀。matplotlib 依存部分は分離する。

- `write_report` → 指定ディレクトリに frontmatter ＋ 本文が書かれ、再呼び出しで上書きされること
- 書き込み先解決 → 解析フォルダ書き込み可なら採用、不可をシミュレートしたら退避先へフォールバック
- `read_report` / `list_reports` → 書いたものを `analysis_id` で読み戻せる／一覧に出る／存在しない場合の案内
- `save_pca_figure` → `last_pca_plot` → 図データ整形の純ロジック部分を単体テスト。PNG 書き出し自体はスモークに留める

## ⑥ ドキュメント更新

- README のツール一覧に4ツール追記
- `docs/HISTRY.md` に設計記録
- `.env.example` に `LIPIDMIX_REPORTS_DIR` を追記

## スコープ外（YAGNI）

- レポートの `[[link]]` グラフ展開・構造予算付き索引（必要になれば `report_store.py` へ切り出し）
- ファイル内のセクション単位インクリメンタル追記（上書き再生成で代替）
- PCA 以外の図（EIC クロマトグラム等）の保存
