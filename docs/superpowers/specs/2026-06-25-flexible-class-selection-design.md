# 柔軟な Class 指定・群分け 設計

- 日付: 2026-06-25
- 対象: `msdial_classes.py`（純粋関数）、`server.py`（`arf_parser` / `arf_re_pca` /
  `arf_list_classes` / PCAプロット整形ヘルパー）、`tests/`

## 背景 / 問題

MS-DIAL の Class ID は `Cerebellum_gf_AIN` のような **複合因子（`_` 区切り）** の
完全文字列。現状の `filter_arf_by_class_ids` は **casefold 完全一致** ＋ 未知ID
エラー、複数指定 OR で「サンプルの絞り込み」のみを行う
（[msdial_classes.py:169](../../../msdial_classes.py)）。PCA は点をサンプル名で
ラベルするだけで、**Class による群分け（色分け）はしていない**
（[server.py:1207-1241](../../../server.py)）。

このため「Cerebellum での群間比較」「gf での群間比較」「Cerebellum_gf での
群間比較」のような、因子を跨いだ柔軟な選択・群比較ができない。

## 目標

1. **選択の柔軟化**: `Cerebellum` / `gf` / `Cerebellum_gf` のような部分指定を、
   該当する全 Class ID に展開してサブセット抽出する。
2. **群分け（色分け）**: 選択したサブセット内で、PCA の点を群に分けて色分けし、
   群間比較を明示する。

非目標: pai2（ピークレベル）への適用、位置インデックス指定UI、2因子同時の
専用群指定（既定の完全Class ID色分けで賄う）。

## 設計

### 1. 選択 — トークン集合の AND 部分一致

`filter_arf_by_class_ids` の照合規則を変更する:

- 各 Class ID を `_` で分割したトークン集合として扱う（casefold）。
  例: `Cerebellum_gf_AIN` → `{cerebellum, gf, ain}`。
- `class_ids` の各要素も `_` 分割し、**その全トークンを含む** Class ID に一致
  （順不同の AND）。
- リスト内の複数要素は **OR**（例: `["Cerebellum_gf", "Hippocampus_gf"]`）。
- 完全一致は自動的に内包（`Cerebellum_gf_AIN` 指定 → そのクラスのみ）＝後方互換。
- どの Class ID にも一致しない指定はエラー（利用可能 Class ID/トークンを提示）。

```python
def expand_class_specs(specs: list[str], available_class_ids: list[str]) -> dict[str, list[str]]:
    """各 spec を該当する実 Class ID 群に展開する。
    戻り: {spec: [matched_class_id, ...]}。一致ゼロの spec があれば ValueError。"""
```

`filter_arf_by_class_ids` は `requested`（完全一致集合）の代わりに、
`expand_class_specs` の結果を OR で合算した「実 Class ID 集合」を採用する。
stats には従来の `requested_class_ids` に加え、**展開後にマッチした実 Class ID
一覧** を持たせて透明性を確保する。

### 2. 群分け（色分け）

PCA の各サンプルに群ラベル `group` を付与する:

- **既定**: Class メタデータがあれば `group = 完全 Class ID`（フィルタ有無に
  関わらず適用）。
- **任意 `group_levels`**（例: `["gf", "spf"]`）: 各サンプルの Class ID トークン
  集合のうち、`group_levels` に含まれる値トークンを群ラベルにする（その因子だけで
  統合）。
  - どの level にも該当しないサンプル → `"other"` 群（除外せず可視化）。
  - 2つ以上の level に該当（相互排他でない指定＝ユーザーミス）→ ValueError。

```python
def assign_sample_groups(
    sample_names: list[str],
    class_index: dict | None,
    group_levels: list[str] | None = None,
) -> dict[str, str | None]:
    """PCA のサンプル名→群ラベルの対応を返す。
    class_index が無ければ全て None（＝群分けなし）。"""
```

サンプル名→Class ID は `class_index["by_file_name"]`（`normalize_sample_name`）で
解決する（`build_pca_matrix` の sample_names は ARF の file_name）。

### 3. プロット出力への配線

`_format_pca_plot_block` と `_remember_arf_pca_plot` に `groups: dict[str, str|None]`
（または並列リスト）を渡し、各点に `group` フィールドを追加する。`group` が全て
None のときは従来どおり省略。intro テキストに「`group` で色分けして描画」する旨を
追記する。

### 4. API 変更

- `arf_parser` / `arf_re_pca` に `group_levels: list[str] | None = None` を追加。
- `filter_arf_by_class_ids` の照合をトークン AND 部分一致へ変更（`expand_class_specs`
  を使用）。stats に `matched_class_ids` を追加。
- `arf_list_classes` を拡張: 完全 Class ID 分布に加え、**位置別の因子値（トークン
  語彙）** を返し、有効な部分指定を発見しやすくする。

### 5. エラーハンドリング / データフロー

- 未解決サンプル（Class メタデータ無し）の扱いは既存 `class_missing_sample_policy`
  に従う（error/exclude）。
- `group_levels` はフィルタではなくラベル付け（既定で除外しない）。
- 一致ゼロの class_ids 指定、相互排他違反の group_levels はいずれも明示的エラー。

## テスト

- `expand_class_specs`: `gf` / `Cerebellum_gf` / 完全一致 / OR / 未知→エラー。
- `filter_arf_by_class_ids`: トークン AND 部分一致でサブセットが正しく絞られる、
  stats に matched_class_ids。
- `assign_sample_groups`: 既定=完全 Class ID、group_levels で統合、other、相互排他
  違反→エラー、class_index 無し→全 None。
- `arf_parser` / `arf_re_pca`: プロット JSON の各点に group が載る。
- `arf_list_classes`: 因子語彙（位置別トークン）を返す。

## 影響範囲

`msdial_classes.py`（`expand_class_specs` / `assign_sample_groups` 追加、
`filter_arf_by_class_ids` 改修）、`server.py`（2ツールに引数追加、ヘルパー配線、
`arf_list_classes` 拡張）、関連テスト。pai2 系・タグ系は不変。
