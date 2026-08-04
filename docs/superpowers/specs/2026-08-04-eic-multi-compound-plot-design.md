# 複数物質 EIC 重ね描きプロット設計

日付: 2026-08-04
対象: `eic_plot.py` / `eic_aef_reader.py` / `tools_eic.py` / `tools_reports.py`

## 1. 背景と目的

現行の `eic_plot_chromatograms`（`tools_eic.py`）は **1 スポット（1 物質）× 複数サンプル**の
クロマトグラムを `lipidmix.eic.v1` ペイロードとして返す。標準品ミックスの品質確認では、
**複数の脂質分子種を 1 枚のクロマトグラム上に重ねて**、溶出順・分離・強度バランスを一望
したい（参照: nano-flow 標準ミックスの多物質オーバーレイ図）。

本設計は「複数物質 × 1 サンプル」を同一グラフに重ね描きするための新ツールを追加する。
複数サンプルを比較したい場合は、サンプルごとに別グラフ（別呼び出し）とする。

## 2. スコープ

含む:

- 脂質名部分一致 / オントロジー（脂質クラス）による物質選択
- ARF2 の同定情報と EIC スポットの対応付け、および rt/mz による検証
- 1 サンプル分の複数物質オーバーレイ用ペイロード `lipidmix.eic.multi.v1`
- ピーク頂点への「名前 / RT」注記情報のペイロード同梱
- 複数スポットを 1 回のファイルオープンで読むバッチリーダー
- 新スキーマに対応した matplotlib 描画と PNG 保存

含まない:

- 既存 `eic_plot_chromatograms` と `lipidmix.eic.v1` の変更（無変更を維持する）
- 1 呼び出しでの複数サンプル・複数パネル返却
- `spot_id` 直接指定による物質選択（名前 / オントロジーのみ）
- スキーマ v1 / multi.v1 の統合リファクタリング

## 3. 公開インターフェース

`tools_eic.py` に新 MCP ツールを追加する。

```python
@mcp.tool()
def eic_plot_compounds(
    file_id: int,
    names: list[str] | None = None,
    ontologies: list[str] | None = None,
    file_path: str | None = None,
    arf2_path: str | None = None,
    normalize: str = "none",
    top_n: int = 24,
    title: str | None = None,
) -> EICMultiPlotPayload:
```

- `file_id`: 必須。1 呼び出し = 1 サンプル。
- `names`: ARF2 `Name` への大文字小文字を無視した部分一致クエリのリスト。
- `ontologies`: ARF2 `Ontology` への大文字小文字を無視した完全一致のリスト。
- `names` と `ontologies` の結果は和集合。両方とも未指定（`None` または空リスト）なら
  `ValueError` を送出する。
- `file_path` / `arf2_path`: 省略時は既存の `resolve_eicaef_file_path` /
  `resolve_arf2_file_path` により最新バッチを自動解決する。
- `normalize`: 既存と同じ `"none"` / `"per_trace_max"`。他の値は `ValueError`。
- `top_n`: 描画するトレース数の上限。既定 24。1 未満は `ValueError`。
- 戻り値は構造化 JSON のみ。画像は書かない。PNG が必要な場合のみ既存 `save_eic_figure`
  をユーザーの明示要求時に呼ぶ（既存ツールと同じ運用）。
- 生成したペイロードは `session_state.session.last_eic_plot` に格納する。

## 4. 物質の解決とガード（新モジュール `eic_identity_map.py`）

責務: ARF2 の同定情報から「描画対象スポットの候補リスト」を作り、EIC スポットとの
対応付けを検証すること。EIC のバイナリ読み出しにも matplotlib にも依存しない。

主要関数:

```python
def select_identity_candidates(
    arf2_path: str | Path,
    *,
    names: list[str] | None,
    ontologies: list[str] | None,
    max_candidates: int = 300,
) -> tuple[list[IdentityCandidate], list[str]]:
    """クエリに一致する ARF2 スポット候補と caveat 文字列を返す。"""


def verify_spot_match(
    candidate: IdentityCandidate,
    spot: dict,
    *,
    rt_tolerance: float = 0.02,
    mz_tolerance: float = 0.01,
) -> str | None:
    """一致すれば None、外れていれば除外理由の文字列を返す。"""
```

`IdentityCandidate` は `TypedDict` で `spot_id`（= ARF2 `AlignmentID`）、`name`、
`ontology`、`adduct`、`rt`、`mz`、`height_average` を持つ。

処理手順:

1. ARF2 を読み、`Name` 部分一致（大小無視）または `Ontology` 完全一致（大小無視）で
   候補を集める。同一 `AlignmentID` が両方のクエリに当たった場合は 1 件に重複排除する。
2. 候補が `max_candidates`（既定 300）を超える場合、ARF2 `HeightAverage` 降順で
   `max_candidates` 件に予備選抜する。EIC を無駄に読まないための足切りであり、
   打ち切った旨を caveat に記録する。
3. `spot_id = AlignmentID` として EIC スポットを読み、**`|Δrt| ≤ 0.02 min` かつ
   `|Δmz| ≤ 0.01 Da`** を検証する。外れた候補は描画せず `selection.dropped[]` に
   理由（`"rt_mismatch"` / `"mz_mismatch"`）を付けて記録し、`caveats` にも 1 行出す。
   `spot_id` が EIC のスポット範囲外だった候補も同様に `"spot_out_of_range"` として落とす。
   指定 `file_id` のトレースがそのスポットに存在しない候補は `"file_id_absent"` として落とす。
4. 検証を通った候補を、**指定サンプルにおける実測 `max_intensity` の降順**で `top_n` 件に
   絞る。打ち切られた候補も `"below_top_n"` として `dropped[]` に記録する（黙って捨てない）。
5. 最終シリーズは EIC スポットの `rt` 昇順に並べ替える（凡例と注記が左から順に読める）。

対応付けの根拠と限界は `docs/output_format/eic.md` の記述（spot_id は今回のデータでは
ARF/ARF2 の ID 順と対応）に依存するため、手順 3 の検証を必須の安全弁とする。検証で
1 件も残らなかった場合は、候補数と除外理由を含む `ValueError` を送出する。

## 5. ペイロード `lipidmix.eic.multi.v1`

`eic_plot.py` に `EICMultiPlotPayload` を追加する。既存 `EICPlotPayload` は変更しない。

```
plot_schema : "lipidmix.eic.multi.v1"
plot_type   : "line"
title       : str
source      : {file, file_name, arf2_file}
axes        : {x: {label, unit, scale}, y: {label, unit, scale}}
sample      : {file_id, sample_name, class_id}
normalization : "none" | "per_trace_max"
series[]    : {
                id, label, spot_id, name, ontology, adduct, mz, rt,
                x[], y[], peak_left, peak_top, peak_right,
                max_intensity, mean_intensity, point_count,
                annotation: {text, x, y}
              }
selection   : {queries[], ontologies[], candidates, plotted, top_n,
               dropped[{spot_id, name, reason}]}
render_hints: {mode, connect_points, show_legend, show_annotations, hover_fields[]}
caveats[]   : str
```

- `sample` が単一 `spot` を置き換える。多物質では「1 スポット」は意味を持たないため。
- `series[].label` は脂質名（ARF2 `Name`）。名前が `"Unknown"` または空の場合は
  `f"spot {spot_id} (m/z {mz:.4f})"` にフォールバックする。
- `annotation.text` は `f"{label} / {peak_top:.3f}"`（参照図と同形式）。
- `annotation.x` はピーク頂点に最も近いデータ点の x 座標、`annotation.y` は**正規化後**の
  その点の y 値。描画側は座標変換なしに文字を置けばよい。
- 軸ラベルは既存 `_axis_definition` を共用（`main_type=0` なら `RT [min]`）。
  `normalize="per_trace_max"` のとき y 軸は `Relative intensity`。
- 選択された複数スポットの `main_type` が混在する場合は、最頻値を採用して caveat を出す。

## 6. 読み込み層（`eic_aef_reader.py`）

複数スポットを 1 回のファイルオープンで読むバッチ版を追加する。

```python
def read_eic_spots_css1(
    file_path,
    spot_ids,
    file_ids=None,
    *,
    max_traces=12,
    max_total_points=200_000,
) -> list[dict]:
```

- CSS1 のポインタ表を使い、要求された `spot_id` の位置へ順に seek する。
- 選択外サンプルの点列は既存単数版と同様 `seek` で読み飛ばす。
- 戻り値は既存 `read_eic_spot_css1` と同じ形のスポット辞書のリスト。要求順ではなく
  `spot_id` 昇順で返す（シーク順＝ファイル順）。呼び出し側で並べ替える。
- `max_traces` は「1 スポットあたりの選択サンプル数」の上限であり、既存単数版と同じ意味。
  多物質ツールは `file_ids=[file_id]` の 1 本しか要求しないため実質制約にならない。
- `max_total_points` はバッチ全体の合計点数に対する上限とし、超過時は
  読み込んだ点数と `top_n` を減らす旨のメッセージを含む `ValueError` を送出する。
- 範囲外 `spot_id` は例外を投げず、返却リストから単に欠落させる。呼び出し側が
  `"spot_out_of_range"` として扱う。
- 既存 `read_eic_spot_css1` はこのバッチ版に委譲し、外部から見た振る舞い（範囲外
  `spot_id` で `ValueError`、`file_ids` 未指定かつ 12 トレース超で `ValueError`）を維持する。

## 7. 描画・保存

- `eic_plot.py` の `render_eic_plot(payload, title=None)` を `plot_schema` で分岐させる。
  `lipidmix.eic.v1` は現行動作を維持し、`lipidmix.eic.multi.v1` は新しい描画関数
  `_render_multi_compound(payload, title)` に委譲する。未知のスキーマは `ValueError`。
- multi 描画は物質ごとに色を変え、`render_hints.show_annotations` が真なら各系列の
  `annotation` を `ax.annotate` で頂点付近に置く。単一系列時の `axvspan` は multi では
  行わない（重なって読めなくなるため）。
- `tools_reports.py` の `save_eic_figure` は `session.last_eic_plot` を読む現行実装のまま
  両スキーマに対応する（`render_eic_plot` 側の分岐で吸収されるため変更不要）。
  保存ファイル名の規約も現行どおり `figures/<slug>_eic.png`。

## 8. エラー処理

| 条件 | 挙動 |
|---|---|
| `names` と `ontologies` の両方が未指定 | `ValueError`（どちらかを指定するよう案内） |
| `normalize` が既定値以外 | `ValueError`（既存と同じメッセージ方針） |
| `top_n < 1` | `ValueError` |
| `.EIC.aef` が見つからない | `FileNotFoundError`（既存ツールと同じ文言方針） |
| `.arf2` が見つからない | `FileNotFoundError` |
| クエリに一致する ARF2 候補が 0 件 | `ValueError`（クエリ文字列を含めて再指定を促す） |
| rt/mz 検証を通る候補が 0 件 | `ValueError`（候補数と除外理由の内訳を含める） |
| 候補が `max_candidates` 超 | 予備選抜のうえ caveat |
| 検証通過が `top_n` 超 | 強度上位で打ち切り、`dropped[]` と caveat に明記 |

## 9. テスト（`tests/test_eic_plot.py` に追加）

既存の `_write_css1` ヘルパーを再利用し、合成 ARF2 候補（`eic_identity_map` の読み取り関数を
モンキーパッチまたは合成ファイルで差し替え）と組み合わせて検証する。

1. 名前部分一致とオントロジー完全一致が期待どおりのスポット集合を選ぶ（和集合・重複排除を含む）
2. rt が許容差を超えるスポットが除外され、`selection.dropped[]` と `caveats` に理由が載る
3. 検証通過が `top_n` を超えるとき、実測 `max_intensity` 上位が残り、残りが
   `reason="below_top_n"` で `dropped[]` に載る
4. `series` が rt 昇順に並び、`annotation.x/y` がピーク頂点のデータ点と一致する
   （`normalize="per_trace_max"` のとき `annotation.y` が正規化後の値であることも確認）
5. `eic_plot_compounds` 単体では PNG が書かれない（`rglob("*.png")` が空）
6. multi ペイロードを `session.last_eic_plot` に置いた状態で `save_eic_figure` が PNG を保存できる
7. `read_eic_spots_css1` が複数スポットを 1 回のオープンで返し、範囲外 `spot_id` を欠落させる
8. 既存 `read_eic_spot_css1` の振る舞い（範囲外で `ValueError`、12 トレース超で `ValueError`）が
   バッチ版への委譲後も保たれる
9. FastMCP が `eic_plot_compounds` の `outputSchema` を公開する

## 10. ドキュメント更新

`docs/output_format/eic.md` に「8.6 `eic_plot_compounds()` の描画契約」を追加する。
記載事項: 1 呼び出し 1 サンプルであること、`lipidmix.eic.multi.v1` の主要フィールド、
`sample` が `spot` を置き換えること、`selection.dropped[]` を読めば除外された物質と理由が
分かること、rt/mz 検証により ID 対応の崩れたバッチでは物質が落ちうること、通常描画では
ファイルを作らず PNG は `save_eic_figure` の明示要求時のみであること。
