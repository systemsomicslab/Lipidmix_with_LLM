# PCA 座標を LLM に渡して自描画させる（ARF スコアプロット）設計

日付: 2026-07-09
対象: `arf_parser` / `arf_re_pca` / `arf_pca_preprocessed`（`_format_pca_plot_block`）

## 背景・問題

以前は PCA のスコア座標を LLM に渡し、LLM が Claude Desktop 上でその座標から
散布図（群ごとに色分けした点）を自身で描画していた。現在はこの座標データが
ツール戻り値から欠落しており、LLM は自描画できない。

原因は `tool_helpers._format_pca_plot_block`（`tool_helpers.py:143`）が座標の
点列を payload から意図的に外していること。コメント（`tool_helpers.py:154`）は
「8000字切り詰めで末尾の loadings が消えるため要約のみ返す」と説明する。

**重要な発見**: この 8000字 truncate は `archives/local_llm_agent/agent_core.py:24`
の `_truncate(text, limit=8000)`、すなわち **archives へ退避済みのローカルLLM
エージェントループ固有の制約**である。実運用パスである MCP サーバ
（`server.py` / `mcp_core.py`）は戻り値を truncate しない（grep で確認済み）。
Claude Desktop は MCP 経由でツール戻り値を全量受け取るため、座標を payload に
戻しても loadings が消える問題は起きない。

さらに ARF ツールの intro テキスト（`tools_arf.py:312-316` 他）は今も
「以下のJSONデータを用いて散布図を描画してください／group で色分け」と
指示しているが、後続に JSON が無く**指示だけが宙に浮いている**。

## スコープ

- **対象**: ARF 系サンプルレベル PCA（`arf_parser` / `arf_re_pca` /
  `arf_pca_preprocessed`）。点＝サンプル（数十件）で群色分けが有効な、ユーザーが
  想起する「色やドットを分けて表示」ユースケース。
- **対象外（現状維持）**: `pai2_parser` / `pai2_update_analysis_filter`。pai2 の
  PCA は点＝ピーク（数百〜数千件）で座標を全量渡すのは非現実的なため、PNG 生成
  （`perform_pca_summary` の matplotlib 図 → `mcp.Image` インライン返却）を
  そのまま維持する。今回の変更で pai2 のコードは触らない。
- ARF の PNG は従来どおり `save_pca_figure` によるユーザー要求時のみ生成
  （ARF は元から既定で PNG を作らない）。本設計で挙動は変えない。

## 変更

### `tool_helpers._format_pca_plot_block`

現在の「要約のみ（点列非同梱）」を廃し、座標点列を JSON ブロックとして payload に
含める。渡された `pca_result` / `sample_names` / `groups` から、
`_remember_arf_pca_plot`（`tool_helpers.py:118`）が `last_pca_plot` に積むのと
同じ点構造を組み立てる:

```json
{"x_label":"PC1 (42.10%)","y_label":"PC2 (18.30%)",
 "points":[{"pc1":1.23,"pc2":-0.45,"sample":"GF_1","group":"gf"}, ...]}
```

- 各点: `pc1`, `pc2`（float, 4桁丸め）, `sample`（サンプル名）, `group`（`groups`
  に値がある場合のみ付与）。キー名は intro テキスト（`tools_arf.py:314` が
  `sample` を参照）に合わせる。なお `session.last_pca_plot` は従来どおり
  `x`/`y`/`label` 構造を維持（`save_pca_figure` 用、本変更で不変）。
- 出力は既存の intro テキスト（呼び出し側が指定）＋ タイトル／PC 説明分散比／
  群別サンプル数の要約行に続けて、```json フェンス付きで点列を出す。
- ヘルパの署名（引数）は不変。`_remember_arf_pca_plot` も従来どおり呼ばれ
  `last_pca_plot` を維持（`save_pca_figure` の PNG 経路は不変）。

### 出力順序（座標ブロックを末尾へ）

現状の各 ARF ツールは `要約 → plot_instruction_text（座標）→ loadings` の順で
連結している（例: `tools_arf.py:347-348`）。座標 JSON を復活させると座標が
loadings の前に来て、万一の下流截断で再び loadings（解釈の金脈）を失う旧バグ
構造に戻る。実運用 MCP パスに truncate は無いが、防御的に **座標ブロックを末尾
（loadings の後）** へ置く。各ツールで `{plot_instruction_text}{loadings_summary_text}`
の連結順を `{loadings_summary_text}{plot_instruction_text}` に入れ替える
（`arf_parser` / `arf_re_pca` / `arf_pca_preprocessed` の 3 箇所）。これにより
仮に末尾が切れても失うのは散布図の点（最も非クリティカル）に限られる。

### 整合性

`tools_arf.py:312-316` の宙に浮いた「JSONを使って描画」指示が、本変更で実データを
伴うようになり整合する。`arf_re_pca` / `arf_pca_preprocessed` の intro も同一
ヘルパ経由のため同様に座標同梱となる。

## テスト

- `_format_pca_plot_block` の単体テスト: 返り値に `points` を含む JSON フェンスが
  あり、群あり点に `group` キー、群なし点に `group` キー無しであること。
- 既存の ARF ツールテスト（`tests/test_server_class_filter.py` 等）が壊れない
  こと。座標同梱により payload が長くなるが、実運用 MCP パスに truncate は無い
  ため loadings 節の消失は起きない。加えて出力順を `要約 → loadings → 座標 JSON`
  とし、座標を末尾に置くことで万一の下流截断でも解釈の金脈（loadings）を守る。

## 非目標

- pai2 経路の変更。
- `perform_pca_summary` / `run_pca` / `save_pca_figure` の変更。
- 座標フォーマットの汎用化（markdown 表 / CSV など）。JSON フェンス一択。
