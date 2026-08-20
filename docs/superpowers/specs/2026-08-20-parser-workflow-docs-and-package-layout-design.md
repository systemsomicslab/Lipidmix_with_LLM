# パーサ/プロットのワークフロー文書化とパッケージ再編

- 日付: 2026-08-20
- 状態: 設計承認済み
- 関連: `docs/output_format/*.md`（出力フィールドの意味）、`USAGE.md`（ツール外形）

## 1. 背景と目的

`ms-data-parser` の Python モジュールはリポジトリルート直下にフラットに 35 個並んでいる。
どのファイルがどの入力形式を担当するのかはファイル名からしか読めず、1 つの MCP ツール
（例 `arf_pca_preprocessed`）が実際にどのモジュールのどの関数を何の順に呼ぶのかは、
`tools_arf.py` → `tool_helpers.py` → `session_state.py` → `arf_reader.py` と
自力で追わないと分からない。

本作業のゴールは 2 つ。

1. **ワークフロー文書の新設** — パーサ系・プロット系の各 MCP ツールについて、
   「どのファイルのどの関数を、どの順に呼ぶか」を呼び出し連鎖として明文化する。
2. **対象ファイル別のパッケージ再編** — `.arf` / `.arf2` / `.pai2` / `.dcl` / `.EIC.aef`
   という入力形式ごとにフォルダを切り、解析層・描画層を別軸として分離する。

この 2 つは同じ作業の表裏である。文書の「どのファイル」という記述は再編後のパスで
書かれるべきで、逆に再編の妥当性は呼び出し連鎖が読みやすくなったかで測られる。

### 実測した現状（2026-08-20 時点）

| 項目 | 実測値 |
|------|--------|
| テストスイート | 535 件 OK / skip 3 / 0.69 秒 |
| 登録 MCP ツール | 40 個（`USAGE.md` の「39ツール」は 1 件古い） |
| 登録 MCP リソース | 4 + テンプレート 3 = 7（`mcp_core.py` の「6 リソース」コメントは古い） |
| ルート直下 `.py` | 35 個（`server.py` `check.py` 含む） |
| 最大モジュール | `arf_reader.py` 1014 行 / `tools_arf.py` 973 行 |

## 2. 目標構造

```
リポジトリルート/
├─ server.py              MCP ファサード（役割は現状のまま、import 先だけ差し替え）
├─ check.py               スクラッチ（不動）
├─ lipidmix/
│   ├─ __init__.py
│   ├─ core/              mcp_core.py  session_state.py  path_resolvers.py
│   │                     tool_helpers.py  mcp_errors.py  data_config.py
│   ├─ msdial/            classes.py  tags.py  sample_factors.py
│   │                     lipid_identity.py  peak_verification.py
│   ├─ arf/               reader.py  tools.py  exclusions.py
│   ├─ arf2/              reader.py  tools.py
│   ├─ pai2/              reader.py  tools.py
│   ├─ dcl/               reader.py  tools.py
│   ├─ eic/               reader.py  tools.py  identity_map.py
│   ├─ analysis/          preprocessing.py  differential.py
│   ├─ plots/             volcano.py  eic.py
│   ├─ corpus/            knowledge_store.py  paper_ingest.py
│   └─ tools/             dataset.py  samples.py  objective.py
│                         reports.py  resources.py
├─ knowledge/  playbook/  analyses/  data/  reference/   ← 既存データ（不動）
└─ docs/  tests/
```

### 層の責務

- **形式パッケージ**（`arf/` `arf2/` `pai2/` `dcl/` `eic/`）— そのバイナリ形式を読むこと、
  および対応する MCP ツールを公開すること。この 2 つだけ。統計処理と描画は持たない。
- **`analysis/`** — 入力形式に依存しない数値処理（正規化・QC・補完・検定・多重比較補正）。
- **`plots/`** — レンダラ中立の payload 組み立て。matplotlib は明示保存時のみ触る。
- **`core/`** — FastMCP インスタンス、セッション状態、パス解決、共通ヘルパ。依存の底。
- **`msdial/`** — MS-DIAL 固有のサイドカー（`*_tags.xml` / `.mddata`）と同定・検証ロジック。
- **`corpus/`** — 蓄積ノートの純ロジック（MCP に依存しない）。
- **`tools/`** — 形式に紐づかない MCP 公開層（入口・サンプル検索・目的記録・レポート・リソース）。

### 移動表

| 現在 | 移動先 |
|------|--------|
| `mcp_core.py` | `lipidmix/core/mcp_core.py` |
| `session_state.py` | `lipidmix/core/session_state.py` |
| `path_resolvers.py` | `lipidmix/core/path_resolvers.py` |
| `tool_helpers.py` | `lipidmix/core/tool_helpers.py` |
| `mcp_errors.py` | `lipidmix/core/mcp_errors.py` |
| `data_config.py` | `lipidmix/core/data_config.py` |
| `msdial_classes.py` | `lipidmix/msdial/classes.py` |
| `msdial_tags.py` | `lipidmix/msdial/tags.py` |
| `sample_factors.py` | `lipidmix/msdial/sample_factors.py` |
| `lipid_identity.py` | `lipidmix/msdial/lipid_identity.py` |
| `peak_verification.py` | `lipidmix/msdial/peak_verification.py` |
| `arf_reader.py` | `lipidmix/arf/reader.py` |
| `tools_arf.py` | `lipidmix/arf/tools.py` |
| `exclusions.py` | `lipidmix/arf/exclusions.py` |
| `arf2_reader.py` | `lipidmix/arf2/reader.py` |
| `tools_arf2.py` | `lipidmix/arf2/tools.py` |
| `pai2_reader.py` | `lipidmix/pai2/reader.py` |
| `tools_pai2.py` | `lipidmix/pai2/tools.py` |
| `dcl_reader.py` | `lipidmix/dcl/reader.py` |
| `tools_dcl.py` | `lipidmix/dcl/tools.py` |
| `eic_aef_reader.py` | `lipidmix/eic/reader.py` |
| `eic_identity_map.py` | `lipidmix/eic/identity_map.py` |
| `tools_eic.py` | `lipidmix/eic/tools.py` |
| `preprocessing.py` | `lipidmix/analysis/preprocessing.py` |
| `differential.py` | `lipidmix/analysis/differential.py` |
| `volcano_plot.py` | `lipidmix/plots/volcano.py` |
| `eic_plot.py` | `lipidmix/plots/eic.py` |
| `knowledge_store.py` | `lipidmix/corpus/knowledge_store.py` |
| `paper_ingest.py` | `lipidmix/corpus/paper_ingest.py` |
| `tools_dataset.py` | `lipidmix/tools/dataset.py` |
| `tools_samples.py` | `lipidmix/tools/samples.py` |
| `tools_objective.py` | `lipidmix/tools/objective.py` |
| `tools_reports.py` | `lipidmix/tools/reports.py` |
| `tools_resources.py` | `lipidmix/tools/resources.py` |
| `server.py` | ルート据え置き |
| `check.py` | ルート据え置き |
| `tools/measure_payloads.py` | `archives/tools/measure_payloads.py`（§6 参照） |

### 命名の決定と理由

- 形式パッケージ内は `reader.py` / `tools.py` に短縮する。パッケージ名が接頭辞を担うため
  `arf/arf_reader.py` は冗長になる。
- `core/` の中は原名を維持する。`core/mcp.py` にすると外部の `mcp` パッケージ
  （`from mcp.server.fastmcp import FastMCP`）と読み手が混同する。絶対 import なので
  実害はないが、混同のコストの方が短縮の利得より大きい。
- **すべての `__init__.py` は空にする。** 再エクスポートを書くと
  `lipidmix.eic.tools` → `lipidmix.plots.eic` → `lipidmix.eic.identity_map` の経路で
  循環 import を招く。パッケージは名前空間としてのみ使う。

## 3. import 戦略

束縛名を保存する形に統一し、呼び出し側の本文を触らずに済ませる。

```python
from lipidmix.core import session_state, tool_helpers, mcp_core
from lipidmix.arf import reader as arf_reader
from lipidmix.analysis import preprocessing, differential
from lipidmix.plots import volcano
```

テスト内の `session_state.*`（235 箇所）や `server.*`（295 箇所）はほぼ属性アクセスで、
束縛名が同じなら本文は無変更で済む。`patch.object(server.arf_reader, ...)` も module
オブジェクト自体を差し替えるため、束縛名さえ保てば現行のモンキーパッチが全て生き残る。

### `server.py` をルートに残す理由

`.mcp.json` と `.vscode/mcp.json` が `server.py` を絶対パスで指している。ルートに薄い
ランチャを置いて実体を `lipidmix/server.py` に移すこともできるが、`server.py` は既に
81 行の再エクスポート層であり、二重の薄い層を作る意味がない。**役割は変えず import 先
だけ書き換える。** これにより `.mcp.json` / `.vscode/mcp.json` / `DEPLOY.md` は無変更。

### 移動だけでは静かに壊れる箇所

`__file__` 起点のパス解決が 2 箇所ある。どちらも例外を出さず、**空のディレクトリを
新規作成して黙って動く**ため、テストを通しても気づけない可能性がある。

| 箇所 | 現在 | 修正後 |
|------|------|--------|
| `mcp_core.py:18` | `BASE_DIR = Path(__file__).parent` | `Path(__file__).resolve().parents[2]` |
| `data_config.py:21` | `Path(__file__).resolve().parent / "data"` | `Path(__file__).resolve().parents[2] / "data"` |

`data_config` は `mcp_core` から import される側なので、`mcp_core.BASE_DIR` を参照して
はならない（循環する）。両者が独立に `parents[2]` を計算する重複は許容し、§5 の項目 4 で
両者が一致することをテストで縛る。

`BASE_DIR` は `docs/output_format/`・`knowledge/`・`playbook/`・`analyses/`・`reports/`
の解決元。`lipidmix/core/` を指してしまうと、`lipidmix/core/knowledge/` などが新規作成され、
蓄積済みのノートが見えなくなる。§5 に専用の回帰テストを置く。

## 4. ワークフロー文書

### 配置

```
docs/workflow/
├─ index.md      層構成・読み方・全文書への目次
├─ dataset.md    list_data_files / load_dataset / sample_search
├─ arf.md        ARF 系 9 ツール
├─ arf2.md       arf2_parser / arf2_annotate_identities
├─ pai2.md       pai2_parser / pai2_inspect_peak / verify_peak_annotation
├─ dcl.md        dcl_parser / dcl_find_msms
├─ eic.md        EIC 系 6 ツール
└─ plots.md      save_pca_figure / save_volcano_figure / save_eic_figure と payload 契約の比較
```

分割軸は既存の `docs/output_format/` と新パッケージ構成の両方に一致する。

### 対象範囲: 28 ツール

| 文書 | ツール | 数 |
|------|--------|----|
| `dataset.md` | `list_data_files` `load_dataset` `sample_search` | 3 |
| `arf.md` | `arf_parser` `arf_list_classes` `arf_list_tags` `arf_list_sample_roles` `arf_exclude` `arf_preprocess` `arf_pca_preprocessed` `arf_differential` `arf_plot_volcano` | 9 |
| `arf2.md` | `arf2_parser` `arf2_annotate_identities` | 2 |
| `pai2.md` | `pai2_parser` `pai2_inspect_peak` `verify_peak_annotation` | 3 |
| `dcl.md` | `dcl_parser` `dcl_find_msms` | 2 |
| `eic.md` | `eic_parser` `eic_search_by_mz_range` `eic_search_by_rt_range` `eic_rank_by_max_intensity` `eic_plot_chromatograms` `eic_plot_compounds` | 6 |
| `plots.md` | `save_pca_figure` `save_volcano_figure` `save_eic_figure` | 3 |
| | **合計** | **28** |

**範囲外の 12 ツール**（今回の要望「パーサ及びプロット機能」の外）:
`record_objective` `update_objective` `log_search` `knowledge_coverage` `paper_search`
`ingest_stage` `ingest_review_queue` `ingest_promote` `ingest_reject` `write_report`
`read_report` `list_reports`。後追いで `docs/workflow/corpus.md` を足せる構成にしておく。

28 + 12 = 40 で、登録ツール総数と一致する。

### 節の書式

1 ツール 1 節。全節が同じ 3 要素を持つ。

```markdown
## arf_pca_preprocessed

前提: `arf_preprocess` 実行済み（未実行なら手順 2 で MissingState を返す）
状態変更: `session.last_pca` を更新

1. lipidmix/arf/tools.py  arf_pca_preprocessed()
2. └─ lipidmix/core/tool_helpers.py  _pp_has_preprocessed()
3. └─ lipidmix/core/tool_helpers.py  _pp_build_matrix()
4. └─ lipidmix/arf/reader.py  run_pca()
5. └─ lipidmix/arf/reader.py  get_pca_loading_features()
6. └─ lipidmix/core/tool_helpers.py  _remember_arf_pca_plot()
```

- **行番号は書かない。** リファクタや行挿入で静かに嘘になり、監査コストが釣り合わない。
  パス + 関数名なら grep で必ず当たり、腐らない。
- ファミリごとに mermaid 図を 1 枚（節ごとではない）。ツール間の共有経路が見える粒度にする。
- 呼び出し連鎖は機械可読な書式に固定する。§5 の腐敗防止テストが以下の正規表現で拾う:

  ```
  ^\s*\d+\.\s*(?:└─\s*)*(?P<path>[\w/]+\.py)\s+(?P<func>[\w.]+)\(\)
  ```

  括弧付き `name()` だけが検証対象。属性参照など括弧のない記述は散文として扱い、
  検証しない。

### 既存文書との境界

| 文書 | 責務 |
|------|------|
| `README.md` | リポジトリ全体像とファイル配置。詳細は各文書へリンク |
| `USAGE.md` | MCP ツール 40 個の外形（引数・用途）。LLM / 利用者向け |
| `docs/output_format/*.md` | 出力フィールドの意味。既存のまま維持 |
| `docs/workflow/*.md` | 内部実装の呼び出し連鎖。開発者向け（新設） |

`README.md` の「各パーサが取得できる情報（日本語・網羅）」節は関数の外形説明であり、
`USAGE.md` および新文書と重なる。**節そのものは残し**、ファイルパスを新パスへ更新した
うえで、呼び出し順の詳細は `docs/workflow/` へのリンクに退避する。README だけを見て
いた人の動線を壊さないことを優先する。

`USAGE.md` の「39ツール」と `mcp_core.py` の「6 リソース」コメントは実測と食い違うので、
本作業で 40 / 7 に訂正する。

## 5. 検証

再編は「動くはず」で済ませない。以下を完了条件とする。

1. **回帰**: `python -m unittest discover -s tests -t .` の失敗・エラーがゼロ、skip が 3 件、
   かつ実行件数が **535 + 新規テスト件数** に一致すること。既存 535 件のうち 1 件でも
   消えていたら（＝ import 失敗で収集されなかったら）不合格とする。
2. **登録面**: `test_server_registration.py` が通り、ツール 40 個・リソース 4 +
   テンプレート 3 が維持されること。
3. **起動**: `python server.py` が stdio で起動し、`.mcp.json` の設定のまま MCP クライアント
   から接続できること。
4. **パス解決の回帰テスト（新設 `tests/test_package_layout.py`）**
   - `mcp_core.BASE_DIR` がリポジトリルート（`docs/` と `playbook/` を含むディレクトリ）
     であること
   - `data_config.get_data_dir()` が環境変数なしのとき `BASE_DIR / "data"` を返すこと
   - `KNOWLEDGE_DIR` / `PLAYBOOK_DIR` / `ANALYSES_DIR` が `lipidmix/` の**外**を指すこと
5. **文書の腐敗防止テスト（新設 `tests/test_workflow_docs.py`）**
   - `docs/workflow/*.md` の呼び出し連鎖行を §4 の正規表現で抽出し、各参照について
     ファイルが存在し、その関数/メソッドが AST 上に定義されていることを検証
   - 対象範囲 28 ツールが、それぞれ対応する文書に `## <tool>` 節として存在すること
   - 範囲外 12 ツールが現れていないこと（範囲の線引きを固定する）

項目 5 は行番号を捨てた分の担保である。文書が実装から乖離したらテストが落ちる。

## 6. 付随して片付ける 1 件

ルート直下の `tools/measure_payloads.py` は `agent_core` と `interp_eval_cases` を
import しているが、両者は既に `archives/` へ退避済みで、現状 import できない。
放置すると新設の `lipidmix/tools/` とルート `tools/` が並立して読み手を混乱させる。
`archives/tools/measure_payloads.py` へ移す。

絶対 import を使う限り `lipidmix.tools.*` とルート `tools/` に実害の衝突はないが、
既に壊れているものを残す理由がない。

## 7. やらないこと

- 関数の分割・リネーム・シグネチャ変更。今回は**移動と import 修正のみ**で、
  ロジックには一切手を入れない。差分レビューを成立させるための制約。
- `arf_reader.py`（1014 行）や `tools_arf.py`（973 行）の分割。大きさは事実だが、
  分割は挙動を変えるリスクを伴うため、本作業とは切り離して別途判断する。
- 文献・レポート系 12 ツールのワークフロー文書化（§4 の範囲外）。
- `pyproject.toml` / パッケージインストール化。`server.py` をルートから起動する現行の
  運用（`.mcp.json` が絶対パスで指す）を維持するため、sys.path 依存のままでよい。
