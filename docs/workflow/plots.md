# ワークフロー: 図の保存と payload 契約

通常の対話では PNG を作らない。ユーザーが明示的に保存を求めたときだけ `save_*_figure`
を呼ぶ。3 ツールは同じ骨格を持つ ——「`session` に記録済みの payload を取り出し、
ファイル名の slug を作り、レポート先ディレクトリを解決し、matplotlib で描いて保存する」。

## payload 契約の比較

| payload | 生成元 | 保存 | 特徴 |
|---|---|---|---|
| PCA スコア（`session.arf.last_pca`） | `arf_parser` / `arf_pca_preprocessed` | `save_pca_figure` | 散布図。`_pca_scatter_arrays` で座標配列に展開 |
| `lipidmix.volcano.v1` | `arf_plot_volcano(output="payload")` | `save_volcano_figure` | `up`/`down` は全点保持、`ns` のみ間引く |
| `lipidmix.eic.v1` / `.multi.v1` | `eic_plot_chromatograms` / `eic_plot_compounds(output="payload")` | `save_eic_figure` | 線グラフ。描画は `plots/eic.py` に委譲 |

`arf_plot_volcano` と `eic_plot_compounds` の**既定は payload ではなく画像**
（`output="image"`）。座標点列を LLM の文脈へ流すと実測で数万トークンかかるのに対し、
サーバ側で描いた PNG は画素課金で 300〜1,000 画像トークンに収まる。Plotly で対話的に
描くクライアント（Use-LLLM）は起動 env に `LIPIDMIX_PLOT_OUTPUT=payload` を置くか、
呼び出しごとに `output="payload"` を渡す。判定は `plots/render.py resolve_plot_output()`。

描画はいずれも `plots/` 側に置いてある（`render_eic_plot()` / `render_volcano_plot()`）。
画像返しと PNG 保存が同じ関数を通るので、画面の図とレポートに貼る図がずれない。
`save_pca_figure` だけは `tools/reports.py` 内で直接 matplotlib を呼ぶ。

保存先は `_resolve_report_dir()` が解決する。解析フォルダ配下の `reports/` を優先し、
書き込めなければ `LIPIDMIX_REPORTS_DIR` またはリポジトリ内の `reports/` に退避する
（候補列挙と可否判定が分かれているのは、退避したこと自体を返り値で開示するため）。

## save_pca_figure

前提: `arf_parser` / `arf_pca_preprocessed` / `load_dataset` のいずれか実行済み
（未実行なら手順 2 で `MissingState`）
状態変更: PNG ファイルを書き出す。

生行列 PCA と前処理後 PCA は同じスロット（`session.arf.last_pca`）を使うので、
保存されるのは**直近に実行したほう**の図になる。

1. lipidmix/tools/reports.py  save_pca_figure()
2. └─ lipidmix/core/mcp_errors.py  missing_state()
3. └─ lipidmix/corpus/knowledge_store.py  make_slug()
4. └─ lipidmix/core/mcp_core.py  _resolve_report_dir()
5. │  └─ lipidmix/core/mcp_core.py  _report_dir_candidates()
6. │  └─ lipidmix/core/mcp_core.py  _first_writable_dir()
7. └─ lipidmix/core/tool_helpers.py  _pca_scatter_arrays()

## save_volcano_figure

前提: `arf_differential` 実行済み（未実行なら手順 2 で `MissingState`）
状態変更: PNG ファイルを書き出す。

`arf_plot_volcano` を経由する必要はない。`session.arf.last_differential` に保持された
全量 volcano を直接描く（`arf_plot_volcano` の画像モードと同じ描画関数）。

1. lipidmix/tools/reports.py  save_volcano_figure()
2. └─ lipidmix/core/mcp_errors.py  missing_state()
3. └─ lipidmix/corpus/knowledge_store.py  make_slug()
4. └─ lipidmix/core/mcp_core.py  _resolve_report_dir()
5. │  └─ lipidmix/core/mcp_core.py  _report_dir_candidates()
6. │  └─ lipidmix/core/mcp_core.py  _first_writable_dir()
7. └─ lipidmix/plots/volcano.py  render_volcano_plot()

## save_eic_figure

前提: `eic_plot_chromatograms` または `eic_plot_compounds` 実行済み
（未実行なら手順 2 で `MissingState`）
状態変更: PNG ファイルを書き出す。

手順 7 は payload の `plot_schema` で描画関数を選ぶ。未知のスキーマは `ValueError`。

1. lipidmix/tools/reports.py  save_eic_figure()
2. └─ lipidmix/core/mcp_errors.py  missing_state()
3. └─ lipidmix/corpus/knowledge_store.py  make_slug()
4. └─ lipidmix/core/mcp_core.py  _resolve_report_dir()
5. │  └─ lipidmix/core/mcp_core.py  _report_dir_candidates()
6. │  └─ lipidmix/core/mcp_core.py  _first_writable_dir()
7. └─ lipidmix/plots/eic.py  render_eic_plot()
8.    ├─ [lipidmix.eic.v1] lipidmix/plots/eic.py  _render_single_spot()
9.    └─ [lipidmix.eic.multi.v1] lipidmix/plots/eic.py  _render_multi_compound()
