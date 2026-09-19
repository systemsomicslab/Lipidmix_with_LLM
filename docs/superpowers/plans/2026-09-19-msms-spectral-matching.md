# MS/MS スペクトル照合 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1 つの feature について「付いている名前は本当か」を、参照スペクトルとの突き合わせ（スコア＋対向プロット）で検証できるようにする。

**Architecture:** 参照ライブラリ（`.dbs` / `.msp`）を sha256 を鍵とする永続 SQLite store へ 1 度だけ変換し、precursor m/z 窓 × 極性で候補を引く。採点は MS-DIAL の個別スコア定義を**走査ごと移植**した純関数で行い、mzTab-M の `id_confidence_measure[4..8]` と直接比較できる数値を出す。描画はサーバ側で PNG にして返す。

**Tech Stack:** Python 3.14（`C:/Python314/python.exe`）、`lz4.block` 4.4.5、`msgpack` 1.1.2、`sqlite3`（stdlib）、`matplotlib`、FastMCP。

**Spec:** [docs/superpowers/specs/2026-09-19-msms-spectral-matching-design.md](../specs/2026-09-19-msms-spectral-matching-design.md)

## Global Constraints

spec と `CLAUDE.md` から。**全タスクの要件に暗黙に含まれる。**

- Python は `C:/Python314/python.exe`。テストは**リポジトリルートから** `C:/Python314/python.exe -m pytest tests -q`。
- **コミットは背景実行にする。** `.githooks/pre-commit` が全テストを回し、実測 **188〜196 秒**かかる。既定のコマンドタイムアウトを超える。出力をファイルへ落として `FAILED` を後から引けるようにする。**フック実行中にファイルを編集しない**（その編集がそのテスト実行に混ざる）。
- **`git add` はコミットが拒否されてもステージに残る。** 前のコミットが失敗したら、次に `git commit` する前に `git status` でステージ内容を確認する。
- 戻り値は `lipidmix.core.serialization.json_payload()` で返す。`json.dumps(..., indent=2)` を書かない。
- 全 MCP ツールに `structured_output=False` を付ける。付けないと同じ内容が content と structuredContent の**両方**で送られる（2 倍）。
- float は丸めてから返す。座標点列は `round_floats()`。巨大な中間データは payload から外してセッションへ持つ。
- `lipidmix/core/mcp_core.py` は leaf。ここから `lipidmix.library.*` を import しない。
- 可変状態の正準は `lipidmix.core.session_state.session`。`server.<name>` はスナップショット束縛なので、差し替えは正準モジュール側に当てる。
- 前提状態が無いときは例外でなく `lipidmix.core.mcp_errors.missing_state(state, required_tools, message)` の封筒を返す。
- **fixture はテスト自身が tmp に作る。** `data/` `analyses/` や `C:\Users\yuu18\datasets\` の実ファイルに依存させない。
- セッションスロットは分離する。`session.library` を足し、既存の `session.arf` / `.arf2` / `.pai2` / `.eic` / `.dataset` を**触らない**。
- 記録: 調査・実装をしたら `docs/HISTRY.md` に**追記**し `docs/task.md` のステータスを更新する（どちらも追跡外・追記専用・日付見出しで区切る）。

### spec からの小さな逸脱（Task 3 で導入）

spec §4 のファイル一覧に `lipidmix/library/record.py` を足す。2 つの reader が同じレコード形を吐くための leaf で、`dbs.py` と `msp.py` の共通部を置く場所が無いと循環依存になるため。Task 12 で spec §4 に 1 行追記する。

---

### Task 1: `query.NormalizedScan` の確認（ゲート）

spec §9.2 の未解決 risk。**ここで前提が崩れたら設計に戻る。** コードは書かない。

**Files:**
- Modify: `docs/HISTRY.md`（追記のみ。追跡外）
- Modify: `docs/superpowers/specs/2026-09-19-msms-spectral-matching-design.md`（§9.2 の結論を追記）

**Interfaces:**
- Consumes: なし
- Produces: 「採点にかける測定スペクトルをどう前処理するか」の確定した答え。Task 6 と Task 7 がこれに依存する。

- [ ] **Step 1: `IAnnotationQuery.NormalizedScan` の実装を読む**

```bash
cd "C:/Users/yuu18/source/repos/MsdialWorkbench"
grep -rn --include=*.cs "NormalizedScan" src/MSDIAL5 | head -20
```

`IAnnotationQuery` インターフェースの定義と、LC-MS 経路でそれを作っている実装クラス（`AnnotationQuery` 系）の両方を開く。

- [ ] **Step 2: 生スペクトルから `NormalizedScan` までの変換を書き出す**

見るべきもの: ピークの間引き（相対強度の足切り）、precursor 近傍の除去、m/z 範囲の制限、強度の正規化、並べ替え。**変換が 0 個なら「生スペクトルと同一」と明記する**（それも答えである）。

- [ ] **Step 3: 結論を spec に追記する**

`docs/superpowers/specs/2026-09-19-msms-spectral-matching-design.md` の §9.2 の末尾に「### 結論（2026-MM-DD）」を足し、変換の一覧か「変換なし」を書く。この節の「実装の第 1 工程とする」という文はそのまま残す（経緯が読めなくなるため）。

- [ ] **Step 4: 判断する**

- 変換が無い、または `.dcl` の生スペクトルから決定的に再現できる → **続行**。Task 6 の `match_spectrum` にその前処理を入れる。
- 再現に `.dcl` に無い情報（生データの再読み込み等）が要る → **停止して人に相談**。spec §9.1 の受け入れ試験が成立しないので、設計に戻る必要がある。

- [ ] **Step 5: `docs/HISTRY.md` に追記してコミット**

```bash
cd "C:/Users/yuu18/Lipidmix_with_LLM"
S="$TMPDIR/msms"; mkdir -p "$S"
git add docs/superpowers/specs/2026-09-19-msms-spectral-matching-design.md
git commit -F - > "$S/c.log" 2>&1 <<'MSG'
docs: NormalizedScan の前処理を確認して spec に結論を書く

採点にかける測定スペクトルが .dcl の生スペクトルと何が違うかを上流で確認し、
移植の前提を確定させた。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

**背景実行にすること。** 完了後 `tail -5 "$S/c.log"` で `FAILED` が無いことと、`git log --oneline -1` を確認する。

---

### Task 2: `docs/schema/` に Key 番号表を置く

**インデックス定数の正準は `docs/schema/*.md`** という規約に従い、**実装より先に**置く。

**Files:**
- Create: `docs/schema/molecule_ms_reference.md`

**Interfaces:**
- Consumes: なし
- Produces: Task 3 が写す Key 番号の正準表。

- [ ] **Step 1: 上流のピン（照合したコミット）を取る**

```bash
git -C "C:/Users/yuu18/source/repos/MsdialWorkbench" log --oneline -1
```

- [ ] **Step 2: 既存の schema 文書の書式に合わせて書く**

```bash
cd "C:/Users/yuu18/Lipidmix_with_LLM" && ls docs/schema/ && head -30 docs/schema/*.md | head -40
```

- [ ] **Step 3: `docs/schema/molecule_ms_reference.md` を書く**

必須の内容:

1. 出典 — `src/Common/CommonStandard/Components/MoleculeMsReference.cs`、照合したコミット。
2. **Key 0–28 の全表。27 が飛び番**（`DatabaseUniqueIdentifier` が 20 と 21 のあいだに書かれている）。
3. `IonMode` の列挙 — `{Positive=0, Negative=1, Both=2}`（`src/Common/CommonStandard/Enum/CommonEnums.cs`）。
4. `LargeListMessagePack` の枠組み（`src/Common/CommonStandard/MessagePack/LargeListMessagePack.cs`）:
   - `ExtensionTypeCode = 99` / `HeaderSize = 11` / `OffsetCutoff = 1073741824`
   - チャンク = `c9` + ext 長(4B BE) + `63` + `d2` + 展開後サイズ(4B BE) + LZ4 **block**
   - **罠**: 展開後先頭 5 バイトは配列ヘッダ用に予約されるが、要素数が小さいと `WriteArrayHeader` が短形式（`0xdc` / fixarray）で書く。**要素は常にオフセット 5 から始まる**。
5. `.dbs` の ZIP 構造 — `MetabolomicsDB/<DB名>/DataBase` / `MetabolomicsDB/MS-FINDER/DataBase` / `Storage`。`Storage` 自身も ext99+LZ4 で包まれている。
6. `MsRefSearchParameterBase` の Key 表（0–19、`AndromedaScoreCutOff` が Key 19 で飛び番）。
7. ドリフト確認の手順（既存 schema 文書に倣う）:

```bash
git -C <clone> diff --stat <pinned> origin/master -- "*MoleculeMsReference.cs" "*LargeListMessagePack.cs"
```

- [ ] **Step 4: リンクが切れていないか確認**

```bash
cd "C:/Users/yuu18/Lipidmix_with_LLM" && "C:/Python314/python.exe" -m pytest tests/test_readme_links.py -q
```
Expected: PASS

- [ ] **Step 5: コミット（背景実行）**

```bash
git add docs/schema/molecule_ms_reference.md
git commit -m "docs(schema): MoleculeMsReference の Key 番号表と .dbs/.lbm2 の枠組みを書く

..." # 本文に飛び番(27)と短形式 array header の罠を明記する
```

---

### Task 3: `.dbs` / `.lbm2` リーダ

**Files:**
- Create: `lipidmix/library/__init__.py`（空）
- Create: `lipidmix/library/record.py`
- Create: `lipidmix/library/dbs.py`
- Test: `tests/test_library_dbs.py`

**Interfaces:**
- Consumes: Task 2 の Key 番号表。
- Produces:
  - `lipidmix.library.record.RECORD_FIELDS: tuple[str, ...]`
  - `lipidmix.library.record.make_record(**kwargs) -> dict` — 欠けたキーを既定値で埋める
  - `lipidmix.library.dbs.iter_decompressed_chunks(data: bytes) -> Iterator[bytes]`
  - `lipidmix.library.dbs.read_array_count(chunk: bytes) -> int`
  - `lipidmix.library.dbs.iter_records(path: str | Path) -> Iterator[dict]`
  - `lipidmix.library.dbs.read_storage_meta(path: str | Path) -> dict | None`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_library_dbs.py`:

```python
"""`.dbs` / `.lbm2` リーダ。fixture はテスト自身が組む（実データに依存しない）。"""
import struct
import zipfile

import lz4.block
import msgpack
import pytest

from lipidmix.library import dbs


def _pack_chunk(records: list[list]) -> bytes:
    """LargeListMessagePack のチャンク 1 個を組む。

    要素数に応じて array ヘッダが短形式になる点を再現する（本番の罠）。
    要素は常にオフセット 5 から始まる。
    """
    body = b"".join(msgpack.packb(r, use_bin_type=True) for r in records)
    n = len(records)
    if n < 16:
        header = bytes([0x90 | n])
    elif n < 65536:
        header = b"\xdc" + struct.pack(">H", n)
    else:
        header = b"\xdd" + struct.pack(">I", n)
    raw = header + b"\x00" * (5 - len(header)) + body
    comp = lz4.block.compress(raw, store_size=False)
    return (b"\xc9" + struct.pack(">I", len(comp) + 5) + b"\x63"
            + b"\xd2" + struct.pack(">i", len(raw)) + comp)


def _record(name="GABA", mz=104.0706, ion_mode=1, peaks=((87.04, 999.0), (69.03, 500.0))):
    r = [None] * 29
    r[0] = 0
    r[1] = mz
    r[2] = [[1, [1.23, 0, 0]]]
    r[3] = ion_mode
    r[4] = [[m, i] + [None] * 4 + [0, 0] + [None] * 3 + [0, False] for m, i in peaks]
    r[5] = name
    r[6] = None
    r[7] = ""
    r[8] = "NCCCC(=O)O"
    r[9] = "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    r[10] = [1.00782503207, 1, "[M+H]+", 1, 1, True, 0.0, 0.0, False, False]
    r[14] = "AminoAcid"
    return r


def test_a_short_array_header_is_read_correctly(tmp_path):
    """要素数が小さいと array ヘッダが短形式になる。常に 5 バイトと仮定すると壊れる。"""
    chunk = _pack_chunk([_record()] * 3)
    dec = next(dbs.iter_decompressed_chunks(chunk))
    assert dbs.read_array_count(dec) == 3


def test_records_come_back_from_a_multi_chunk_lbm2(tmp_path):
    path = tmp_path / "lib.lbm2"
    path.write_bytes(_pack_chunk([_record(name="A")] * 2) + _pack_chunk([_record(name="B")]))

    records = list(dbs.iter_records(path))

    assert [r["name"] for r in records] == ["A", "A", "B"]
    assert records[0]["precursor_mz"] == pytest.approx(104.0706)
    assert records[0]["ion_mode"] == "negative"      # IonMode=1
    assert records[0]["adduct"] == "[M+H]+"
    assert records[0]["inchikey"] == "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    assert records[0]["compound_class"] == "AminoAcid"
    assert records[0]["spectrum"] == [[87.04, 999.0], [69.03, 500.0]]


def test_a_dbs_zip_is_read_from_its_database_entry(tmp_path):
    path = tmp_path / "Project_Loaded.msp2.dbs"
    storage = {"MetabolomicsDataBases": [{"DataBase": ["mylib", 4, 2, "C:/x/mylib.lbm2"],
                                          "Pairs": [[0, {"SerializableAnnotatorKey":
                                                         [3, {"Parameter": [0.0, 2000.0, 2.0, 100.0, 20.0,
                                                                            0.01, 0.025, 0.0, 0.0, 0.0225,
                                                                            0.0225, 0.09, 0.0, 0.8, 1.0,
                                                                            True, True, False, False, 0.1],
                                                              "SourceType": 4, "Key": "mylib_1",
                                                              "Priority": 1}]}]]}]}
    packed = msgpack.packb(storage, use_bin_type=True)
    comp = lz4.block.compress(packed, store_size=False)
    wrapped = (b"\xc9" + struct.pack(">I", len(comp) + 5) + b"\x63"
               + b"\xd2" + struct.pack(">i", len(packed)) + comp)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("MetabolomicsDB/mylib/DataBase", _pack_chunk([_record(name="Z")]))
        z.writestr("MetabolomicsDB/MS-FINDER/DataBase", b"")
        z.writestr("Storage", wrapped)

    assert [r["name"] for r in dbs.iter_records(path)] == ["Z"]

    meta = dbs.read_storage_meta(path)
    assert meta["library_name"] == "mylib"
    assert meta["source_path"] == "C:/x/mylib.lbm2"
    assert meta["search_params"]["ms2_tolerance"] == pytest.approx(0.025)
    assert meta["search_params"]["mass_range_end"] == pytest.approx(2000.0)
    assert meta["search_params"]["rt_tolerance"] == pytest.approx(2.0)


def test_an_empty_msfinder_entry_is_not_mistaken_for_the_database(tmp_path):
    """MS-FINDER エントリは空で出る。これを DataBase と取り違えない。"""
    path = tmp_path / "P_Loaded.msp2.dbs"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("MetabolomicsDB/MS-FINDER/DataBase", b"")
        z.writestr("MetabolomicsDB/real/DataBase", _pack_chunk([_record(name="R")]))
    assert [r["name"] for r in dbs.iter_records(path)] == ["R"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_dbs.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'lipidmix.library'`）

- [ ] **Step 3: `lipidmix/library/record.py` を書く**

```python
"""参照ライブラリの正規化レコード（leaf）。

`.dbs` と `.msp` の 2 つの入口が吐く形を 1 つに揃える。これが store のスキーマで
あり、採点エンジンの入力契約でもある。ここは stdlib しか import しない。
"""
from __future__ import annotations

RECORD_FIELDS = (
    "name", "precursor_mz", "ion_mode", "adduct", "rt",
    "formula", "inchikey", "smiles", "compound_class", "ontology",
    "spectrum", "library_id", "record_index",
)

#: 上流 `CommonEnums.cs` の `enum IonMode { Positive, Negative, Both }`。
#: レコードには整数ではなく文字列で持つ（保存形式に上流の列挙を漏らさない）。
ION_MODES = {0: "positive", 1: "negative", 2: "both"}


def make_record(**kwargs) -> dict:
    """欠けたキーを既定値で埋めた正規化レコードを返す。"""
    unknown = set(kwargs) - set(RECORD_FIELDS)
    if unknown:
        raise ValueError(f"未知のフィールド: {sorted(unknown)}")
    record = {field: None for field in RECORD_FIELDS}
    record["spectrum"] = []
    record.update(kwargs)
    return record
```

- [ ] **Step 4: `lipidmix/library/dbs.py` を書く**

```python
"""MS-DIAL の `.dbs` / `.lbm2` を読む（LargeListMessagePack + LZ4 block）。

Key 番号と枠組みの正準は `docs/schema/molecule_ms_reference.md`。推測で直さない。

deps: record のみ。mcp_core / session_state / tools_* を import しない。
"""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Iterator

from lipidmix.library.record import ION_MODES, make_record

EXT_TYPE_CODE = 99
CHUNK_HEADER_SIZE = 11          # ext32 ヘッダ 6 + 長さ 5

# MoleculeMsReference の [Key(N)]。docs/schema/molecule_ms_reference.md が正準。
_K_PRECURSOR_MZ = 1
_K_CHROMXS = 2
_K_ION_MODE = 3
_K_SPECTRUM = 4
_K_NAME = 5
_K_FORMULA = 6
_K_ONTOLOGY = 7
_K_SMILES = 8
_K_INCHIKEY = 9
_K_ADDUCT = 10
_K_COMPOUND_CLASS = 14

# MsRefSearchParameterBase の [Key(N)]（配列位置）。
_SEARCH_PARAM_KEYS = {
    0: "mass_range_begin", 1: "mass_range_end", 2: "rt_tolerance",
    5: "ms1_tolerance", 6: "ms2_tolerance",
    9: "squared_weighted_dot_cutoff", 10: "squared_simple_dot_cutoff",
    11: "squared_reverse_dot_cutoff", 12: "matched_peaks_percentage_cutoff",
    13: "total_score_cutoff", 14: "minimum_spectrum_match",
}


def iter_decompressed_chunks(data: bytes) -> Iterator[bytes]:
    """チャンク列を展開して順に返す。"""
    import lz4.block

    offset = 0
    while offset < len(data):
        if data[offset] != 0xC9:
            raise ValueError(f"ext32 ヘッダではありません: {data[offset]:#04x} (offset={offset})")
        ext_len = struct.unpack(">I", data[offset + 1:offset + 5])[0]
        type_code = struct.unpack("b", data[offset + 5:offset + 6])[0]
        if type_code != EXT_TYPE_CODE:
            raise ValueError(f"想定外の ext type: {type_code}")
        raw_len = struct.unpack(">i", data[offset + 7:offset + 11])[0]
        body_len = ext_len - 5
        body = data[offset + CHUNK_HEADER_SIZE: offset + CHUNK_HEADER_SIZE + body_len]
        offset += CHUNK_HEADER_SIZE + body_len
        yield lz4.block.decompress(body, uncompressed_size=raw_len)


def read_array_count(chunk: bytes) -> int:
    """展開後チャンクの要素数を返す。

    先頭 5 バイトは配列ヘッダ用に予約されるが、`WriteArrayHeader` は要素数が
    小さいと**短形式**で書く。常に 5 バイトの `0xdd` と仮定すると最終チャンクで
    壊れる。要素は常にオフセット 5 から始まる（`DeserializeList` が固定）。
    """
    first = chunk[0]
    if first == 0xDD:
        return struct.unpack(">I", chunk[1:5])[0]
    if first == 0xDC:
        return struct.unpack(">H", chunk[1:3])[0]
    if 0x90 <= first <= 0x9F:
        return first & 0x0F
    raise ValueError(f"配列ヘッダではありません: {first:#04x}")
```

`iter_records` は `.dbs`（ZIP）と `.lbm2`（生）を拡張子ではなく**中身**で見分ける（ZIP は `PK\x03\x04` で始まる）。`_to_record` が Key を引いて `make_record` に渡す。`Spectrum` の各ピークは 13 フィールドの配列で、使うのは先頭 2 つ（m/z と強度）だけ。`AdductType` は配列でインデックス 2 が表示名。`ChromXs` から RT を取る経路は `[0][1][0]` だが、`.dbs` では参照 RT が入っていないことが多いので **`None` 許容**にする。

- [ ] **Step 5: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_dbs.py -q`
Expected: PASS（4 件）

- [ ] **Step 6: 実物で 1 度だけ確かめる（テストには入れない）**

```bash
cd "C:/Users/yuu18/Lipidmix_with_LLM"
"C:/Python314/python.exe" -c "
from lipidmix.library import dbs
p = r'C:/Users/yuu18/datasets/a_lipidome_landscape_of_aging_in_mice/rplc/kidney/neg/Dataset_2026_09_09_17_28_59_Loaded.msp2.dbs'
print(dbs.read_storage_meta(p))
n = sum(1 for _ in dbs.iter_records(p)); print('records =', n)
"
```
Expected: `records = 1406352`、`search_params` の `ms2_tolerance` が 0.025。
**一致しなければ止まって原因を調べる。** 数字は spec §2.2 / §2.6 の実測値。

- [ ] **Step 7: コミット（背景実行）**

```bash
git add lipidmix/library/__init__.py lipidmix/library/record.py lipidmix/library/dbs.py tests/test_library_dbs.py
git commit -m "feat(library): .dbs / .lbm2 リーダを足す"
```

---

### Task 4: `.msp` リーダ

**Files:**
- Create: `lipidmix/library/msp.py`
- Test: `tests/test_library_msp.py`

**Interfaces:**
- Consumes: `lipidmix.library.record.make_record`
- Produces: `lipidmix.library.msp.iter_records(path: str | Path) -> Iterator[dict]`（`dbs.iter_records` と**同じ形**を返す）

- [ ] **Step 1: 失敗するテストを書く**

```python
"""`.msp` リーダ。別名表は上流 `MspFileParcer.cs` の switch を写している。"""
import textwrap

import pytest

from lipidmix.library import msp

_MSP = textwrap.dedent("""\
    NAME: GABA
    PRECURSORMZ: 104.0706
    PRECURSORTYPE: [M+H]+
    IONMODE: Positive
    FORMULA: C4H9NO2
    INCHIKEY: BTCSSZJGUNDROE-UHFFFAOYSA-N
    SMILES: NCCCC(=O)O
    RETENTIONTIME: 1.23
    Num Peaks: 2
    87.0441\t999
    69.0335\t500

    Name: Glutamate
    precursor_m/z: 148.0604
    precursor_type: [M+H]+
    ion_mode: Negative
    num_peaks: 1
    130.0499 800
""")


def test_both_records_are_read(tmp_path):
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")

    records = list(msp.iter_records(path))

    assert [r["name"] for r in records] == ["GABA", "Glutamate"]


def test_the_first_record_keeps_every_field(tmp_path):
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")

    r = list(msp.iter_records(path))[0]

    assert r["precursor_mz"] == pytest.approx(104.0706)
    assert r["adduct"] == "[M+H]+"
    assert r["ion_mode"] == "positive"
    assert r["formula"] == "C4H9NO2"
    assert r["inchikey"] == "BTCSSZJGUNDROE-UHFFFAOYSA-N"
    assert r["rt"] == pytest.approx(1.23)
    assert r["spectrum"] == [[87.0441, 999.0], [69.0335, 500.0]]


def test_the_field_name_dialects_are_accepted(tmp_path):
    """`precursor_m/z` `num_peaks` `ion_mode` も上流が受ける別名。"""
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")

    r = list(msp.iter_records(path))[1]

    assert r["precursor_mz"] == pytest.approx(148.0604)
    assert r["ion_mode"] == "negative"
    assert r["spectrum"] == [[130.0499, 800.0]]


def test_comment_lines_and_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "lib.msp"
    path.write_text("# a comment\n\nNAME: X\nPRECURSORMZ: 1.0\nNum Peaks: 0\n", encoding="utf-8")
    assert [r["name"] for r in msp.iter_records(path)] == ["X"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_msp.py -q`
Expected: FAIL（`No module named 'lipidmix.library.msp'`）

- [ ] **Step 3: 実装する**

要点:
- レコードの区切りは **`NAME:` で始まる行**（大小無視。上流 `MspFileReader` の `isRecordStarted`）。
- `#` で始まる行は読み飛ばす。
- フィールド名は `:` の左を小文字化して照合。別名表は上流の `switch` を写す。最低限: `name` / `precursormz`|`precursor_mz`|`precursor_m/z` / `precursortype`|`precursor_type` / `ionmode`|`ion_mode` / `formula` / `inchikey`|`inchi_key`|`inchi key` / `smiles` / `ontology` / `compoundclass` / `retentiontime`|`retention_time`|`rt` / `num peaks`|`numpeaks`|`num_peaks` / `comment`|`comments`。
- `Num Peaks` の**次の行から**ピークが始まる。区切りは**タブまたは空白**。宣言本数より実際が少ないことがあるので、本数を信用せず空行かレコード開始まで読む。
- `ion_mode` は `Positive` / `Negative` を小文字化して入れる。不明な値は `None`。
- **ピークは m/z 昇順に並べ替えてから返す。** 採点の走査が昇順を前提にしている（Task 6）。

- [ ] **Step 4: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_msp.py -q`
Expected: PASS（4 件）

- [ ] **Step 5: コミット（背景実行）**

```bash
git add lipidmix/library/msp.py tests/test_library_msp.py
git commit -m "feat(library): .msp リーダを足す"
```

---

### Task 5: 永続 store

**Files:**
- Create: `lipidmix/library/store.py`
- Test: `tests/test_library_store.py`

**Interfaces:**
- Consumes: `dbs.iter_records` / `msp.iter_records` / `dbs.read_storage_meta`
- Produces:
  - `lipidmix.library.store.LIBRARY_CACHE_ENV = "LIPIDMIX_LIBRARY_CACHE_DIR"`
  - `lipidmix.library.store.cache_dir() -> Path`
  - `lipidmix.library.store.source_sha256(path) -> str`
  - `lipidmix.library.store.store_path_for(path, cache_dir=None) -> Path`
  - `lipidmix.library.store.open_store(path, *, cache_dir=None, rebuild=False) -> LibraryStore`
  - `LibraryStore.summary() -> dict`
  - `LibraryStore.candidates(precursor_mz, *, mz_tol, ion_mode=None, rt=None, rt_tol=None) -> list[dict]`
  - `LibraryStore.close() -> None`

- [ ] **Step 1: 失敗するテストを書く**

```python
"""参照ライブラリの永続 store。"""
import textwrap

import pytest

from lipidmix.library import store

_MSP = textwrap.dedent("""\
    NAME: A
    PRECURSORMZ: 100.0
    IONMODE: Positive
    Num Peaks: 2
    50.0 999
    80.0 500

    NAME: B
    PRECURSORMZ: 100.005
    IONMODE: Positive
    Num Peaks: 1
    50.0 999

    NAME: C
    PRECURSORMZ: 100.0
    IONMODE: Negative
    Num Peaks: 1
    50.0 999

    NAME: D
    PRECURSORMZ: 200.0
    IONMODE: Positive
    Num Peaks: 1
    50.0 999
""")


@pytest.fixture()
def library(tmp_path, monkeypatch):
    monkeypatch.setenv(store.LIBRARY_CACHE_ENV, str(tmp_path / "cache"))
    path = tmp_path / "lib.msp"
    path.write_text(_MSP, encoding="utf-8")
    return path


def test_the_store_is_built_once_and_reopened(library):
    s1 = store.open_store(library)
    built_at = store.store_path_for(library).stat().st_mtime_ns
    assert s1.record_count == 4
    s1.close()

    s2 = store.open_store(library)
    assert store.store_path_for(library).stat().st_mtime_ns == built_at  # 作り直していない
    assert s2.record_count == 4
    s2.close()


def test_a_changed_source_gets_a_different_store(library, tmp_path):
    first = store.store_path_for(library)
    library.write_text(_MSP + "\nNAME: E\nPRECURSORMZ: 300.0\nNum Peaks: 0\n", encoding="utf-8")
    assert store.store_path_for(library) != first


def test_candidates_are_filtered_by_mz_window_and_polarity(library):
    s = store.open_store(library)
    try:
        hits = s.candidates(100.0, mz_tol=0.01, ion_mode="positive")
        assert sorted(r["name"] for r in hits) == ["A", "B"]   # C は極性違い、D は窓の外
        assert hits[0]["spectrum"] == [[50.0, 999.0], [80.0, 500.0]]
    finally:
        s.close()


def test_omitting_the_ion_mode_keeps_both_polarities(library):
    s = store.open_store(library)
    try:
        assert sorted(r["name"] for r in s.candidates(100.0, mz_tol=0.01)) == ["A", "B", "C"]
    finally:
        s.close()


def test_the_summary_reports_what_was_loaded(library):
    s = store.open_store(library)
    try:
        summary = s.summary()
        assert summary["record_count"] == 4
        assert summary["ion_modes"] == {"positive": 3, "negative": 1}
        assert summary["source_sha256"][:2].isalnum()
        assert summary["search_params"] is None       # .msp には同梱されない
    finally:
        s.close()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_store.py -q`
Expected: FAIL

- [ ] **Step 3: 実装する**

SQLite のスキーマ:

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE record(
    id INTEGER PRIMARY KEY, name TEXT, precursor_mz REAL NOT NULL,
    ion_mode TEXT, adduct TEXT, rt REAL, formula TEXT, inchikey TEXT,
    smiles TEXT, compound_class TEXT, ontology TEXT, spectrum BLOB NOT NULL);
CREATE INDEX record_mz ON record(precursor_mz);
```

- `spectrum` は `msgpack.packb([[mz, intensity], ...])`。`SpectrumPeak` の 13 フィールドを捨てるので、元の 1% 以下に縮む（spec §5.2）。
- `store_path_for` は `cache_dir() / f"{stem}-{sha256[:16]}.sqlite"`。**sha256 が鍵**なので、元ファイルが変われば別ファイルになり、無効化の判断が要らない。
- `cache_dir()` は `os.getenv(LIBRARY_CACHE_ENV)` → 無ければ `mcp_core.DATA_DIR / ".library-cache"`。**`mcp_core.DATA_DIR` は module 修飾で参照する**（`from ... import DATA_DIR` を書かない）。
- 構築は**一時ファイルに書いてから rename** する（途中で落ちた store を掴まないため）。
- `candidates` は `WHERE precursor_mz BETWEEN ? AND ?` に、`ion_mode` 指定時は `AND ion_mode = ?`、`rt` 指定時は `AND (rt IS NULL OR ABS(rt - ?) <= ?)` を足す。**RT が無いレコードを落とさない**（`.msp` の RT は別の LC 条件で測られていることがあり、落とすと正解を失う）。
- `.dbs` のときは `read_storage_meta` の `search_params` を `meta` 表に JSON で入れる。

- [ ] **Step 4: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_store.py -q`
Expected: PASS（5 件）

- [ ] **Step 5: 実物で構築時間を測る（テストには入れない）**

```bash
"C:/Python314/python.exe" -c "
import time, os
os.environ['LIPIDMIX_LIBRARY_CACHE_DIR'] = r'C:/Users/yuu18/AppData/Local/Temp/libcache'
from lipidmix.library import store
p = r'C:/Users/yuu18/datasets/a_lipidome_landscape_of_aging_in_mice/rplc/kidney/neg/Dataset_2026_09_09_17_28_59_Loaded.msp2.dbs'
t = time.time(); s = store.open_store(p); print('build', round(time.time()-t, 1), 's', s.record_count)
t = time.time(); s.candidates(700.0, mz_tol=0.01, ion_mode='negative'); print('query', round(time.time()-t, 3), 's')
"
```

所要を `docs/HISTRY.md` に記録する。**構築が 5 分を超えるようなら止めて相談**（spec の前提は「全走査は十数秒の桁」）。

- [ ] **Step 6: コミット（背景実行）**

```bash
git add lipidmix/library/store.py tests/test_library_store.py
git commit -m "feat(library): sha256 を鍵とする永続 store を足す"
```

---

### Task 6: 採点エンジン

**このタスクの目的は数値が MS-DIAL と一致すること。** spec §6.3 の瑕疵をそのまま写す。

**Files:**
- Create: `lipidmix/analysis/spectral_match.py`
- Test: `tests/test_spectral_match.py`

**Interfaces:**
- Consumes: Task 1 の前処理の結論
- Produces:
  - `weighted_dot_product(measured, reference, *, bin_width, mass_begin=0.0, mass_end=2000.0) -> float`（**二乗値**、比較不能なら `-1.0`）
  - `simple_dot_product(...) -> float`（同上）
  - `reverse_dot_product(...) -> float`（同上）
  - `matched_peaks_scores(...) -> tuple[float, float]`（`(percentage, count)`、比較不能なら `(-1.0, -1.0)`）
  - `spectral_entropy_similarity(measured, reference, *, bin_width) -> float`（比較不能なら `-1.0`）
  - `match_spectrum(measured, reference, *, ms2_tol, mass_begin=0.0, mass_end=2000.0) -> dict`

`measured` / `reference` はいずれも `[[mz, intensity], ...]`。

- [ ] **Step 1: 失敗するテストを書く**

```python
"""採点エンジン。上流 `MsScanMatching.cs` の定義の写し。

**ここで固定している値は上流の挙動そのもの**で、素直な実装に直すと落ちる。
瑕疵を含めて写しているのは mzTab の数値と比較可能にするため（spec §6.3）。
"""
import math

import pytest

from lipidmix.analysis import spectral_match as sm

_A = [[100.0, 999.0], [200.0, 500.0], [300.0, 100.0]]


def test_an_identical_spectrum_scores_one():
    for fn in (sm.simple_dot_product, sm.weighted_dot_product, sm.reverse_dot_product):
        assert fn(_A, _A, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_a_disjoint_spectrum_scores_zero():
    other = [[150.0, 999.0], [250.0, 500.0]]
    assert sm.simple_dot_product(_A, other, bin_width=0.01) == pytest.approx(0.0, abs=1e-9)


def test_an_empty_spectrum_returns_the_not_computed_sentinel():
    """上流は比較不能を 0 ではなく -1 で返す。0（＝合わない）と混同しない。"""
    assert sm.simple_dot_product([], _A, bin_width=0.01) == -1.0
    assert sm.weighted_dot_product(_A, [], bin_width=0.01) == -1.0
    assert sm.matched_peaks_scores(_A, [], bin_width=0.01) == (-1.0, -1.0)
    assert sm.spectral_entropy_similarity([], [], bin_width=0.01) == -1.0


def test_a_single_peak_reference_is_penalised():
    """正規化強度 > 0.1 の参照窓が 1 個なら penalty 0.75（weighted / reverse のみ）。"""
    one = [[100.0, 999.0]]
    assert sm.weighted_dot_product(one, one, bin_width=0.01) == pytest.approx(0.75, abs=1e-9)
    assert sm.reverse_dot_product(one, one, bin_width=0.01) == pytest.approx(0.75, abs=1e-9)
    # simple には penalty が無い。
    assert sm.simple_dot_product(one, one, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_the_penalty_ladder_matches_upstream():
    for n, expected in ((1, 0.75), (2, 0.88), (3, 0.94), (4, 0.97), (5, 1.0)):
        spec = [[100.0 + 10 * i, 999.0] for i in range(n)]
        assert sm.weighted_dot_product(spec, spec, bin_width=0.01) == pytest.approx(expected, abs=1e-9)


def test_reverse_ignores_measured_peaks_absent_from_the_reference():
    """reverse は参照グリッドだけを歩く。測定側の余分なピークは効かない。"""
    reference = [[100.0, 999.0], [200.0, 999.0], [300.0, 999.0], [400.0, 999.0], [500.0, 999.0]]
    measured = reference + [[777.0, 999.0]]
    assert sm.reverse_dot_product(measured, reference, bin_width=0.01) == pytest.approx(
        sm.reverse_dot_product(reference, reference, bin_width=0.01), abs=1e-9)


def test_simple_is_reduced_by_an_extra_measured_peak():
    """simple は両方のグリッドを歩くので、測定側の余分なピークが効く。"""
    reference = [[100.0, 999.0], [200.0, 999.0]]
    measured = reference + [[777.0, 999.0]]
    assert sm.simple_dot_product(measured, reference, bin_width=0.01) < sm.simple_dot_product(
        reference, reference, bin_width=0.01)


def test_matched_peaks_counts_reference_windows_above_one_percent():
    reference = [[100.0, 999.0], [200.0, 999.0], [300.0, 1.0]]   # 3 本目は 1% 未満
    measured = [[100.0, 10.0]]
    percentage, count = sm.matched_peaks_scores(measured, reference, bin_width=0.01)
    assert count == 1.0
    assert percentage == pytest.approx(0.5)     # libCounter は 2


def test_entropy_similarity_of_an_identical_spectrum_is_one():
    assert sm.spectral_entropy_similarity(_A, _A, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)


def test_match_spectrum_returns_the_square_rooted_values():
    """mzTab に出ているのは平方根側。比較の土俵を合わせる（spec §5.1）。"""
    result = sm.match_spectrum(_A, _A, ms2_tol=0.01)
    assert result["simple_dot_product"] == pytest.approx(
        math.sqrt(sm.simple_dot_product(_A, _A, bin_width=0.01)), abs=1e-9)
    assert result["matched_peaks_count"] == 3.0
    assert result["entropy_similarity"] == pytest.approx(1.0, abs=1e-9)


def test_match_spectrum_reports_the_alignment():
    """数値には出ない「どの窓が合ったか」。対向プロットの注釈がこれを読む。"""
    reference = [[100.0, 999.0], [200.0, 500.0]]
    measured = [[100.0, 999.0]]
    alignment = sm.match_spectrum(measured, reference, ms2_tol=0.01)["alignment"]
    assert [(round(a["mz"], 3), a["matched"]) for a in alignment] == [(100.0, True), (200.0, False)]


def test_unsorted_input_is_sorted_before_scoring():
    """上流の走査は m/z 昇順を前提にしている。並べ替えずに渡すと静かに壊れる。"""
    shuffled = list(reversed(_A))
    assert sm.simple_dot_product(shuffled, _A, bin_width=0.01) == pytest.approx(1.0, abs=1e-9)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_spectral_match.py -q`
Expected: FAIL

- [ ] **Step 3: 実装する**

モジュール docstring に**必ず**書くこと:

```python
"""MS/MS スペクトルの照合スコア（純関数）。

**上流 `MsScanMatching.cs` の写しである。** 定義を揃えているのは mzTab-M の
`id_confidence_measure[4..8]` と同じ土俵で数値を比較するためで、次の「明らかに変な点」は
**意図的にそのまま残している**。直すと比較の土俵が消える（spec §6.3）。

- 上流には `wM` / `wR` の計算があるがどこにも使われていない。移植していない。
- 上流の weighted には中身が同一の if/else 分岐がある。条件ごと落としている。
- simple の `× 999` は比になる時点で打ち消える。意味は無い。
- 窓の走査は固定幅でなく、**同じピークが隣り合う 2 つの窓に二重計上されうる**。
  素直な 1 対 1 アラインメントに書き直してはいけない。
- entropy に Li et al. 2021 の低エントロピー重み変換は入っていない。足さない。

比較不能（どちらかのスペクトルが空）は **0 ではなく -1** を返す。0 は「合わない」を
意味するので、混同すると「照合していない」が「合わなかった」に化ける。

前提: 入力は m/z 昇順。`_prepare()` が並べ替えるので呼び側は気にしなくてよい。
"""
```

実装の骨格（走査は 3 通りあるが、窓の合算は共通に切り出せる）:

```python
def _prepare(spectrum):
    """None を空に潰し、m/z 昇順に並べ替えた [[mz, intensity], ...] を返す。"""
    if not spectrum:
        return []
    return sorted(([float(p[0]), float(p[1])] for p in spectrum), key=lambda p: p[0])


def _peak_count_penalty(reference_norm):
    """正規化強度 > 0.1 の参照窓の数で決まる penalty（weighted / reverse のみ）。"""
    n = sum(1 for value in reference_norm if value > 0.1)
    return {1: 0.75, 2: 0.88, 3: 0.94, 4: 0.97}.get(n, 1.0)
```

走査は上流の `focusedMz` の進め方をそのまま写す:

- **weighted / simple** — カーソルは両スペクトルのピークの和集合を進む。
- **reverse / matched peaks** — カーソルは `focusedMz = peaks2[remaindIndexL].Mass`、つまり**参照のピークだけ**を進む。

`match_spectrum` は 5 種のスコアを計算し、dot product 3 種は `math.sqrt(max(v, 0.0))` を取って返す（`-1` の番兵はそのまま `-1` で通す）。`alignment` は**参照グリッド**の窓ごとに `{"mz", "measured", "reference", "matched"}` を並べる。

- [ ] **Step 4: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_spectral_match.py -q`
Expected: PASS（12 件）

- [ ] **Step 5: コミット（背景実行）**

```bash
git add lipidmix/analysis/spectral_match.py tests/test_spectral_match.py
git commit -m "feat(analysis): MS-DIAL の個別スコア定義を移植する"
```

---

### Task 7: 実データ突き合わせ（受け入れゲート）

**ここが通るまで先へ進まない。** spec §9.1。

**Files:**
- Create: `scripts/verify_spectral_match.py`
- Modify: `docs/HISTRY.md`（追記のみ）

**Interfaces:**
- Consumes: Task 3・5・6 の全部
- Produces: 移植の正しさの証拠。以降のタスクはこれを前提にする。

- [ ] **Step 1: 照合スクリプトを書く**

`scripts/verify_spectral_match.py` が受け取るもの: `--dbs` / `--mztab` / `--dcl-dir` / `--limit`（既定 200）。

やること:

1. `.dbs` から store を作る（`search_params` の `ms2_tolerance` を `bin_width` に使う）。
2. mzTab の SME 行を読む。`lipidmix.mztab.reader` の既存パーサを使い、`id_confidence_measure[4..8]` と `exp_mass_to_charge`・`chemical_name`・`spectra_ref` を取る。
3. 各 SME について、`spectra_ref` が指す測定の `.dcl` から実スペクトルを引く（`lipidmix.dcl.reader.deserialize_dcl` / `get_msms_by_precursor`）。**`top_n_peaks` は絞らない**（間引くと数値が変わる）。
4. 名前が一致する参照レコードを store から引く。
5. `match_spectrum` を回し、`[4] Simple` `[5] Weighted` `[6] Reverse` `[7] count` `[8] percentage` と突き合わせる。
6. 相対誤差 **1e-4** 以内を一致とし、一致率と不一致の上位 10 件（名前・両者の値・差）を出す。

- [ ] **Step 2: 実行する**

```bash
cd "C:/Users/yuu18/Lipidmix_with_LLM"
D="C:/Users/yuu18/datasets/a_lipidome_landscape_of_aging_in_mice/rplc/kidney/neg"
"C:/Python314/python.exe" scripts/verify_spectral_match.py \
  --dbs "$D/Dataset_2026_09_09_17_28_59_Loaded.msp2.dbs" \
  --mztab "$D/Height_AlignmentResult_2026_09_09_17_31_52_2026_09_09_18_01_10.mzTab" \
  --dcl-dir "$D"
```

- [ ] **Step 3: 結果で分岐する**

- **一致率が高い** → Task 8 へ。
- **系統的にずれている**（全件が同じ方向に外れる）→ 前処理の差を疑う。Task 1 の結論を読み直し、`match_spectrum` に入れた前処理を見直す。**`spectral_match.py` の式には手を入れない**（式は上流の写しである）。
- **一部だけ外れる** → 外れた側の `CompoundClass` を見る。脂質の `GetLipidomicsMatchedPeaksScores` に落ちている候補が混ざっている可能性がある（`[7]`/`[8]` だけ外れるならこれ）。その場合は「脂質枝の matched peaks は別定義なので一致しない」という**既知の限界**として記録し、`[4][5][6]` の一致で受け入れてよい。
- **全く合わない** → **止まって人に相談**。spec の前提が崩れている。

- [ ] **Step 4: 結果を `docs/HISTRY.md` に記録する**

一致率・許容誤差・外れた件数と理由・使ったファイル名を書く。**この記録が spec §1 の完成条件 5 の証拠になる。**

- [ ] **Step 5: コミット（背景実行）**

```bash
git add scripts/verify_spectral_match.py
git commit -m "test: 移植したスコアを MS-DIAL の実出力と突き合わせる CLI を足す"
```

`.gitignore` に `*.txt` があるので、**出力を .txt で保存して追跡させようとしない**（無言で漏れる）。

---

### Task 8: 対向プロット

**Files:**
- Create: `lipidmix/plots/mirror.py`
- Test: `tests/test_plots_mirror.py`

**Interfaces:**
- Consumes: `match_spectrum` の `alignment`
- Produces:
  - `build_mirror_payload(measured, reference, alignment, *, title, top_labels=8) -> dict`
  - `render_mirror(payload: dict) -> bytes`（PNG）

- [ ] **Step 1: 失敗するテストを書く**

```python
"""対向プロット。座標は payload に持ち、描画は PNG で返す。"""
from lipidmix.plots import mirror


def test_the_payload_keeps_both_spectra_and_the_matches():
    measured = [[100.0, 999.0], [200.0, 500.0]]
    reference = [[100.0, 999.0], [300.0, 250.0]]
    alignment = [{"mz": 100.0, "measured": 1.0, "reference": 1.0, "matched": True},
                 {"mz": 300.0, "measured": 0.0, "reference": 0.25, "matched": False}]

    payload = mirror.build_mirror_payload(measured, reference, alignment, title="GABA")

    assert payload["title"] == "GABA"
    assert payload["measured"] == [[100.0, 999.0], [200.0, 500.0]]
    assert payload["reference"] == [[100.0, 999.0], [300.0, 250.0]]
    assert payload["matched_mz"] == [100.0]


def test_the_labels_are_capped():
    measured = [[float(i), float(1000 - i)] for i in range(50)]
    payload = mirror.build_mirror_payload(measured, measured, [], title="x", top_labels=3)
    assert len(payload["labels"]) <= 3


def test_render_returns_a_png():
    payload = mirror.build_mirror_payload([[100.0, 999.0]], [[100.0, 999.0]],
                                          [{"mz": 100.0, "measured": 1.0,
                                            "reference": 1.0, "matched": True}], title="t")
    png = mirror.render_mirror(payload)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_plots_mirror.py -q`
Expected: FAIL

- [ ] **Step 3: 実装する**

- 上段が測定（上向き）、下段が参照（下向き。強度に `-1` を掛ける）、横軸 m/z 共通。
- 強度は**それぞれ自分の最大値で正規化**してから描く（生の強度だと片方が潰れる）。
- 一致した m/z は色を変える。ラベルは強度上位 `top_labels` 本だけ。
- `render_mirror` は `lipidmix.plots.render.figure_to_png(fig)` を使う。**dpi は既定のまま**（画像トークンは画素数に比例する）。
- `matplotlib` は `Agg` バックエンドで使う（既存の描画モジュールに倣う）。

- [ ] **Step 4: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_plots_mirror.py -q`
Expected: PASS（3 件）

- [ ] **Step 5: 目で見て確かめる**

Task 7 で一致した候補を 1 つ選んで PNG を書き出し、**写真と同じ形になっているか**を確認する（上段青・下段赤・一致点に印）。

- [ ] **Step 6: コミット（背景実行）**

```bash
git add lipidmix/plots/mirror.py tests/test_plots_mirror.py
git commit -m "feat(plots): 測定と参照の対向プロットを足す"
```

---

### Task 9: セッションスロットとパス解決

**Files:**
- Modify: `lipidmix/core/session_state.py`（`LibraryState` の追加と `AnalysisSession.__init__`）
- Modify: `lipidmix/core/path_resolvers.py`（`resolve_library_path` の追加）
- Test: `tests/test_library_session.py`

**Interfaces:**
- Consumes: `LibraryStore`
- Produces:
  - `lipidmix.core.session_state.LibraryState`（属性 `store` / `source_path` / `last_match`）
  - `session.library: LibraryState`
  - `lipidmix.core.path_resolvers.resolve_library_path(file_path: str | None = None) -> str | None`

- [ ] **Step 1: 失敗するテストを書く**

```python
"""ライブラリのセッションスロットとパス解決。"""
from lipidmix.core import mcp_core, path_resolvers, session_state


def test_the_new_slot_does_not_disturb_the_others():
    s = session_state.AnalysisSession()
    assert s.library.store is None
    assert s.library.source_path is None
    assert s.library.last_match is None
    # 既存スロットが消えていないこと（無言の破棄の再発防止）。
    for name in ("arf", "arf2", "pai2", "eic"):
        assert getattr(s, name) is not None
    assert s.dataset is None


def test_a_dbs_is_preferred_over_an_msp(tmp_path, monkeypatch):
    (tmp_path / "other.msp").write_text("NAME: x\n", encoding="utf-8")
    (tmp_path / "P_Loaded.msp2.dbs").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(mcp_core, "DATA_DIR", str(tmp_path))
    assert path_resolvers.resolve_library_path().endswith("P_Loaded.msp2.dbs")


def test_an_msp_is_used_when_no_dbs_exists(tmp_path, monkeypatch):
    (tmp_path / "lib.msp").write_text("NAME: x\n", encoding="utf-8")
    monkeypatch.setattr(mcp_core, "DATA_DIR", str(tmp_path))
    assert path_resolvers.resolve_library_path().endswith("lib.msp")


def test_an_explicit_path_wins(tmp_path, monkeypatch):
    (tmp_path / "P_Loaded.msp2.dbs").write_bytes(b"PK\x03\x04")
    explicit = tmp_path / "explicit.msp"
    explicit.write_text("NAME: x\n", encoding="utf-8")
    monkeypatch.setattr(mcp_core, "DATA_DIR", str(tmp_path))
    assert path_resolvers.resolve_library_path(str(explicit)) == str(explicit)


def test_nothing_found_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_core, "DATA_DIR", str(tmp_path))
    assert path_resolvers.resolve_library_path() is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_session.py -q`
Expected: FAIL

- [ ] **Step 3: 実装する**

`LibraryState` は他のスロットと同じく小さく保つ:

```python
class LibraryState:
    """参照ライブラリのスロット。他スロットとは共有しない。

    `store` は開いたままの `LibraryStore`。`last_match` は直近の
    `library_match_feature` の結果（候補ごとのスペクトルとアラインメントを含む）で、
    `library_plot_mirror` がここから座標を読む。**payload には載せない**。
    """

    def __init__(self):
        self.store = None
        self.source_path: str | None = None
        self.last_match: dict | None = None
```

`resolve_library_path` は既存リゾルバと同じ流儀で書く。探索順は `*_Loaded.msp2.dbs` → `*.msp`。複数あれば `_pick_latest` で最新を選ぶ。**`*.msp2` と `*.lbm2` は候補にしない**（前者は 0 バイトのことがあり、後者は入口として出さない）。

- [ ] **Step 4: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_session.py tests/test_package_layout.py -q`
Expected: PASS

- [ ] **Step 5: コミット（背景実行）**

```bash
git add lipidmix/core/session_state.py lipidmix/core/path_resolvers.py tests/test_library_session.py
git commit -m "feat(core): library スロットとパス解決を足す"
```

---

### Task 10: MCP ツール 3 本

**Files:**
- Create: `lipidmix/library/tools.py`
- Modify: `server.py`（import と再エクスポート）
- Modify: `tests/test_server_registration.py`（`EXPECTED_TOOLS`）
- Test: `tests/test_library_tools.py`

**Interfaces:**
- Consumes: `open_store` / `match_spectrum` / `mirror` / `resolve_library_path` / `session.library`
- Produces: `__all__ = ["library_load", "library_match_feature", "library_plot_mirror"]`

- [ ] **Step 1: 失敗するテストを書く**

```python
"""MCP ツール。戻り値の量と missing_state 契約を固定する。"""
import json
import textwrap

import pytest

from lipidmix.core import mcp_core, session_state
from lipidmix.library import tools

_MSP = textwrap.dedent("""\
    NAME: GABA
    PRECURSORMZ: 104.0706
    IONMODE: Positive
    Num Peaks: 2
    87.0441 999
    69.0335 500
""")


@pytest.fixture(autouse=True)
def fresh_session(tmp_path, monkeypatch):
    monkeypatch.setattr(session_state, "session", session_state.AnalysisSession())
    monkeypatch.setattr(mcp_core, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIPIDMIX_LIBRARY_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "lib.msp").write_text(_MSP, encoding="utf-8")
    return tmp_path


def test_matching_without_a_loaded_library_returns_missing_state():
    payload = json.loads(tools.library_match_feature(104.07))
    assert payload["error"]["code"] == "missing_state"
    assert "library_load" in payload["error"]["required_tools"]


def test_plotting_without_a_match_returns_missing_state():
    payload = json.loads(tools.library_plot_mirror())
    assert payload["error"]["code"] == "missing_state"
    assert "library_match_feature" in payload["error"]["required_tools"]


def test_loading_reports_what_was_loaded(fresh_session):
    text = tools.library_load()
    assert "lib.msp" in text
    assert session_state.session.library.store is not None


def test_the_payload_carries_no_coordinate_arrays(fresh_session, monkeypatch):
    """座標はセッションに持つ。戻り値に点列を載せない（文脈を食うため）。"""
    tools.library_load()
    monkeypatch.setattr(tools, "_measured_spectrum",
                        lambda *a, **k: [[87.0441, 999.0], [69.0335, 500.0]])
    text = tools.library_match_feature(104.0706, ion_mode="positive")

    assert "87.0441" not in text or text.count("87.0441") <= 2   # ラベル程度は可
    assert session_state.session.library.last_match is not None
    assert session_state.session.library.last_match["candidates"][0]["spectrum"]


def test_loading_does_not_disturb_the_other_slots(fresh_session):
    before = session_state.session.arf
    tools.library_load()
    assert session_state.session.arf is before
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_tools.py -q`
Expected: FAIL

- [ ] **Step 3: 実装する**

3 本とも `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=...), structured_output=False)`。

- `library_load(file_path=None, rebuild=False) -> str` — **`readOnlyHint=False`**（キャッシュを書く）。戻り値は `json_payload` で `{status, file, source_sha256, record_count, ion_modes, compound_classes(上位10), search_params}`。`search_params` が `None` のときは「`.msp` には許容幅が同梱されないので既定値を使う」旨を明示する。
- `library_match_feature(precursor_mz, rt=None, ion_mode=None, dcl_file=None, mz_tol=None, ms2_tol=None, rt_tol=None, top_n=5) -> str` — `readOnlyHint=True`。`mz_tol` / `ms2_tol` の既定は store の `search_params` → 無ければ 0.01 / 0.025。測定スペクトルは `_measured_spectrum()`（`resolve_dcl_file_path` + `deserialize_dcl` + `get_msms_by_precursor`）で引く。**`.dcl` に MS/MS が無いときは `not_found` を返し、「未取得であって合わないのではない」と明示する**。候補一覧は **TSV**（列名 1 回）。座標は `session.library.last_match` へ。
- `library_plot_mirror(rank=1, output=None) -> ImageContent | str` — `readOnlyHint=True`。`resolve_plot_output(output)` が `payload` なら `round_floats()` した座標を返し、`image` なら PNG を返す。

`server.py` には副作用 import と `from lipidmix.library.tools import *` を足す（**薄いファサードのまま**。実体をここに書かない）。

- [ ] **Step 4: 登録数を更新する**

```bash
C:/Python314/python.exe -m pytest tests/test_server_registration.py -q
```

落ちたら `EXPECTED_TOOLS` に 3 本足す。**件数だけを合わせない**（`ToolAnnotations` も検証されている）。

- [ ] **Step 5: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_library_tools.py tests/test_server_registration.py -q`
Expected: PASS

- [ ] **Step 6: コミット（背景実行）**

```bash
git add lipidmix/library/tools.py server.py tests/test_library_tools.py tests/test_server_registration.py
git commit -m "feat(library): 照合の MCP ツール 3 本を足す"
```

---

### Task 11: `verify_peak_annotation` の拡張と mzTab サブスコアの回収

**Files:**
- Modify: `lipidmix/msdial/peak_verification.py`（`msms_evidence` に `spectral_match` を足す）
- Modify: `lipidmix/mztab/dataset_state.py`（`id_confidence_measure[2..8]` の回収）
- Test: `tests/test_peak_verification.py`（既存）、`tests/test_dataset_state.py`（既存）

**Interfaces:**
- Consumes: `match_spectrum`、`session.library`
- Produces: `msms_evidence(...)["spectral_match"]`、`DatasetState` の同定エントリに `confidence_measures: dict`

- [ ] **Step 1: 失敗するテストを書く（2 本）**

```python
# tests/test_peak_verification.py に追記
def test_the_msms_band_still_has_only_three_states():
    """PASS / FLAG_ONLY / ABSENT の 3 状態は契約。照合はその内側に足す。"""
    from lipidmix.msdial.peak_verification import msms_evidence
    assert msms_evidence({"msms_spectrum": [[100.0, 999.0]]})["band"] == "PASS"
    assert msms_evidence({"has_msms": True})["band"] == "FLAG_ONLY"
    assert msms_evidence({})["band"] == "ABSENT"


def test_spectral_match_is_absent_without_a_loaded_library():
    from lipidmix.msdial.peak_verification import msms_evidence
    assert msms_evidence({"msms_spectrum": [[100.0, 999.0]]}).get("spectral_match") is None
```

```python
# tests/test_dataset_state.py に追記
_SUBSCORE_MZTAB = textwrap.dedent("""\
    MTD\tmzTab-version\t2.0.0-M
    MTD\tms_run[1]-location\tfile:///s1.raw
    MTD\tassay[1]-ms_run_ref\tms_run[1]
    MTD\tid_confidence_measure[1]\t[,, MS-DIAL algorithm matching score, ]
    MTD\tid_confidence_measure[2]\t[,, Retention time similarity, ]
    MTD\tid_confidence_measure[3]\t[,, m/z similarity, ]
    MTD\tid_confidence_measure[4]\t[,, Simple dot product, ]
    MTD\tid_confidence_measure[5]\t[,, Weighted dot product, ]
    MTD\tid_confidence_measure[6]\t[,, Reverse dot product, ]
    MTD\tid_confidence_measure[7]\t[,, Matched peaks count, ]
    MTD\tid_confidence_measure[8]\t[,, Matched peaks percentage, ]
    SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tchemical_name\tadduct_ions
    SML\t1\t1\tnull\tGABA\t[M+H]1+
    SFH\tSMF_ID\tSME_ID_REFS\texp_mass_to_charge\tretention_time_in_seconds\tabundance_assay[1]
    SMF\t1\t1\t104.07\t72.0\t1.0
    SEH\tSME_ID\tevidence_input_id\tdatabase_identifier\tchemical_name\tadduct_ion\texp_mass_to_charge\tid_confidence_measure[1]\tid_confidence_measure[2]\tid_confidence_measure[3]\tid_confidence_measure[4]\tid_confidence_measure[5]\tid_confidence_measure[6]\tid_confidence_measure[7]\tid_confidence_measure[8]\trank
    SME\t1\t1\tnull\tGABA\t[M+H]1+\t104.07\t0.91\tnull\t0.99\t0.95\t0.93\t0.88\t7\t0.7\t1
""")


def test_the_sub_scores_are_recovered_from_the_sme_row(tmp_path):
    """mzTab は [4..8] に個別スコアを出している。total だけ読むと捨てることになる。"""
    ds = _build_text(tmp_path, _SUBSCORE_MZTAB, "Height_subscores.mzTab")

    measures = ds.feature_annotations["1"]["confidence_measures"]

    assert measures["simple_dot_product"] == pytest.approx(0.95)
    assert measures["weighted_dot_product"] == pytest.approx(0.93)
    assert measures["reverse_dot_product"] == pytest.approx(0.88)
    assert measures["matched_peaks_count"] == pytest.approx(7.0)
    assert measures["matched_peaks_percentage"] == pytest.approx(0.7)
    assert measures["mz_similarity"] == pytest.approx(0.99)
    assert "retention_time_similarity" not in measures       # null の列は入れない


def test_the_existing_total_score_reading_is_unchanged(tmp_path):
    """best_id_confidence_value は下流の契約。サブスコア回収で壊さない。"""
    ds = _build_text(tmp_path, _SUBSCORE_MZTAB, "Height_total.mzTab")
    assert ds.feature_annotations["1"]["confidence_value"] == pytest.approx(0.91)
```

**列名は `MTD id_confidence_measure[N]` の宣言行から引く**こと。番号は固定順だが、`manualAssigned` があると 9 本目が増える（上流 `SetIdConfidenceMeasure`）ので、**位置に依存すると静かにずれる**。宣言名 → スネークケースの対応表をモジュール定数に置く:

```python
_CONFIDENCE_MEASURE_NAMES = {
    "MS-DIAL algorithm matching score": "total_score",
    "Retention time similarity": "retention_time_similarity",
    "Retention index similarity": "retention_index_similarity",
    "m/z similarity": "mz_similarity",
    "Simple dot product": "simple_dot_product",
    "Weighted dot product": "weighted_dot_product",
    "Reverse dot product": "reverse_dot_product",
    "Matched peaks count": "matched_peaks_count",
    "Matched peaks percentage": "matched_peaks_percentage",
    "CCS similarity": "ccs_similarity",
}
```

`[,, X, ]` の形から `X` を取り出して引く。**未知の名前は捨てずに、そのままスネークケース化して入れる**（上流が測定種別を増やしても黙って落ちないように）。

- [ ] **Step 2: テストが失敗することを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_peak_verification.py tests/test_dataset_state.py -q`
Expected: FAIL

- [ ] **Step 3: 実装する**

`msms_evidence`:
- **既存の 3 状態の判定に一切触らない。** `band == "PASS"` かつ `session.library.store` があるときだけ `spectral_match` キーを足す。
- `peak_verification.py` から `session_state` を import する形になるので、**循環していないこと**を確認する（`session_state` は leaf 側）。循環するなら、照合結果を引数で受け取る形にして呼び側（`verify_peak_annotation`）で組み立てる。

`dataset_state.py`:
- `MTD id_confidence_measure[N]` の宣言を読み、名前 → 列名の対応を作る。
- SME 行からその列を読み、`confidence_measures` に入れる。**`best_id_confidence_value` の既存の読み取りは変えない**（下流の契約）。
- 値が `null` の列は入れない。

- [ ] **Step 4: テストが通ることを確認**

Run: `C:/Python314/python.exe -m pytest tests/test_peak_verification.py tests/test_dataset_state.py -q`
Expected: PASS

- [ ] **Step 5: 差次的エクスポートの契約が動いていないことを確認**

```bash
C:/Python314/python.exe -m pytest tests -q -k "export or contract or binding"
```
Expected: PASS。**`lipidmix/analysis/export_contract.py` に差分が無いこと**を `git diff --stat` で示す（別リポ massbank-context との契約）。

- [ ] **Step 6: コミット（背景実行）**

```bash
git add lipidmix/msdial/peak_verification.py lipidmix/mztab/dataset_state.py tests/
git commit -m "feat: 照合結果を同定の証拠に載せ、mzTab のサブスコアを回収する"
```

---

### Task 12: 文書と腐敗防止テスト

**Files:**
- Create: `docs/workflow/library.md`
- Create: `docs/output_format/library.md`
- Modify: `docs/workflow/index.md` / `USAGE.md` / `CLAUDE.md` / `README.md`（必要なら）
- Modify: `lipidmix/tools/` のリソース登録（`lipidmix://docs/output-format/{topic}` に `library` を足す）
- Modify: `docs/superpowers/specs/2026-09-19-msms-spectral-matching-design.md`（§4 に `record.py` を追記）
- Modify: リポジトリ外: `C:\Users\yuu18\Documents\KnowledgeVault\30_Projects\ms-data-parser\ms-data-parser-flow.md`

- [ ] **Step 1: `docs/workflow/library.md` を書く**

`tests/test_workflow_docs.py` が**パス・関数名を AST で実在検証し、対象ツール数を実登録数と突き合わせる**。**行番号は書かない規約**。

- [ ] **Step 2: `docs/output_format/library.md` を書く**

必ず書くこと: 各スコアの意味、**3 つの dot product が平方根側であること**、`-1` が「比較していない」で `0` が「合わない」であること、**spec §6.3 の瑕疵の告知**（素直な実装と数値が違う理由）、`.msp` 由来には許容幅が同梱されないこと。

- [ ] **Step 3: MCP リソースに登録する**

```bash
C:/Python314/python.exe -m pytest tests/test_server_registration.py -q
```
`EXPECTED_RESOURCES` を更新する。

- [ ] **Step 4: `USAGE.md` を更新する**

ツール 3 本を表に足し、**冒頭の宣言件数（現在 63）を直す**。`tests/test_readme_links.py` がツール集合と件数を実登録と突き合わせる。**README.md / CLAUDE.md に数量表現を書かない**（写しが腐るため）。

- [ ] **Step 5: `CLAUDE.md` の構成図に 1 行足す**

```
lipidmix/library/   参照ライブラリ（.dbs / .msp）の読み取りと永続 store。
                    形式ごとに分けないのは、2 つが同じレコード形と同じ store を
                    共有する 2 つの入口にすぎないため（spec 2026-09-19）
```

- [ ] **Step 6: spec §4 に `record.py` を追記する**

計画の「Global Constraints / spec からの小さな逸脱」で予告したもの。

- [ ] **Step 7: KB は更新済みなので、やり直さない**

spec §11 が挙げる KB の訂正（`_Loaded.msp2` 0 バイトの解釈）は **2026-09-19 に対応済み**。あわせて `refs/msdial-loaded-msp2-dbs-contains-reference-library.md` と `failures/importorskip-does-not-guard-native-capability.md` を新設し、INDEX にポインタを足してある。**重複エントリを作らないこと。** 実装中に新しい再利用可能な知見が出たときだけ、既存エントリの重複を確認してから足す。

- [ ] **Step 8: vault の流れ図を直す**

`library_load` が前提状態の連鎖に入り、入口の分岐が増える。**末尾の出典行の日付も書き直す**。リポジトリ外・追跡外で、テストも git も腐敗を検出しない。後回しにすると次のエージェントが古い図を正しいものとして読む。

- [ ] **Step 9: 全テストを通す**

```bash
cd "C:/Users/yuu18/Lipidmix_with_LLM"
C:/Python314/python.exe -m pytest tests -q
```
Expected: 全数 PASS（rdkit の 1 件は環境により skip）

- [ ] **Step 10: `docs/HISTRY.md` と `docs/task.md` を更新してコミット（背景実行）**

```bash
git add docs/ USAGE.md CLAUDE.md lipidmix/tools/
git commit -m "docs: MS/MS 照合の文書とリソースを整える"
```

---

## 完了の確認

spec §1 の完成条件に 1 つずつ答えられること。

| # | 条件 | 証拠 |
|---|---|---|
| 1 | 候補を集めて採点し上位を返せる | Task 10 のテスト |
| 2 | 個別スコアが MS-DIAL と一致する | **Task 7 の実データ突き合わせ**（`docs/HISTRY.md` の記録） |
| 3 | 対向プロットを画像で返せる | Task 8 のテスト＋目視 |
| 4 | `verify_peak_annotation` が照合を証拠に載せる | Task 11 のテスト |
| 5 | 実データで一致を確認済み | Task 7 の記録 |

**残る限界**（spec §9.3）: 受け入れ試験に使えた実データは**脂質の run** である。親水性の実 run にはまだ `.dbs` が無いため、**本来の目的である親水性メタボロミクスでの実用性は未検証**のまま残る。完了報告でこれを黙らない。
