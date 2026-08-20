# ワークフロー: データセット投入の入口

`load_dataset` はユーザーがフォルダを渡したときの唯一の入口で、arf2 概観 → arf PCA を
一括実行するオーケストレータ。`arf2_parser` / `arf_parser` を**そのまま呼ぶ**ので、
連鎖の続きは [arf2.md](arf2.md) / [arf.md](arf.md) を参照。

```mermaid
flowchart TD
    LD[load_dataset] --> RES1[path_resolvers.resolve_arf2_file_path]
    LD --> RES2[path_resolvers.resolve_arf_file_path]
    LD --> CAV[session.maybe_prepend_caveat]
    LD --> BATCH[path_resolvers._describe_batch_selection]
    LD --> A2[arf2_parser → arf2.md]
    LD --> A1[arf_parser → arf.md]
    LDF[list_data_files] --> PR[path_resolvers.list_data_files<br/>同一関数オブジェクト]
    SS[sample_search] --> SF[msdial.sample_factors]
```

## list_data_files

前提: なし
状態変更: なし

`lipidmix/tools/dataset.py` で純関数を MCP ツールとして登録しているだけで、専用の
ラッパ関数は存在しない。`resolve_*_file_path` から呼ばれる関数と同一オブジェクト。

1. lipidmix/core/path_resolvers.py  list_data_files()

## load_dataset

前提: なし（`directory` 省略時は `mcp_core.DATA_DIR` を使う）
状態変更: `directory` 指定時に `mcp_core.DATA_DIR` を差し替える。以降 `arf2_parser` /
`arf_parser` がそれぞれ `session` を更新する。

意味論ダイジェストは入口の先頭で 1 回だけ前置する。ここで発火させると、後段の
`arf2_parser` / `arf_parser` 内の同じガードは `caveat_emitted` により no-op になる。

1. lipidmix/tools/dataset.py  load_dataset()
2. └─ lipidmix/core/path_resolvers.py  resolve_arf2_file_path()
3. └─ lipidmix/core/path_resolvers.py  resolve_arf_file_path()
4. └─ lipidmix/core/session_state.py  AnalysisSession.maybe_prepend_caveat()
5. └─ lipidmix/core/path_resolvers.py  _describe_batch_selection()
6. └─ lipidmix/arf2/tools.py  arf2_parser()
7. └─ lipidmix/arf/tools.py  arf_parser()

## sample_search

前提: なし（ARF ロード前でも動く）
状態変更: なし

1. lipidmix/tools/samples.py  sample_search()
2. └─ lipidmix/msdial/sample_factors.py  token_vocabulary()
3. └─ lipidmix/msdial/sample_factors.py  expand_sample_specs()
4. └─ lipidmix/tools/samples.py  _collect_facets()
5. └─ lipidmix/tools/samples.py  _apply_role_filter()
6. └─ lipidmix/tools/samples.py  _describe()
