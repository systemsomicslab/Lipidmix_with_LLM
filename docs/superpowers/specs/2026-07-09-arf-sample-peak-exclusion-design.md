# ARF サンプル/ピーク手動除外ツール 設計

- 日付: 2026-07-09
- 状態: 設計確定（ユーザ承認済み・実装計画へ）
- 関連: `preprocessing.py`（特徴量フィルタ）, `tools_arf.py`（ARFツール群）, `session_state.py`

## 1. 背景と目的

現状の ARF フィルタリングは次の粒度をカバーする:

- **スポット（ピーク）レベル**: 強度・アノテーションキーワード・MS-DIALタグ・Class ID・検出率（`arf_parser` / `arf_re_pca`）
- **特徴量（列）レベル**: blank 背景除去・QC RSD（`preprocessing.py`）

欠けているのは **「個々のサンプル（行）を名指しで除外する」口** である。典型ユースケースは
「PCA スコアプロットで明らかに外れている 1 サンプルを外して再解析する」。あわせて
**特定のピーク/スポットを ID で名指し除外**したいケース（既存の強度/キーワードフィルタでは狙い撃ちできない）も対象とする。

行列のサンプルキーは各 `AlignedPeakProperties` エントリの `file_name`、スポットキーは
`MasterAlignmentID`。これらを除外できるようにする。

## 2. 要件（ユーザ確認済み）

- **特定方法**: 手動指定（名前 / ID で除外）。自動外れ値検出は今回のスコープ外。
- **粒度**: サンプル単位（行の除外）＋ ピーク/スポット単位（列の除外）の両方。
- **可逆性**: 可逆。セッションに除外集合を保持し、追加・解除・確認・全クリアができる。
  `filtered_features` は破壊しない。

## 3. アーキテクチャ（Approach A: セッション除外集合 ＋ 行列構築前プルーニング）

除外集合をセッションに保持し、行列を組む直前に `filtered_features` から非破壊的に
プルーニングした派生リストを作って `build_pca_matrix` に渡す。`filtered_features` 本体は
無傷なので、除外の解除・再解析が自在。除外がスポット除去・サンプル除去の**後**に走るため、
`build_pca_matrix` 内の平均補完・分散フィルタは**残ったサンプルで再計算**される（統計的に正しい）。

却下した代替:

- **B. 破壊的フィルタ**（`filtered_features` を直接書換え）: 実装最小だが不可逆 → 可逆要件に反する。
- **C. 既存ツールへ `exclude_*` 引数追加**: 状態を持てず毎回指定が必要・確認/解除ができない → 可逆要件と相性が悪い。

## 4. コンポーネント

### 4.1 セッション状態（`session_state.AnalysisSession`）

```python
self.excluded_samples: set[str] = set()   # 除外する file_name（サンプル）
self.excluded_spots:   set[int] = set()   # 除外する MasterAlignmentID（スポット）
```

- `__init__` で空集合に初期化。
- `load_data` が新ファイルをデシリアライズしたとき（キャッシュミス経路）にリセットする。
  既存のリセット（`pca_result` / `filtered_features` = None）と同じ箇所に追記。
  キャッシュヒット経路（同一ファイル再ロード）では**維持**する。

### 4.2 純ロジック層（新規 `exclusions.py`・MCP 非依存）

`preprocessing.py` / `differential.py` と同格の leaf 純モジュール。

```python
def prune_spots(spots, excluded_samples, excluded_spots):
    """除外集合を適用したスポットリストの非破壊コピーを返す。

    - MasterAlignmentID ∈ excluded_spots のスポットを丸ごと除外。
    - 残スポットの AlignedPeakProperties から file_name ∈ excluded_samples の
      エントリを除去（スポット dict は浅いコピーし、AlignedPeakProperties を
      フィルタ済みリストに差し替える。元の spots / エントリは変更しない）。
    - 除外集合が両方空なら入力をそのまま返してよい（コピー不要）。
    """
```

- 各 `AlignedPeakProperties` エントリの `file_name` は `arf_reader._convert_to_alignment_feature`
  で取得する（build_pca_matrix と同じ導出。位置インデックス非依存）。
- 位置インデックスに依存しない（build_pca_matrix は file_name をキーにするため安全）。

補助述語（`arf_exclude` の照合用）を同モジュールに置く:

```python
def roster(spots) -> tuple[set[str], set[int]]:
    """現データに存在する (sample file_name 集合, MasterAlignmentID 集合) を返す。
    除外指定の未一致検出と list 表示に使う。"""
```

### 4.3 新 MCP ツール（`tools_arf.py`）

```python
@mcp.tool()
def arf_exclude(
    exclude_samples: list[str] | None = None,
    exclude_spots: list[int] | None = None,
    mode: str = "add",
) -> str:
    """PCA 外れサンプルや特定ピークを手動で除外/再包含する（非破壊・可逆）。"""
```

- 前提: `session.filtered_features`（= `arf_parser` 実行済み）。未実行なら error を返す。
- `mode`:
  - `add`（既定）: 指定を除外集合に追加。
  - `remove`: 指定を除外集合から除去（再包含）。
  - `clear`: 除外集合を全消去（引数は無視）。
  - `list`: 現在の除外集合と利用可能サンプル/スポットの件数のみ返す（引数は無視）。
- **照合**: サンプルは `file_name` と**完全一致**、スポットは `MasterAlignmentID`（int）。
  現データ roster に無い指定は `unmatched_samples` / `unmatched_spots` として警告に載せ、
  一致分のみ集合へ反映する。`unmatched` があるときは利用可能サンプル名の一部（先頭 N 件）を
  添えて修正を助ける。
- 戻り（JSON 文字列）: `status`, `mode`, 現在の `excluded_samples` / `excluded_spots`,
  除外前後の**サンプル数・スポット数**（`prune_spots` 適用で算出）, `unmatched_*`, 必要な caveat。
- 完全非破壊: `session.excluded_*` を更新するだけ。行列や `filtered_features` は変えない。

### 4.4 配線（除外を反映する消費点）

行列を `filtered_features` から組む直前に `prune_spots` を挟む。

- **`arf_preprocess`**: `tool_helpers._pp_build_matrix` に渡す前に `prune_spots` を適用。
  → 下流の `arf_pca_preprocessed` / `arf_differential` が自動的に除外反映（これらは
  `session.feature_matrix` を消費するため追加配線不要）。
- **`arf_re_pca`**: `path_resolvers._filter_arf_spots` → タグ/クラスフィルタの後、
  `build_pca_matrix` の前に `prune_spots` を適用。
- **`arf_parser`（初回ロード）**: 適用しない。まず全体 PCA で外れを俯瞰してから除外する運用のため。
- **`arf_list_sample_roles`**: 除外集合を反映せず全サンプルを表示するが、各サンプルに
  `excluded: true/false` を付す（どれを除外中か確認できるように）。
- 除外が有効なとき、`arf_preprocess` / `arf_re_pca` の出力へ
  「ユーザ手動除外: サンプル N 件 / スポット M 件」の注記（caveat / 行）を追加し透明化する。

## 5. データフロー（運用フロー）

```
arf_parser（全体 PCA で外れ俯瞰）
  → 外れサンプル名 / 外れスポット ID を特定
  → arf_exclude(exclude_samples=[...], exclude_spots=[...])   # 可逆・非破壊
  → arf_re_pca            # 生 PCA を除外後で再計算、または
  → arf_preprocess → arf_pca_preprocessed   # 前処理後 PCA を除外後で確認
  → arf_differential      # 除外後の前処理行列で差次的解析
  ［必要なら arf_exclude(mode="remove"/"clear") で戻す］
```

## 6. エッジケースと不変条件

- 除外集合が両方空: 既存挙動と完全に一致（`prune_spots` は入力をそのまま返す）。
- 全サンプル除外 / 全スポット除外: 行列が空になり `build_pca_matrix` が空を返す →
  既存の「PCA 用データを構築できませんでした」経路。加えて `arf_exclude` 側でも
  「残サンプル 0 / 残スポット 0」を caveat で前景化する。
- 除外により列が定数化 → `build_pca_matrix` の `var>0` フィルタが残サンプルで再判定するため
  ゼロ分散列は自動除去され、PCA の StandardScaler で 0 除算を起こさない（プルーニングを
  build 前に置く理由）。
- 未一致指定: エラーにせず一致分のみ反映＋ `unmatched_*` を返す（名前のタイプミスに寛容）。
- `load_data` 新ファイル: 除外集合をリセット（別データに古い除外を持ち越さない）。

## 7. テスト計画（TDD）

**`exclusions.prune_spots`（純ロジック）**
- サンプル除外のみ: 指定 file_name のエントリが全スポットから消える／他は不変。
- スポット除外のみ: 指定 MasterAlignmentID のスポットが消える／他は不変。
- 両方同時。
- 空集合: 入力と同一（非破壊・恒等）。
- 非破壊性: 元の spots / AlignedPeakProperties が変更されない。
- 未存在の名前/ID を渡しても既存分は正しく処理（no-op で落ちない）。

**`exclusions.roster`**
- サンプル file_name 集合・MasterAlignmentID 集合を正しく返す。

**`arf_exclude`（ツール）**
- `add` で集合に入る／件数レポート／非破壊。
- `remove` で再包含。
- `clear` で全消去。
- `list` で現状レポート。
- 未一致サンプル/スポットの警告＋利用可能名の提示。
- `arf_parser` 未実行時の error。

**配線（統合）**
- 除外後に `arf_preprocess` の行列形状が縮む（サンプル行減／スポット列減）。
- 除外後に `arf_re_pca` の行列形状が縮む。
- `arf_pca_preprocessed` / `arf_differential` が除外後行列を消費する。
- `load_data` 新ファイルで除外集合がリセットされる。
- 除外集合が空のとき既存テストが全て不変（回帰なし）。

## 8. スコープ外（YAGNI）

- 自動外れ値検出（マハラノビス距離 / IQR 等での候補提示）。将来の別テーマ。
- ピーク/スポットの部分一致・範囲指定除外（今回は ID 完全一致のみ）。
- 除外理由のメタ記録・監査ログ。
- `.arf2`（arf2_reader）経路への適用（本設計は `.arf` の ARF ツール群が対象）。
