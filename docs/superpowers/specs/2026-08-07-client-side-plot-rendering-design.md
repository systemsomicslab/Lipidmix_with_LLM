---
date: 2026-08-07
status: approved
topic: client-side-plot-rendering
repos:
  - Lipidmix_with_LLM (MCP サーバ)
  - Use-LLLM (webUI クライアント)
---

# 全プロットをクライアント描画に統一し、PNG 生成を明示要求時のみに限定する

## 背景と問題

このシステムの描画方針は「MCP サーバは描画中立な構造化データを返し、各 MCP
クライアントが自前 UI で描画する。PNG はユーザーが明示的に希望したときだけ
`save_*_figure` で書く」である（2026-07-07 payload slim、2026-07-09 PCA 座標
payload、2026-08-04 EIC multi の各設計で段階的に確立）。

しかし現状、volcano プロットと EIC オーバーレイはこの方針から外れている。

### 調査結果

MCP サーバ側（Lipidmix_with_LLM）

| 機能 | 状態 |
| --- | --- |
| `eic_plot_chromatograms` / `eic_plot_compounds` | 適合。構造化 payload のみ。PNG は `save_eic_figure` の明示要求時だけ |
| ARF PCA（`arf_parser` 等） | 適合。`_format_pca_plot_block` が座標点列を同梱し描画を指示 |
| **`arf_differential`（volcano）** | **不適合。** 点列を payload に載せず、`volcano_note` が「`save_volcano_figure` で図示できます」と PNG を唯一の可視化手段として案内する（`tools_arf.py:885-895`） |
| **`save_pca_figure` / `save_volcano_figure`** | **不適合。** docstring に「明示要求時のみ」の制約が無い（`save_eic_figure` にはある）ため LLM が自発的に呼びやすい |
| **`arf_reader.py` CLI** | **不適合。** `--pca` 指定時、`--output-plot` 未指定でも `pca_plot.png` を無条件生成（`arf_reader.py:986`）。`--output-dist-plot` も default 値のため無条件生成 |

webUI 側（Use-LLLM）

| 描画 | 状態 |
| --- | --- |
| PCA | 適合。`pca-plot.js` → Plotly |
| EIC 単一 `lipidmix.eic.v1` | 適合。`eic-plot.js` |
| **EIC 複数物質 `lipidmix.eic.multi.v1`** | **不適合。** `eic-plot.js:44` がスキーマ不一致で拒否するためオーバーレイ図が描画されない |
| **volcano** | **不適合。** レンダラが存在しない |
| **ツール分類 `policy.py:40-44`** | **不適合。** 旧名 `eicaef_*` のままで現行 EIC ツールが `UNKNOWN` 扱い＝毎回承認待ちになり、描画に到達できない |

つまり「PNG になってしまう」原因は2つ。**volcano はサーバ側に構造化経路が存在
しない**、**EIC オーバーレイはクライアント側にレンダラが無い**。

## 設計判断：仮想 PNG は採らない

「matplotlib figure を inline base64 image（`fastmcp.utilities.types.Image`）で
返しディスクに書かない」方式は技術的に可能だが、表示段には採用しない。

1. Plotly の modebar（`app.js:569` の config は modebar を消していない）に既に
   PNG ダウンロードがあり、「見てから欲しければ PNG」は部分的に実現済み。
   足りないのは保存先が `reports/figures/` でない点だけ。
2. ラスタ化で hover（feature 名・sample 名）・zoom・凡例フィルタを失う。EIC の
   ピーク形状確認と volcano の外れ点特定はその操作が本体であり機能後退になる。
3. 画像からは正確な値が読めない。本リポジトリは解釈精度が主目的であり、ローカル
   Qwen は画像を扱えない。結果として画像と数値 JSON の両方を送る二重コストになる。

Plotly を持たないクライアント（Claude Desktop 等）向けの `render_*_figure`
（inline image・ファイル書き込みなし）は将来の追加として本設計と両立するが、
今回のスコープ外。webUI からの `reports/figures/` 保存 UI も今回のスコープ外。

## スコープ

### A. サーバ側：volcano に構造化経路を作る

新モジュール `volcano_plot.py`（`differential.py` の純ロジックと `tools_arf.py`
の間。`eic_plot.py` と同じ位置づけで MCP 非依存）。

```python
build_volcano_plot_payload(last_differential, *, max_points=3000, title=None)
    -> VolcanoPlotPayload
```

`lipidmix.volcano.v1` payload:

```json
{
  "plot_schema": "lipidmix.volcano.v1",
  "plot_type": "scatter",
  "title": "Volcano (24M vs 9w)",
  "comparison": {"group_a": "24M", "group_b": "9w", "n_a": 6, "n_b": 6},
  "axes": {
    "x": {"label": "log2 fold change", "unit": null, "scale": "linear"},
    "y": {"label": "-log10 p", "unit": null, "scale": "linear"}
  },
  "thresholds": {"q": 0.05, "log2fc": 1.0},
  "points": [{"feature": "PC 34:1", "log2fc": 1.82, "neg_log10_p": 3.41, "sig": "up"}],
  "selection": {
    "total": 12483, "plotted": 3000, "significant_total": 218,
    "significant_plotted": 218, "ns_total": 12105, "ns_plotted": 2782,
    "max_points": 3000, "dropped_nonfinite": 160
  },
  "render_hints": {
    "mode": "markers", "color_by": "sig", "show_legend": true,
    "guides": {"x": [-1.0, 1.0], "y": [1.3010]},
    "hover_fields": ["feature", "log2fc", "neg_log10_p", "sig"]
  },
  "caveats": []
}
```

間引き規則（決定的・乱数を使わない）:

1. `log2fc` が `None`/非有限、または `neg_log10_p` が非有限の点は描画不能なので
   除外し、件数を `selection.dropped_nonfinite` と caveat に記録する。
   「除外＝有意でない」と誤読させないため caveat 文で理由を明示する。
2. `up` / `down` は全件保持する。有意点を落とすと図の意味が壊れる。
3. `ns` は `max_points - 有意件数` を枠として等間隔ストライドで抽出する
   （`stride = ceil(ns_total / quota)`）。同じ入力なら常に同じ点集合を返す。
4. 有意件数だけで `max_points` を超える場合は `ns` を 0 件にし、有意点は切らない。
   この場合 caveat に「ns 点は全て省略した」と記録する。

`render_hints.guides.y` は `-log10(q_threshold)` を入れる。`q` はしきい値であり
`p` 軸との厳密な対応はないため、caveat に「横破線は -log10(q しきい値) の目安で
あり、点の y は -log10(p)」と明示する。

新ツール `arf_plot_volcano(max_points=3000, title=None)`（`tools_arf.py`）:

- `session.last_differential` を読む。`save_volcano_figure` と同じソースなので
  差次的解析の再計算はしない。
- 戻り値は `VolcanoPlotPayload`（TypedDict）＝構造化出力スキーマ付き。
- `last_differential` が無い、または `kind != "two_group"` の場合は
  `先に arf_differential（2群比較）を実行してください` を含む `ValueError` を
  投げる。案内文字列を返す流儀（`save_*_figure` 等）は戻り値が `str` の
  ツール向けであり、構造化出力ツールでは型が壊れるため使えない。
  `eic_plot_chromatograms` が `FileNotFoundError` を投げるのと同じ扱い。
- read-only。ファイルは一切書かない。

### B. サーバ側：PNG を明示要求時のみに固定する

- `arf_differential` の `volcano_note` を差し替える。`save_volcano_figure` では
  なく `arf_plot_volcano` を案内し、PNG は明示要求時のみと明記する。
- `save_pca_figure` / `save_volcano_figure` の docstring を `save_eic_figure` と
  同じ書式に統一する。先頭行を「明示的なユーザー要求時だけ〜」とし、「通常の
  対話描画ではこのツールを呼ばず、各 MCP クライアントの UI へ描画を任せる」を
  追記する。
- 要約 payload に volcano 点列は載せない（2026-07-07 の誤読退行対策を維持）。
- `_format_pca_plot_block` の文言は既に方針に適合しているため変更しない。

### C. サーバ側：CLI から無条件 PNG 生成を外す

`arf_reader.py`:

- `--output-plot` の default を `None` にし、未指定なら `plot_pca` を呼ばず
  `[INFO] --output-plot 未指定のため PCA プロット PNG は生成しません` を出す。
- `--output-dist-plot` の default を `None` にし、未指定なら
  `plot_peak_height_distribution` を呼ばず同様の INFO を出す。
- `plot_pca` / `plot_pca_scores_by_sample` / `plot_peak_height_distribution` の
  シグネチャ上の default 値（`"pca_plot.png"` 等）は残す。関数を直接呼ぶ場合に
  出力先を省略できる利便性は保つ。変えるのは CLI の既定挙動のみ。

### D. webUI：EIC オーバーレイを描画可能にする

`eic-plot.js`:

- `normalizePlot` が `lipidmix.eic.v1` と `lipidmix.eic.multi.v1` の両方を受理
  する。返す正規化結果に `schema` を持たせる。
- `normalizeSeries` は両スキーマ共通の `x`/`y`/`label` 検証を維持し、multi 固有
  フィールド（`spot_id`/`name`/`ontology`/`adduct`/`mz`/`rt`/`annotation`）は
  スプレッドで保持する（現行実装が `...value` しているためそのまま通る）。
- `traces()` は `schema` で customdata と hovertemplate を切り替える。
  - single: `file_id` / `sample_name` / `class_id` / `peak_left` / `peak_top` /
    `peak_right`（現行のまま）
  - multi: `spot_id` / `name` / `ontology` / `adduct` / `mz` / `rt` /
    `peak_top` / `max_intensity`
- multi 用に `annotations(plot)` を追加し、`render_hints.show_annotations` が真
  のとき各 series の `annotation`（`text`/`x`/`y`）を Plotly layout annotation
  配列として返す。偽または欠落時は空配列。

`app.js` の `appendEicPlot` はレイアウトに `annotations` を渡す。系列数が多い
multi では凡例が縦に伸びるため、`plot.series.length > 8` のとき legend を
`orientation: "v"` かつ右外側配置に切り替える。

### E. webUI：volcano レンダラを新設する

`static/volcano-plot.js`（UMD、`GeneralVolcanoPlot`）:

- `findPlot(input)`: `pca-plot.js` / `eic-plot.js` と同じ
  `parseJsonCandidates` → 再帰探索で `lipidmix.volcano.v1` を見つける。
  生文字列 JSON と ```json フェンス内 JSON の両方に対応する。
- `traces(plot)`: `sig` ごとに3トレース。`up` = `#c0392b`、`down` = `#2471a3`、
  `ns` = `#95a5a6`（`save_volcano_figure` の matplotlib 配色と一致させる）。
  凡例名に件数を入れる（`up (218)`）。`ns` を最初に積んで有意点を上に描く。
  hover に feature 名・log2fc・-log10 p を出す。
- `shapes(plot)`: `render_hints.guides` からしきい値の破線 shape 配列を返す。
  `guides` が欠落していれば空配列。

`app.js`:

- `appendVolcanoPlot(bubble, item)` を PCA/EIC と同型で追加し、`renderMessage`
  から呼ぶ。カード見出しの note は
  `<plotted> / <total> points · 有意 <n> 件 · hover / zoom / legend filter`。
  `selection.plotted < selection.total` のときは間引きが起きた旨を note に添え、
  画面が全点でないことをユーザーに隠さない。

`index.html` に `<script src="./static/volcano-plot.js"></script>` を
`eic-plot.js` の次へ追加する。

### F. webUI：ツール名を実サーバに同期する

`policy.py` の `READ_ONLY_TOOLS` を実サーバのツール名に合わせる。

- 旧名を現行名へ: `eicaef_parser` → `eic_parser`、
  `eicaef_plot_chromatograms` → `eic_plot_chromatograms`、
  `eicaef_top_peak_tops` → `eic_rank_by_max_intensity`
- 追加: `eic_plot_compounds`、`arf_plot_volcano`、`dcl_parser`、
  `dcl_find_msms`、`sample_search`、`pai2_inspect_peak`
- 削除（サーバに存在しない）: `arf_re_pca`、`pai2_get_top_metabolites`、
  `pai2_inspect_metabolite_details`
- `LOCAL_WRITE_TOOLS` から `pai2_update_analysis_filter` を削除（存在しない）

`mcp_state_policy.py`:

- `arf_re_pca` の規則を削除する。
- `eicaef_plot_chromatograms` → `eic_plot_chromatograms`（`provides=("eic_plot",)`,
  `replay_safe=True`）。
- `eic_plot_compounds` を追加（`provides=("eic_plot",)`, `replay_safe=True`）。
- `arf_differential` に `provides=("differential_result",)` を追加する。ただし
  `replay_safe` は付けない。群指定引数に依存し、無引数での再実行が同じ状態を
  再現しないため。
- `arf_plot_volcano` と `save_volcano_figure` に
  `requires=("differential_result",)` を追加する。
- `indicates_missing_state` のマーカー文言を現行サーバの案内文に合わせる。
  `先に eicaef_plot_chromatograms` → `先に eic_plot_chromatograms`、
  `先に arf_differential` を追加。

`arf_differential` が `replay_safe` でない結果として、`arf_plot_volcano` の
状態復元は `StateRestoreBlocked` になる。これは正しい挙動で、ユーザーに
`arf_differential` の再実行を促す。

### G. ドキュメント

- `USAGE.md` のツール表に `arf_plot_volcano` を追加。`save_pca_figure` /
  `save_volcano_figure` の行を「明示要求時のみ」に書き換える。
- `README.md` にモジュール `volcano_plot.py` とツール `arf_plot_volcano` を追加。
- `docs/output_format/arf.md` の volcano 節に `lipidmix.volcano.v1` の
  フィールド定義と間引きの注意（`selection.plotted < total` なら画面は全点でない）
  を追記。
- `docs/output_format/pai2.md:73` の `img_bytes`（既に存在しない PAI2 PCA の
  記述）を削除。
- `docs/HISTRY.md` に開発ログを追記。

## テスト

サーバ側（unittest）。新規 `tests/test_volcano_plot.py`:

1. payload の必須キーと `plot_schema` / `plot_type`
2. `up`/`down` が全件保持される（`max_points` を下回る枠でも切られない）
3. `ns` の等間隔間引きが決定的（同一入力で2回呼んで同一結果）
4. 非有限値の点が除外され `dropped_nonfinite` と caveat に反映される
5. 有意件数 > `max_points` のとき `ns_plotted == 0` かつ有意点は無傷、caveat あり
6. `comparison` / `thresholds` / `guides` が `last_differential` の値を反映する
7. `arf_plot_volcano` が `last_differential` 未設定時と `kind != "two_group"` の
   ときに `先に arf_differential` を含む `ValueError` を投げる
8. `arf_plot_volcano` 実行後に `*.png` が1枚も作られない（`rglob("*.png")` が空）

既存テストへの追加:

- `tests/test_server_registration.py`: `arf_plot_volcano` の登録と構造化出力
  スキーマに `plot_schema` があること
- `tests/test_differential_tools.py`: `volcano_note` が `arf_plot_volcano` を
  案内すること

クライアント側（node）。新規 `tests/test_general_volcano_plot.cjs`:

1. 生 JSON とフェンス JSON の両方から `findPlot` が payload を見つける
2. `traces` が sig ごとに3トレースを返し、`ns` が先頭（下層）にある
3. 空の sig グループはトレースを作らない
4. `shapes` が `guides` から破線を作り、`guides` 欠落時は空配列
5. スキーマ不一致の payload では `findPlot` が `null`

既存 `tests/test_general_eic_plot.cjs` に multi ケースを追加:

6. `lipidmix.eic.multi.v1` が `findPlot` を通り、`traces` の customdata に
   `spot_id` / `ontology` / `mz` / `rt` が入る
7. `annotations` が `show_annotations: true` で series 数ぶん返り、`false` で空

## 最終検証

`C:\Users\yuu18\Use-LLLM` の webUI を実データで起動し、以下を確認する。

1. PCA が Plotly で描画される
2. EIC 単一スポットが Plotly で描画される
3. EIC 複数物質オーバーレイが Plotly で描画される（本設計 D の対象）
4. volcano が Plotly で描画される（本設計 A・E の対象）
5. 上記 1〜4 の過程で `reports/figures/` に PNG が1枚も作られない
6. EIC 系ツールが承認ダイアログで止まらない（本設計 F の対象）

## 非対象

- `differential.py` の純ロジック（`volcano_data` / `summarize_two_group`）は不変
- `save_pca_figure` / `save_volcano_figure` / `save_eic_figure` の PNG 生成ロジック
  そのものは不変（docstring と呼ばれ方だけを変える）
- webUI からの `reports/figures/` 保存ボタン
- inline image を返す `render_*_figure` 系ツール
- `arf_differential` の ANOVA 分岐
