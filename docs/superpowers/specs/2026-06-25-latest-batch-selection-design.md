# 最新バッチ自動選択 設計

- 日付: 2026-06-25
- 対象: `server.py` のファイル解決層、`tests/test_load_dataset.py`、MCPドキュメント

## 背景 / 問題

指定フォルダ内に複数日付（複数回のMS-DIAL処理）のファイルが混在すると、LLMが
「曖昧で解析不可」と判断して停止してしまう。実体としては:

- `.arf` / `.arf2` / `.aef` の resolver は `_pick_latest()` を持つが、これは
  **同種ファイルの重複を1つに絞るだけ**で、バッチ（処理タイムスタンプ）単位の
  選別ではない（[server.py:816-873](../../../server.py)）。
- `pai2_parser` は `file_paths[0]`（順不同の先頭）を使い、最新選択が効いていない
  （[server.py:967](../../../server.py)）。
- `list_data_files` は全日付のファイルを区別なく返すため、これを見たLLMが
  「複数日付混在＝曖昧」と解釈しうる。

MS-DIALの出力は全ファイル種が `AlignmentResult_<YYYY_MM_DD_HH_MM_SS>_...` の
処理タイムスタンプ接頭辞を共有する（ユーザー確認済み）。この**埋め込み
タイムスタンプ＝バッチ識別子**として利用できる。

## 目標

フォルダに複数バッチが混在していても、**最新バッチ（最大の埋め込み
タイムスタンプに一致する全ファイル）を自動選択して解析を続行する**。
選択結果はユーザー/LLMに明示し、拒否させない。

非目標: `list_data_files` の戻り値仕様変更、日付（YYYY_MM_DD）粒度での丸め
（処理タイムスタンプ完全一致で識別する）、サンプル別の細粒度選別。

## 設計

### 1. バッチ抽出ヘルパー

```python
def _batch_key(path: str) -> str:
    """ファイル名に埋め込まれた処理タイムスタンプ（バッチ識別子）を返す。
    AlignmentResult の完全タイムスタンプを優先、無ければ12-14桁連番、
    どちらも無ければ空文字。"""

def _select_latest_batch(paths: list[str]) -> list[str]:
    """最新バッチに属するファイルだけに絞る。
    - 埋め込みタイムスタンプを持つファイルがあれば、その最大値に一致する
      ファイル群を採用する。
    - タイムスタンプを持たないファイルはバッチ判定不能なので除外せず温存
      （最新バッチの結果に常に含める）。
    - 全ファイルが無タイムスタンプなら全件そのまま返す（現状互換）。"""
```

`_batch_key` は既存の `_recency_key` と同じ正規表現
（`_ALIGNMENT_TIMESTAMP_RE` → `_COMPACT_TIMESTAMP_RE`）を共用し、正規化した
タイムスタンプ文字列のみを返す（mtimeは見ない＝コピーで保たれるバッチ識別子に
依存）。

### 2. resolver へのバッチ選別組み込み

各 resolver の `real_paths` 確定後に `_select_latest_batch` を挟む:

- `resolve_arf_file_path`: `_select_latest_batch` → PeakProperties 優先 → `_pick_latest`
- `resolve_arf2_file_path`: `_select_latest_batch` → `_pick_latest`
- `resolve_eicaef_file_path`: `_select_latest_batch` → `_pick_latest`
- **新規 `resolve_pai2_file_path(file_path=None)`**: 上記同様。`pai2_parser` の
  `file_paths[0]` ロジックをこのヘルパー呼び出しに置換する。

`_pick_latest` は最新バッチ内でさらに1ファイルへ絞るフォールバックとして残す
（通常バッチ内では各種1ファイルなので実質no-op）。

### 3. 透明性

- `load_dataset` 出力に、検出バッチ数と選択結果を明示する1行を追加:
  「複数バッチ検出時は最新（`<timestamp>`）を自動選択、旧バッチはスキップ」。
- `MCP_INSTRUCTIONS` に1文追記: 複数日付/バッチの混在は解析を妨げない、
  最新バッチが自動選択される旨。
- 各 resolver / `_select_latest_batch` の docstring を更新。

### 4. テスト（`tests/test_load_dataset.py`）

- `_select_latest_batch`: 2バッチ混在で旧バッチが除外され最新バッチだけ残る。
- 無タイムスタンプファイルが温存される。
- 全件無タイムスタンプなら全件返る。
- `resolve_pai2_file_path` が最新バッチの .pai2 を選ぶ。
- `load_dataset`（または各 resolve）が新しい arf/arf2 を選ぶ統合確認。

## エラーハンドリング

- 該当ファイル無し: 既存どおり `None` / 警告文字列を返す（挙動不変）。
- タイムスタンプ抽出不能: 除外せず温存（誤って解析対象を失わない安全側）。

## 影響範囲

`server.py`（ヘルパー追加＋4 resolver＋`pai2_parser`＋`MCP_INSTRUCTIONS`）、
`tests/test_load_dataset.py`。`list_data_files` の公開仕様は不変。
