# サンプル名の因子トークンによる選択・群分け 設計

- 日付: 2026-08-04
- 対象: `sample_factors.py`（新規・純関数）、`tools_samples.py`（新規・MCPツール）、
  `msdial_classes.py`、`tools_arf.py`、`server.py`、`tests/`

## 背景 / 問題

MS-DIAL の Class ID は「ユーザーが MS-DIAL 上で入力した1文字列」でしかなく、
実験デザインの全因子を含むとは限らない。
`C:\Users\yuu18\datasets\2_lipidome_lcms\NEG`（`Dataset_2026_05_15_10_12_46.mddata`）が典型で、
Class ID は処置条件だけの4値（`control` / `LPS` / `G` / `ILG`）である。
一方サンプル名は因子トークン列になっている:

```
20220902_RAW_ILG_6h_2_NEG
 └日付   └細胞 └処置 └時点 └複製 └極性
```

**時点（`0h` / `15min` / `1h` / `6h` / `24h`）・複製番号・測定日はサンプル名にしか存在しない。**
このため現状のツール群では:

- `arf_differential(group_a="ILG", group_b="control")` は全時点をプールするしかなく、
  「ILG 6h vs control 6h」という時点を揃えた2群比較が**原理的に書けない**。
- `arf_parser(class_ids=[...])` は Class ID しか見ないため、時点によるサブセット抽出ができない。
- PCA の色分け（`group_levels`）も Class ID トークンに限られ、処置 × 時点の直積で
  見ることができない。
- `pai2` / `dcl` / EIC は「どのファイル／どの FileID がその条件のサンプルか」を
  引く手段を持たず、ファイル名を人が目視で選ぶしかない。

既存資産としてトークン部分一致の仕組み自体はある
（`msdial_classes.expand_class_specs` / `assign_sample_groups` /
`tools_arf._pool_group_labels`、[2026-06-25-flexible-class-selection-design.md](2026-06-25-flexible-class-selection-design.md)）
が、いずれも **Class ID 文字列だけ** を入力にしている。

## 目標

1. **統合トークン空間**: 各サンプルのトークン集合を
   `tokens(サンプル名) ∪ tokens(Class ID)` とし、既存の `class_ids` / `group_a` /
   `group_b` / `group_levels` がそのままサンプル名トークンにも効くようにする。
2. **多因子指定**: `ILG_6h` のような複数トークン AND 指定を、フィルタ・群分け・
   2群比較のすべてで受ける。
3. **因子軸の直積による群分け**: 処置 × 時点のような多因子色分けを、群を手書き
   列挙せずに出せるようにする。
4. **ARF 以外への提供**: `pai2` / `dcl` / EIC が使える形（実ファイルパスと FileID）で
   条件検索できるツールを1本置く。

**非目標**: 因子名（「処置」「時点」）の自動推定。位置インデックスによる因子指定
（本データの `G_uralensis` が2トークンに割れて位置がズレるため、値トークン指定に統一する）。
`arf_preprocess` の変更（行列を組むだけで群概念を持たない）。

## 設計

### 1. `sample_factors.py` — 純関数レイヤ（新規）

`msdial_classes.py` の Class ID 専用ロジックを、サンプル単位のファセットへ一般化した
leaf モジュール。依存は `msdial_tags.normalize_sample_name` と
`preprocessing.detect_sample_roles` のみ（`tools_*` / `session_state` / `server` は import しない）。

```python
@dataclass(frozen=True)
class SampleFacet:
    name: str                 # ARF FileName（正準のサンプル識別子）
    file_id: int | None       # MS-DIAL AnalysisFileId（EIC の file_ids に直結）
    class_id: str | None
    role: str                 # sample / qc / blank
    tokens: frozenset[str]    # casefold 済み統合トークン
```

#### トークン化

`normalize_sample_name(value, strip_processing_timestamp=True)` で既知拡張子と
末尾12桁の処理タイムスタンプ（`_202605151012`）を落としてから `_` 分割・casefold・
空要素除去。Class ID があれば同様に分割して合流する。

```
20220902_RAW_G_uralensis_6h_2_NEG_202605151012 + class_id="G"
  → {20220902, raw, g, uralensis, 6h, 2, neg}
```

日付 `20220902` と複製番号 `2` も**トークンとして残す**（バッチ効果や複製での
色分け・絞り込みに使えるため）。`raw` / `neg` のような全サンプル共通トークンも
残るが、語彙一覧が出現数を出すので識別できる。

#### 公開関数

| 関数 | 役割 |
|---|---|
| `build_sample_facets(sample_names, class_index)` | ファセット構築。`class_index=None`（`.mddata` 未検出）でも成立し、その場合 `class_id` / `file_id` は `None` |
| `expand_sample_specs(specs, facets, *, include_roles=("sample",))` | spec を `_` 分割 → 部分集合 AND マッチ。要素間 OR |
| `assign_factor_groups(facets, group_factors=None, group_levels=None, sep="\|")` | 因子軸の直積ラベル |
| `token_vocabulary(facets)` | `{token: {samples, roles, positions}}` ＋ 位置別語彙 |

`build_sample_facets` が `class_index=None` でも成立することで、
**`.mddata` が無いフォルダでも因子フィルタが効く**（現状の
`filter_arf_by_class_ids` は `class_index is None` で `ValueError` を投げていた）。

`expand_sample_specs` は `(matches, excluded_by_role)` を返す。

- `matches: dict[str, list[str]]` = `{spec: [sample_name, ...]}`（元の表記を保持）
- `excluded_by_role: dict[str, list[str]]` = role により落としたサンプル名
- role フィルタは**マッチ後**に適用する（何が落ちたかを開示するため）
- **spec 単位で**1件も掴まなければ `ValueError`（既存 `expand_class_specs` と同方針）。
  メッセージに `token_vocabulary` のトークン一覧を添える

`assign_factor_groups` の規則:

- 各軸（`group_factors` の各要素）で spec マッチ。spec は多トークン可
- **軸内で2つ以上の値にヒットしたら `ValueError`**（排他違反＝指定ミス）
- 軸内でどの値にもヒットしなければその軸は `"other"`
- ラベルは各軸の**元の表記**を `sep` で連結（例 `"ILG|6h"`）
- `group_levels=[...]` は `group_factors=[[...]]` の糖衣。1軸のときラベルは
  連結を伴わないため、出力文字列は従来と完全一致する（後方互換）

#### QC / blank の扱い — フィルタと群分けで分ける

- **フィルタ**（`expand_sample_specs` / `filter_arf_by_class_ids`）は既定で
  `role in ("sample",)` だけを残し、除外件数と内訳を caveat に開示する。
  `include_roles` で明示的に戻せる。統合トークン空間により
  `class_ids=["cerebellum"]` が `20240311_QC_Cerebellum_ICR_NEG_1` のような
  QC を名前経由で掴むようになるため、群平均・PCA の汚染を既定で防ぐ。
- **群分け**（`assign_factor_groups`）は除外せず、QC / blank に `"qc"` / `"blank"`
  ラベルを付けて PCA 上に残す。QC の凝集は前処理品質の判断材料であり、
  消すより見せるほうが有用なため。

### 2. 既存 Class ID 経路の統合

新規追加ではなく既存関数をファセット経由へ書き換える（名前・引数名は据え置き、
API 追加を最小に保つ）。

- **`msdial_classes.filter_arf_by_class_ids`** — 各 `AlignedPeakProperties` 行を
  Class ID 文字列で照合していたのを、`expand_sample_specs` が解決した
  **サンプル名集合**での照合に変更する。引数に `include_roles=("sample",)` を追加。
  stats に `matched_samples` / `excluded_by_role` を追加。
  `class_index is None` での `ValueError` は撤廃する。
- **`msdial_classes.assign_sample_groups`** — `group_factors` 引数を追加し、内部を
  `assign_factor_groups` へ委譲する。
- **`msdial_classes.expand_class_specs`** — `expand_sample_specs` の Class ID 限定版
  として残す（既存テストと `tools_arf` の import を壊さない）。
- **`tools_arf._pool_group_labels`** — Class ID ラベル配列でなくファセット経由で
  解決する。QC / blank を `None` にする既存ガードはそのまま有効。

### 3. ARF ツールへの配線

| ツール | 変更 |
|---|---|
| `arf_parser` | `group_factors: list[list[str]] \| None` と `include_roles: list[str] \| None`（既定 `["sample"]`）を追加。`class_ids` の docstring を「Class ID **またはサンプル名**の因子トークン指定」に改訂 |
| `arf_pca_preprocessed` | `group_factors` を追加 |
| `arf_differential` | `_pool_group_labels` をファセット経由に。既存のプール caveat を拡張し、**解決されたサンプル名を全列挙**する（何を比べたのかを取り違えないため） |
| `arf_list_classes` | `sample_token_vocabulary`（トークン→出現数・role 内訳・出現位置）を追加。既に LLM が「何で絞れるか」を見に行くツールなので、新ツールを知らなくても因子を発見できる |
| `arf_exclude` | `exclude_samples` が完全一致に加えトークン spec を受ける。payload に `resolved_samples`（spec→実サンプル名）を必ず出す。一致ゼロの指定は既存どおり **`ValueError` を投げず `unmatched_samples` へ入れて caveat 化**（除外指定の取りこぼしで解析全体を止めない現行方針を維持） |

`arf_preprocess` は変更しない。

本設計で成立する典型フロー（現状は書けないもの）:

```python
arf_parser(class_ids=["ILG_6h", "control_6h"])            # 6h だけで PCA
arf_parser(group_factors=[["control", "LPS", "ILG", "G_uralensis"],
                          ["0h", "15min", "1h", "6h", "24h"]])  # 20群で色分け
arf_preprocess(...)
arf_differential(group_a="ILG_6h", group_b="control_6h")  # 時点を揃えた2群比較
```

### 4. `tools_samples.py` — `sample_search`（新規）

```python
sample_search(specs=None, directory=None, extensions=None, include_roles=None) -> str  # JSON
```

- **ファセットの供給元**: ロード済み ARF があればそのサンプル名を使う。無ければ
  `directory`（既定 `mcp_core.DATA_DIR`）の `.mddata` から、それも無ければ実ファイル名
  から構築する。**pai2 / dcl を触る前でも呼べる**ことが要件。
- **`specs` 未指定** → 全サンプル ＋ `token_vocabulary`（「何で絞れるか」の探索入口）。
- **`specs` 指定** → 一致サンプルの `name` / `file_id` / `class_id` / `role` /
  `tokens` / `files{拡張子: 実パス}` を返す。ファイル解決は既存の
  `path_resolvers._select_latest_batch` を通し、旧バッチを掴まない。
- **`include_roles` 既定 `None` = 全 role を返す**（検索は絞らず、`role` フィールドで
  判断させる）。フィルタ系ツールの既定とは意図的に異なる。

```
sample_search(specs=["ILG_6h"])
→ file_id 57, 58, 59 と .pai2 / .dcl の実パス
→ pai2_parser(file_path=...) / dcl_find_msms(file_path=...)
→ eic_plot_chromatograms(file_ids=[57, 58, 59])
```

`server.py` のファサードに `from tools_samples import *` を追加する
（`tools_arf` の後、`tools_dataset` の前）。

### 5. エラーハンドリング

- 一致ゼロの spec → `ValueError`（利用可能トークン語彙を添える）。既存の
  `expand_class_specs` と同じ方針。
- `group_factors` の軸内排他違反 → `ValueError`。
- `arf_differential` で両群が同じサンプルを掴む → 既存どおり `ValueError`。
- role 除外で残 0 件 → 明示的にエラーとし、`include_roles` の使い方を示す。

## テスト

`tests/test_sample_factors.py`（新規・純関数）

- トークン化: 処理タイムスタンプ除去、既知拡張子除去、Class ID の合流、
  `G_uralensis` が2トークンに割れること
- `expand_sample_specs`: 単一トークン / 多トークン AND / 要素間 OR / 完全サンプル名 /
  一致ゼロ→`ValueError` / role 除外内訳
- `assign_factor_groups`: 直積ラベル、1軸で従来と同一文字列、`other`、
  軸内2ヒット→`ValueError`、QC に `"qc"` ラベル
- `class_index=None`（`.mddata` 無し）でも名前トークンでフィルタが成立すること

既存テストへの追加

- `tests/test_differential_tools.py`: 時点を揃えた2群比較（`group_a="ILG_6h"` 相当）、
  caveat に解決サンプル名が列挙されること
- `tests/test_arf_exclude.py`: spec による除外と `resolved_samples`
- **後方互換**: 既存の `class_ids` / `group_levels` 系テストが**無改修で通ること**。
  これが統合トークン空間の安全性の主証拠になる。

`tests/test_tools_samples.py`（新規）

- 語彙モード（`specs` 未指定）、spec モード、ファイル解決が最新バッチを選ぶこと、
  ARF 未ロードでも動くこと

## 影響範囲

新規: `sample_factors.py`、`tools_samples.py`、`tests/test_sample_factors.py`、
`tests/test_tools_samples.py`。

改修: `msdial_classes.py`（`filter_arf_by_class_ids` / `assign_sample_groups`）、
`tools_arf.py`（5ツール）、`server.py`（ファサードに1行）。

不変: `preprocessing.py`、`differential.py`、タグ系（`msdial_tags.py`）、
`pai2_reader` / `dcl_reader` / `eic_aef_reader` などのパーサ層、
`arf_preprocess`、`pai2_*` / `dcl_*` / `eic_*` の各ツールシグネチャ。
