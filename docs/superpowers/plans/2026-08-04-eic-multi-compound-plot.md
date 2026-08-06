# 複数物質 EIC 重ね描きプロット Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 複数の脂質分子種の EIC を 1 サンプル分だけ同一グラフへ重ね描きするための MCP ツール `eic_plot_compounds` を追加する。

**Architecture:** 既存の `eic_plot_chromatograms`（1 物質 × 複数サンプル / `lipidmix.eic.v1`）は無変更のまま残す。新しく (1) CSS1 を 1 回開いて複数スポットを読むバッチリーダー、(2) ARF2 の同定情報から候補を選び rt/mz で対応付けを検証する `eic_identity_map.py`、(3) 新スキーマ `lipidmix.eic.multi.v1` のペイロードビルダーと matplotlib 描画、(4) それらを束ねる薄い MCP ツール、を積み上げる。

**Tech Stack:** Python 3, 標準ライブラリ `struct` / `os`, `matplotlib`（Agg）, FastMCP (`mcp_core.mcp`), テストは `unittest`（pytest から実行）。

## Global Constraints

- 既存 `eic_plot_chromatograms` と `lipidmix.eic.v1` ペイロードは変更しない。
- 新ツールは 1 呼び出し = 1 サンプル。`file_id` は必須スカラー。
- 物質の選択は ARF2 `Name` の部分一致（大小無視）と `Ontology` の完全一致（大小無視）のみ。`spot_id` 直接指定は受けない。
- rt/mz 検証の許容差は `RT_TOLERANCE = 0.02`（min）、`MZ_TOLERANCE = 0.01`（Da）。
- 候補の予備選抜上限 `max_candidates = 300`（ARF2 `HeightAverage` 降順）。
- 描画トレース数上限 `top_n` の既定値は 24。実測 `max_intensity` 降順で打ち切る。
- 除外理由の語彙は `"spot_out_of_range"` / `"file_id_absent"` / `"rt_mismatch"` / `"mz_mismatch"` / `"below_top_n"` の 5 種のみ。
- 新ツール自身はファイルを書かない。PNG はユーザーの明示要求時に既存 `save_eic_figure` のみが書く。
- `tools_eic.py` は他の `tools_*` / `server` を import しない（既存の依存規約）。
- `eic_identity_map.py` は `arf2_reader` にのみ依存し、EIC バイナリ読み出し・matplotlib・MCP には依存しない。
- テストは `python -m pytest tests/ -v` で全緑にすること。

---

### Task 1: CSS1 バッチリーダー `read_eic_spots_css1`

**Files:**
- Modify: `eic_aef_reader.py:90-236`（`read_eic_spot_css1` をバッチ版へ委譲）
- Test: `tests/test_eic_plot.py`（`EicRandomAccessTests` の下に新クラスを追加）

**Interfaces:**
- Consumes: なし（既存 `_read_exact` を利用）
- Produces:
  - `read_eic_spots_css1(file_path, spot_ids, file_ids=None, *, max_traces=12, max_total_points=200_000, strict=False) -> list[dict]`
    戻り値の各要素は既存 `read_eic_spot_css1` と同じ形（`spot_id`, `rt`, `ri`, `mz`, `drift`, `main_type`, `num_samples`, `selected_samples`, `selected_points`, `samples`）で、`spot_id` 昇順。
  - `read_eic_spot_css1(...)` は署名も戻り値も従来どおり。

- [ ] **Step 1: バッチリーダーの失敗するテストを書く**

`tests/test_eic_plot.py` の `import` 行を差し替える:

```python
from eic_aef_reader import read_eic_spot_css1, read_eic_spots_css1
```

`EicRandomAccessTests` クラスの直後（`class EicPlotToolTests` の前）に追加する:

```python
class EicBatchReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "batch.EIC.aef"
        _write_css1(self.path, [
            {"rt": 1.0, "mz": 100.0, "samples": [
                {"file_id": 1, "peak_top": 1.0, "peak_left": 0.9,
                 "peak_right": 1.1, "points": [(0.9, 10.0), (1.0, 20.0)]},
                {"file_id": 2, "peak_top": 1.0, "peak_left": 0.9,
                 "peak_right": 1.1, "points": [(0.9, 1.0), (1.0, 2.0)]},
            ]},
            {"rt": 2.0, "mz": 200.0, "samples": [
                {"file_id": 2, "peak_top": 2.0, "peak_left": 1.8,
                 "peak_right": 2.2, "points": [(1.8, 3.0), (2.0, 9.0)]},
            ]},
            {"rt": 3.0, "mz": 300.0, "samples": [
                {"file_id": 1, "peak_top": 3.0, "peak_left": 2.8,
                 "peak_right": 3.2, "points": [(2.8, 4.0), (3.0, 8.0)]},
            ]},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_requested_spots_in_one_file_open(self):
        opened = []
        real_open = builtins.open

        def counting_open(*args, **kwargs):
            opened.append(args[0])
            return real_open(*args, **kwargs)

        with unittest.mock.patch("builtins.open", counting_open):
            spots = read_eic_spots_css1(self.path, [2, 0], file_ids=[1])

        self.assertEqual(len(opened), 1)
        self.assertEqual([spot["spot_id"] for spot in spots], [0, 2])
        self.assertEqual(spots[0]["samples"][0]["file_id"], 1)
        self.assertEqual(
            [point[1] for point in spots[1]["samples"][0]["chromatogram"]],
            [4.0, 8.0],
        )

    def test_out_of_range_spot_is_omitted_when_not_strict(self):
        spots = read_eic_spots_css1(self.path, [0, 99], file_ids=[1])
        self.assertEqual([spot["spot_id"] for spot in spots], [0])

    def test_spot_without_requested_file_id_is_returned_empty(self):
        spots = read_eic_spots_css1(self.path, [0, 1], file_ids=[1])
        self.assertEqual([spot["spot_id"] for spot in spots], [0, 1])
        self.assertEqual(spots[1]["samples"], [])
        self.assertEqual(spots[1]["selected_samples"], 0)

    def test_strict_mode_raises_for_out_of_range_and_missing_file_id(self):
        with self.assertRaisesRegex(ValueError, "out of range"):
            read_eic_spots_css1(self.path, [99], file_ids=[1], strict=True)
        with self.assertRaisesRegex(ValueError, "not found"):
            read_eic_spots_css1(self.path, [1], file_ids=[1], strict=True)

    def test_point_budget_is_shared_across_spots(self):
        with self.assertRaisesRegex(ValueError, "point safety limit"):
            read_eic_spots_css1(
                self.path, [0, 2], file_ids=[1], max_total_points=3,
            )
```

ファイル冒頭の import に次を追加する:

```python
import builtins
import unittest.mock
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_eic_plot.py -v`
Expected: FAIL（`ImportError: cannot import name 'read_eic_spots_css1'`）

- [ ] **Step 3: バッチリーダーを実装し、単数版を委譲に変える**

`eic_aef_reader.py` の `read_eic_spot_css1`（90-236 行）を、次の 2 関数で丸ごと置き換える:

```python
def read_eic_spots_css1(
    file_path,
    spot_ids,
    file_ids=None,
    *,
    max_traces=12,
    max_total_points=200_000,
    strict=False,
):
    """Read selected chromatogram traces for several CSS1 alignment spots.

    The CSS1 pointer table permits direct access, so the file is opened once and
    every requested spot is reached with ``seek``. Unselected sample point arrays
    are skipped so plotting a few traces per spot does not expand every
    chromatogram in the file into memory.

    ``strict=True`` reproduces the single-spot contract: an out-of-range
    ``spot_id`` and a missing requested FileID both raise. ``strict=False`` omits
    out-of-range spots from the result and returns spots whose requested FileID
    is absent with an empty ``samples`` list, so callers can tell the two cases
    apart.
    """
    requested_spots = list(spot_ids)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in requested_spots
    ):
        raise ValueError("spot_ids must contain non-negative integers only")
    if max_traces < 1:
        raise ValueError("max_traces must be at least 1")
    if max_total_points < 1:
        raise ValueError("max_total_points must be at least 1")

    requested = None
    requested_set = None
    if file_ids is not None:
        requested = list(file_ids)
        if not requested:
            raise ValueError("file_ids must contain at least one FileID")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in requested):
            raise ValueError("file_ids must contain integers only")
        if len(set(requested)) != len(requested):
            raise ValueError("file_ids must not contain duplicates")
        if len(requested) > max_traces:
            raise ValueError(
                f"At most {max_traces} EIC traces can be plotted at once; "
                f"requested {len(requested)}"
            )
        requested_set = set(requested)

    spots = []
    total_points = 0
    with open(file_path, "rb") as stream:
        file_size = os.fstat(stream.fileno()).st_size
        magic = _read_exact(stream, 4, "CSS1 magic")
        if magic != b"CSS1":
            raise ValueError(f"Not a CSS1 file. Magic: {magic}")
        stream.seek(10)
        num_spots = struct.unpack("<i", _read_exact(stream, 4, "spot count"))[0]
        if num_spots < 0:
            raise ValueError(f"Invalid negative EIC spot count: {num_spots}")
        pointer_table_end = 14 + 8 * num_spots

        for spot_id in sorted(set(requested_spots)):
            if spot_id >= num_spots:
                if strict:
                    raise ValueError(
                        f"spot_id {spot_id} is out of range for {num_spots} EIC spots"
                    )
                continue

            stream.seek(14 + 8 * spot_id)
            pointer = struct.unpack("<q", _read_exact(stream, 8, "spot pointer"))[0]
            if pointer < pointer_table_end or pointer >= file_size:
                raise ValueError(
                    f"Invalid EIC spot pointer for spot_id {spot_id}: {pointer}"
                )
            stream.seek(pointer)

            spot_header = _read_exact(stream, 21, f"spot {spot_id} header")
            rt, ri, mass, drift, main_type, num_samples = struct.unpack(
                "<ffffbi", spot_header
            )
            if num_samples < 0:
                raise ValueError(
                    f"Invalid negative sample count for spot_id {spot_id}: {num_samples}"
                )
            if requested_set is None and num_samples > max_traces:
                raise ValueError(
                    f"spot_id {spot_id} contains {num_samples} samples. "
                    f"Specify file_ids (maximum {max_traces}) instead of silently truncating."
                )

            samples = []
            selected_points = 0
            found_ids = set()
            for sample_index in range(num_samples):
                sample_header = _read_exact(
                    stream, 20, f"spot {spot_id} sample {sample_index} header"
                )
                file_id, num_points, peak_top, peak_left, peak_right = struct.unpack(
                    "<iifff", sample_header
                )
                if num_points < 0:
                    raise ValueError(
                        f"Invalid negative chromatogram point count for FileID {file_id}: "
                        f"{num_points}"
                    )
                point_bytes = 8 * num_points
                if stream.tell() + point_bytes > file_size:
                    raise ValueError(
                        f"Chromatogram payload exceeds file size for spot_id {spot_id}, "
                        f"FileID {file_id}"
                    )

                if requested_set is not None and file_id not in requested_set:
                    stream.seek(point_bytes, os.SEEK_CUR)
                    continue

                if total_points + num_points > max_total_points:
                    raise ValueError(
                        f"Selected EIC traces exceed the {max_total_points} point safety "
                        f"limit; request fewer compounds (lower top_n) or samples"
                    )
                raw_points = _read_exact(
                    stream, point_bytes, f"spot {spot_id} FileID {file_id} chromatogram"
                )
                chromatogram = [
                    [float(x), float(intensity)]
                    for x, intensity in struct.iter_unpack("<ff", raw_points)
                ]
                intensities = [point[1] for point in chromatogram]
                total_points += num_points
                selected_points += num_points
                found_ids.add(file_id)
                samples.append({
                    "file_id": file_id,
                    "peak_left": float(peak_left),
                    "peak_top": float(peak_top),
                    "peak_right": float(peak_right),
                    "num_points": num_points,
                    "mean_intensity": (
                        float(sum(intensities) / len(intensities)) if intensities else 0.0
                    ),
                    "max_intensity": float(max(intensities)) if intensities else 0.0,
                    "chromatogram": chromatogram,
                })

            if strict and requested_set is not None:
                missing = [file_id for file_id in requested if file_id not in found_ids]
                if missing:
                    raise ValueError(
                        f"Requested FileID values were not found in spot_id {spot_id}: "
                        f"{missing}"
                    )

            spots.append({
                "spot_id": spot_id,
                "rt": float(rt),
                "ri": float(ri),
                "mz": float(mass),
                "drift": float(drift),
                "main_type": int(main_type),
                "num_samples": num_samples,
                "selected_samples": len(samples),
                "selected_points": selected_points,
                "samples": samples,
            })

    return spots


def read_eic_spot_css1(
    file_path,
    spot_id,
    file_ids=None,
    *,
    max_traces=12,
    max_total_points=200_000,
):
    """Read selected chromatogram traces for one CSS1 alignment spot.

    Thin wrapper over :func:`read_eic_spots_css1` with the strict single-spot
    contract: an out-of-range ``spot_id`` or a missing requested FileID raises.
    """
    if isinstance(spot_id, bool) or not isinstance(spot_id, int) or spot_id < 0:
        raise ValueError("spot_id must be a non-negative integer")
    spots = read_eic_spots_css1(
        file_path,
        [spot_id],
        file_ids,
        max_traces=max_traces,
        max_total_points=max_total_points,
        strict=True,
    )
    return spots[0]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_eic_plot.py -v`
Expected: PASS（`EicRandomAccessTests` の既存 4 件と `EicBatchReadTests` の 5 件がすべて緑）

- [ ] **Step 5: コミット**

```bash
git add eic_aef_reader.py tests/test_eic_plot.py
git commit -m "feat(eic): add batched CSS1 spot reader for multi-compound plots"
```

---

### Task 2: ARF2 同定マップ `eic_identity_map.py`

**Files:**
- Create: `eic_identity_map.py`
- Test: `tests/test_eic_identity_map.py`

**Interfaces:**
- Consumes: `arf2_reader.deserialize`（`Name` / `Ontology` / `AdductType` / `RT` / `MassCenter` / `HeightAverage` / `AlignmentID` を持つ辞書のリストを返す）
- Produces:
  - `IdentityCandidate` = `TypedDict` with `spot_id: int`, `name: str`, `ontology: str`, `adduct: str`, `rt: float`, `mz: float`, `height_average: float`
  - `load_arf2_records(arf2_path) -> list[dict]`
  - `select_identity_candidates(records, *, names=None, ontologies=None, max_candidates=300) -> tuple[list[IdentityCandidate], list[str]]`
  - `verify_spot_match(candidate, spot, *, rt_tolerance=RT_TOLERANCE, mz_tolerance=MZ_TOLERANCE) -> str | None`
  - モジュール定数 `RT_TOLERANCE = 0.02`, `MZ_TOLERANCE = 0.01`, `MAX_CANDIDATES = 300`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_eic_identity_map.py` を新規作成する:

```python
import unittest

from eic_identity_map import (
    MAX_CANDIDATES,
    select_identity_candidates,
    verify_spot_match,
)


def _record(alignment_id, name, ontology, rt, mz, height=0.0, adduct="[M+H]+"):
    return {
        "AlignmentID": alignment_id,
        "Name": name,
        "Ontology": ontology,
        "AdductType": adduct,
        "RT": rt,
        "MassCenter": mz,
        "HeightAverage": height,
    }


class SelectIdentityCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            _record(0, "PC(12:0/13:0)", "PC", 13.538, 636.4, height=10.0),
            _record(1, "TG(15:0/18:1/15:0) (d5)", "TG", 26.676, 800.7, height=30.0),
            _record(2, "Ceramide (d18:1/25:0)", "Cer", 22.148, 650.6, height=20.0),
            _record(3, "Unknown", "", 5.0, 300.0, height=5.0),
        ]

    def test_name_query_matches_case_insensitive_substring(self):
        candidates, caveats = select_identity_candidates(
            self.records, names=["ceramide"],
        )
        self.assertEqual([c["spot_id"] for c in candidates], [2])
        self.assertEqual(candidates[0]["name"], "Ceramide (d18:1/25:0)")
        self.assertEqual(candidates[0]["ontology"], "Cer")
        self.assertAlmostEqual(candidates[0]["rt"], 22.148, places=3)
        self.assertEqual(caveats, [])

    def test_ontology_query_matches_exact_class_ignoring_case(self):
        candidates, _ = select_identity_candidates(self.records, ontologies=["tg"])
        self.assertEqual([c["spot_id"] for c in candidates], [1])

    def test_name_and_ontology_results_are_a_deduplicated_union_sorted_by_spot_id(self):
        candidates, _ = select_identity_candidates(
            self.records, names=["TG(15:0"], ontologies=["TG", "PC"],
        )
        self.assertEqual([c["spot_id"] for c in candidates], [0, 1])

    def test_no_query_raises(self):
        with self.assertRaisesRegex(ValueError, "names"):
            select_identity_candidates(self.records, names=[], ontologies=None)

    def test_too_many_candidates_are_pruned_by_height_with_a_caveat(self):
        records = [
            _record(index, f"TG(x{index})", "TG", 10.0, 800.0, height=float(index))
            for index in range(5)
        ]
        candidates, caveats = select_identity_candidates(
            records, ontologies=["TG"], max_candidates=2,
        )
        self.assertEqual([c["spot_id"] for c in candidates], [3, 4])
        self.assertEqual(len(caveats), 1)
        self.assertIn("HeightAverage", caveats[0])

    def test_default_candidate_limit_is_three_hundred(self):
        self.assertEqual(MAX_CANDIDATES, 300)


class VerifySpotMatchTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "spot_id": 2, "name": "Ceramide (d18:1/25:0)", "ontology": "Cer",
            "adduct": "[M+H]+", "rt": 22.148, "mz": 650.6, "height_average": 1.0,
        }

    def test_matching_spot_returns_none(self):
        spot = {"spot_id": 2, "rt": 22.150, "mz": 650.605}
        self.assertIsNone(verify_spot_match(self.candidate, spot))

    def test_rt_outside_tolerance_returns_rt_mismatch(self):
        spot = {"spot_id": 2, "rt": 22.5, "mz": 650.6}
        self.assertEqual(verify_spot_match(self.candidate, spot), "rt_mismatch")

    def test_mz_outside_tolerance_returns_mz_mismatch(self):
        spot = {"spot_id": 2, "rt": 22.148, "mz": 651.0}
        self.assertEqual(verify_spot_match(self.candidate, spot), "mz_mismatch")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_eic_identity_map.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'eic_identity_map'`）

- [ ] **Step 3: `eic_identity_map.py` を実装する**

```python
"""ARF2 の同定情報から EIC 描画対象スポットを選び、rt/mz で対応付けを検証する。

deps: arf2_reader のみ。EIC バイナリ読み出し・matplotlib・MCP には依存しない。
`spot_id = AlignmentID` の対応は MS-DIAL のバッチによって崩れうるため、
`verify_spot_match()` による rt/mz 照合を必須の安全弁として使うこと。
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

RT_TOLERANCE = 0.02   # min
MZ_TOLERANCE = 0.01   # Da
MAX_CANDIDATES = 300


class IdentityCandidate(TypedDict):
    spot_id: int
    name: str
    ontology: str
    adduct: str
    rt: float
    mz: float
    height_average: float


def load_arf2_records(arf2_path: str | Path) -> list[dict]:
    """.arf2 を読み、`extract_arf2_data` 形式の辞書リストを返す。"""
    from arf2_reader import deserialize

    with open(Path(arf2_path), "rb") as stream:
        return deserialize(stream)


def _matches(record: dict, name_queries: list[str], ontology_set: set[str]) -> bool:
    ontology = str(record.get("Ontology") or "")
    if ontology_set and ontology.casefold() in ontology_set:
        return True
    lowered = str(record.get("Name") or "").casefold()
    return any(query in lowered for query in name_queries)


def select_identity_candidates(
    records: list[dict],
    *,
    names: list[str] | None = None,
    ontologies: list[str] | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> tuple[list[IdentityCandidate], list[str]]:
    """クエリに一致する ARF2 スポット候補（spot_id 昇順）と caveat 文字列を返す。

    `names` は Name への大小無視の部分一致、`ontologies` は Ontology への大小無視の
    完全一致。両者の結果は AlignmentID で重複排除した和集合になる。
    """
    name_queries = [str(query).casefold() for query in (names or []) if str(query).strip()]
    ontology_set = {
        str(item).casefold() for item in (ontologies or []) if str(item).strip()
    }
    if not name_queries and not ontology_set:
        raise ValueError("names または ontologies のいずれかを指定してください。")

    candidates: list[IdentityCandidate] = []
    seen: set[int] = set()
    for record in records:
        spot_id = record.get("AlignmentID")
        if isinstance(spot_id, bool) or not isinstance(spot_id, int):
            continue
        if spot_id in seen or not _matches(record, name_queries, ontology_set):
            continue
        seen.add(spot_id)
        candidates.append({
            "spot_id": spot_id,
            "name": str(record.get("Name") or ""),
            "ontology": str(record.get("Ontology") or ""),
            "adduct": str(record.get("AdductType") or ""),
            "rt": float(record.get("RT") or 0.0),
            "mz": float(record.get("MassCenter") or 0.0),
            "height_average": float(record.get("HeightAverage") or 0.0),
        })

    caveats: list[str] = []
    if len(candidates) > max_candidates:
        total = len(candidates)
        candidates.sort(key=lambda item: item["height_average"], reverse=True)
        candidates = candidates[:max_candidates]
        caveats.append(
            f"クエリ一致 {total} 件のうち、ARF2 HeightAverage 上位 {max_candidates} 件だけを "
            "EIC 読み出し対象にしました（クエリを絞ると全件を評価できます）。"
        )

    candidates.sort(key=lambda item: item["spot_id"])
    return candidates, caveats


def verify_spot_match(
    candidate: IdentityCandidate,
    spot: dict,
    *,
    rt_tolerance: float = RT_TOLERANCE,
    mz_tolerance: float = MZ_TOLERANCE,
) -> str | None:
    """対応付けが妥当なら None、外れていれば除外理由の語を返す。"""
    if abs(float(spot["rt"]) - float(candidate["rt"])) > rt_tolerance:
        return "rt_mismatch"
    if abs(float(spot["mz"]) - float(candidate["mz"])) > mz_tolerance:
        return "mz_mismatch"
    return None
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_eic_identity_map.py -v`
Expected: PASS（9 件）

- [ ] **Step 5: コミット**

```bash
git add eic_identity_map.py tests/test_eic_identity_map.py
git commit -m "feat(eic): add ARF2 identity candidate selection with rt/mz verification"
```

---

### Task 3: ペイロードビルダー `build_multi_compound_plot_payload`

**Files:**
- Modify: `eic_plot.py:96-175`（共有ヘルパー抽出と新ビルダー追加）
- Test: `tests/test_eic_multi_plot.py`

**Interfaces:**
- Consumes: `eic_identity_map.IdentityCandidate` / `verify_spot_match`、Task 1 のスポット辞書
- Produces:
  - `EICMultiPlotPayload`（`TypedDict`）— `plot_schema` は `"lipidmix.eic.multi.v1"`
  - `build_multi_compound_plot_payload(candidates, spots, *, file_id, file_path, arf2_path, normalize="none", top_n=24, title=None, queries=None, ontologies=None, caveats=None) -> EICMultiPlotPayload`
  - 内部ヘルパー `_series_xy(sample, normalize) -> tuple[list[float], list[float]]`、`_apex_point(x_values, y_values, peak_top) -> tuple[float, float]`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_eic_multi_plot.py` を新規作成する:

```python
import unittest

from eic_plot import build_multi_compound_plot_payload


def _candidate(spot_id, name, ontology, rt, mz):
    return {
        "spot_id": spot_id, "name": name, "ontology": ontology,
        "adduct": "[M+H]+", "rt": rt, "mz": mz, "height_average": 1.0,
    }


def _spot(spot_id, rt, mz, file_id, points, peak_top, max_intensity):
    samples = []
    if file_id is not None:
        samples.append({
            "file_id": file_id,
            "peak_left": points[0][0],
            "peak_top": peak_top,
            "peak_right": points[-1][0],
            "num_points": len(points),
            "mean_intensity": sum(p[1] for p in points) / len(points),
            "max_intensity": max_intensity,
            "chromatogram": [list(point) for point in points],
        })
    return {
        "spot_id": spot_id, "rt": rt, "ri": 0.0, "mz": mz, "drift": -1.0,
        "main_type": 0, "num_samples": len(samples),
        "selected_samples": len(samples), "selected_points": len(points),
        "samples": samples,
    }


class MultiCompoundPayloadTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            _candidate(0, "PC(12:0/13:0)", "PC", 13.5, 636.4),
            _candidate(1, "Ceramide (d18:1/25:0)", "Cer", 22.1, 650.6),
        ]
        self.spots = [
            _spot(0, 13.5, 636.4, 7, [(13.4, 2.0), (13.5, 40.0), (13.6, 3.0)], 13.5, 40.0),
            _spot(1, 22.1, 650.6, 7, [(22.0, 5.0), (22.1, 90.0), (22.2, 4.0)], 22.1, 90.0),
        ]

    def _build(self, **overrides):
        candidates = overrides.pop("candidates", self.candidates)
        spots = overrides.pop("spots", self.spots)
        params = {
            "file_id": 7,
            "file_path": "alignment.EIC.aef",
            "arf2_path": "alignment.arf2",
            "queries": ["PC(12:0", "Ceramide"],
            "ontologies": [],
        }
        params.update(overrides)
        return build_multi_compound_plot_payload(candidates, spots, **params)

    def test_series_are_labelled_by_lipid_name_and_sorted_by_rt(self):
        payload = self._build()
        self.assertEqual(payload["plot_schema"], "lipidmix.eic.multi.v1")
        self.assertEqual(payload["plot_type"], "line")
        self.assertEqual(payload["sample"]["file_id"], 7)
        self.assertEqual(
            [item["label"] for item in payload["series"]],
            ["PC(12:0/13:0)", "Ceramide (d18:1/25:0)"],
        )
        self.assertEqual([item["spot_id"] for item in payload["series"]], [0, 1])
        self.assertEqual(payload["axes"]["x"]["unit"], "min")
        self.assertEqual(payload["axes"]["y"]["label"], "Intensity")

    def test_annotation_sits_on_the_apex_data_point(self):
        payload = self._build()
        annotation = payload["series"][1]["annotation"]
        self.assertEqual(annotation["text"], "Ceramide (d18:1/25:0) / 22.100")
        self.assertAlmostEqual(annotation["x"], 22.1, places=5)
        self.assertAlmostEqual(annotation["y"], 90.0, places=5)

    def test_annotation_y_follows_normalization(self):
        payload = self._build(normalize="per_trace_max")
        self.assertEqual(payload["axes"]["y"]["label"], "Relative intensity")
        self.assertAlmostEqual(payload["series"][1]["annotation"]["y"], 1.0, places=5)

    def test_rt_mismatch_is_dropped_with_reason(self):
        candidates = [self.candidates[0], _candidate(1, "Ceramide", "Cer", 30.0, 650.6)]
        payload = build_multi_compound_plot_payload(
            candidates, self.spots, file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )
        self.assertEqual([item["spot_id"] for item in payload["series"]], [0])
        self.assertEqual(
            payload["selection"]["dropped"],
            [{"spot_id": 1, "name": "Ceramide", "reason": "rt_mismatch"}],
        )
        self.assertTrue(any("rt_mismatch" in note for note in payload["caveats"]))

    def test_missing_spot_and_missing_file_id_are_distinguished(self):
        candidates = self.candidates + [_candidate(2, "TG(x)", "TG", 25.0, 800.0)]
        spots = self.spots + [_spot(2, 25.0, 800.0, None, [(25.0, 1.0)], 25.0, 1.0)]
        payload = build_multi_compound_plot_payload(
            candidates + [_candidate(9, "DG(y)", "DG", 9.0, 500.0)],
            spots, file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )
        reasons = {item["spot_id"]: item["reason"] for item in payload["selection"]["dropped"]}
        self.assertEqual(reasons[2], "file_id_absent")
        self.assertEqual(reasons[9], "spot_out_of_range")

    def test_top_n_keeps_strongest_and_records_the_rest(self):
        payload = self._build(top_n=1)
        self.assertEqual([item["spot_id"] for item in payload["series"]], [1])
        self.assertEqual(
            payload["selection"]["dropped"],
            [{"spot_id": 0, "name": "PC(12:0/13:0)", "reason": "below_top_n"}],
        )
        self.assertEqual(payload["selection"]["plotted"], 1)
        self.assertEqual(payload["selection"]["candidates"], 2)
        self.assertEqual(payload["selection"]["top_n"], 1)

    def test_unknown_name_falls_back_to_spot_and_mz_label(self):
        candidates = [_candidate(0, "Unknown", "", 13.5, 636.4)]
        payload = build_multi_compound_plot_payload(
            candidates, self.spots[:1], file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )
        self.assertEqual(payload["series"][0]["label"], "spot 0 (m/z 636.4000)")

    def test_no_verified_candidate_raises(self):
        candidates = [_candidate(0, "PC", "PC", 99.0, 636.4)]
        with self.assertRaisesRegex(ValueError, "rt_mismatch"):
            build_multi_compound_plot_payload(
                candidates, self.spots[:1], file_id=7,
                file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
            )

    def test_invalid_arguments_raise(self):
        with self.assertRaisesRegex(ValueError, "normalize"):
            self._build(normalize="zscore")
        with self.assertRaisesRegex(ValueError, "top_n"):
            self._build(top_n=0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_eic_multi_plot.py -v`
Expected: FAIL（`ImportError: cannot import name 'build_multi_compound_plot_payload'`）

- [ ] **Step 3: 共有ヘルパーを抽出する**

`eic_plot.py` の `_sample_metadata` の直後に追加する:

```python
def _series_xy(sample: dict, normalize: str) -> tuple[list[float], list[float]]:
    """クロマトグラム点列を x/y 配列へ分解し、必要なら trace 内最大で正規化する。"""
    x_values = [float(point[0]) for point in sample["chromatogram"]]
    raw_y = [float(point[1]) for point in sample["chromatogram"]]
    if normalize == "per_trace_max":
        denominator = max(raw_y, default=0.0)
        return x_values, [
            value / denominator if denominator > 0 else 0.0 for value in raw_y
        ]
    return x_values, raw_y


def _apex_point(
    x_values: list[float], y_values: list[float], peak_top: float,
) -> tuple[float, float]:
    """peak_top に最も近いデータ点の (x, y) を返す。点列が空なら (peak_top, 0.0)。"""
    if not x_values:
        return float(peak_top), 0.0
    index = min(
        range(len(x_values)), key=lambda position: abs(x_values[position] - peak_top)
    )
    return x_values[index], y_values[index]
```

`build_eic_plot_payload` の中の x/y 生成部（`x_values = [...]` から `y_values = raw_y` までの 7 行）を次の 1 行に置き換える:

```python
        x_values, y_values = _series_xy(sample, normalize)
```

- [ ] **Step 4: 既存テストが壊れていないことを確認する**

Run: `python -m pytest tests/test_eic_plot.py -v`
Expected: PASS（Task 1 までの全件）

- [ ] **Step 5: 新スキーマの型と定数を追加する**

`eic_plot.py` の `EICPlotPayload` 定義の直後に追加する:

```python
MULTI_PLOT_SCHEMA = "lipidmix.eic.multi.v1"


class PlotAnnotation(TypedDict):
    text: str
    x: float
    y: float


class PlotSample(TypedDict):
    file_id: int
    sample_name: str | None
    class_id: str | None


class MultiPlotSeries(TypedDict):
    id: str
    label: str
    spot_id: int
    name: str
    ontology: str
    adduct: str
    mz: float
    rt: float
    x: list[float]
    y: list[float]
    peak_left: float
    peak_top: float
    peak_right: float
    max_intensity: float
    mean_intensity: float
    point_count: int
    annotation: PlotAnnotation


class DroppedCompound(TypedDict):
    spot_id: int
    name: str
    reason: str


class PlotSelection(TypedDict):
    queries: list[str]
    ontologies: list[str]
    candidates: int
    plotted: int
    top_n: int
    dropped: list[DroppedCompound]


class MultiRenderHints(TypedDict):
    mode: str
    connect_points: bool
    show_legend: bool
    show_annotations: bool
    hover_fields: list[str]


class EICMultiPlotPayload(TypedDict):
    plot_schema: str
    plot_type: str
    title: str
    source: PlotSource
    axes: PlotAxes
    sample: PlotSample
    normalization: str
    series: list[MultiPlotSeries]
    selection: PlotSelection
    render_hints: MultiRenderHints
    caveats: list[str]
```

`PlotSource` に ARF2 のパスを足すため、`PlotSource` 定義を次に差し替える（既存 v1 は `arf2_file` を含めないので `total=False` を使う）:

```python
class PlotSource(TypedDict, total=False):
    file: str
    file_name: str
    arf2_file: str
```

- [ ] **Step 6: ビルダーを実装する**

`eic_plot.py` の `render_eic_plot` の直前に追加する。冒頭の import に
`from eic_identity_map import IdentityCandidate, verify_spot_match` を足す:

```python
def _dropped(candidate: IdentityCandidate, reason: str) -> DroppedCompound:
    return {
        "spot_id": int(candidate["spot_id"]),
        "name": str(candidate["name"]),
        "reason": reason,
    }


def build_multi_compound_plot_payload(
    candidates: list[IdentityCandidate],
    spots: list[dict],
    *,
    file_id: int,
    file_path: str | Path,
    arf2_path: str | Path,
    normalize: str = "none",
    top_n: int = 24,
    title: str | None = None,
    queries: list[str] | None = None,
    ontologies: list[str] | None = None,
    caveats: list[str] | None = None,
) -> EICMultiPlotPayload:
    """1 サンプル分の複数物質オーバーレイ用ペイロードを組み立てる。

    `candidates` は ARF2 由来の同定候補、`spots` は同じ `spot_id` を要求して読んだ
    EIC スポット。rt/mz 検証、`top_n` での強度打ち切り、RT 昇順の並べ替えを行い、
    除外された物質は理由付きで `selection.dropped` に残す。
    """
    if normalize not in {"none", "per_trace_max"}:
        raise ValueError("normalize must be 'none' or 'per_trace_max'")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")

    path = Path(file_path).resolve()
    arf2 = Path(arf2_path).resolve()
    metadata, meta_caveats = _sample_metadata(path)
    notes = list(caveats or []) + meta_caveats
    dropped: list[DroppedCompound] = []

    spot_by_id = {int(spot["spot_id"]): spot for spot in spots}
    accepted = []
    for candidate in candidates:
        spot = spot_by_id.get(int(candidate["spot_id"]))
        if spot is None:
            dropped.append(_dropped(candidate, "spot_out_of_range"))
            continue
        sample = next(
            (item for item in spot["samples"] if int(item["file_id"]) == int(file_id)),
            None,
        )
        if sample is None:
            dropped.append(_dropped(candidate, "file_id_absent"))
            continue
        reason = verify_spot_match(candidate, spot)
        if reason:
            dropped.append(_dropped(candidate, reason))
            continue
        accepted.append((candidate, spot, sample))

    accepted.sort(key=lambda item: float(item[2]["max_intensity"]), reverse=True)
    for candidate, _spot, _sample in accepted[top_n:]:
        dropped.append(_dropped(candidate, "below_top_n"))
    accepted = accepted[:top_n]
    accepted.sort(key=lambda item: float(item[1]["rt"]))

    if not accepted:
        breakdown = ", ".join(
            f"{item['name'] or item['spot_id']}={item['reason']}" for item in dropped
        )
        raise ValueError(
            f"検証を通過した物質がありません（候補 {len(candidates)} 件、除外内訳: {breakdown}）。"
            "クエリ、file_id、または対象バッチを見直してください。"
        )

    series: list[MultiPlotSeries] = []
    main_types: list[int] = []
    for candidate, spot, sample in accepted:
        x_values, y_values = _series_xy(sample, normalize)
        label = str(candidate["name"] or "")
        if not label or label.casefold() == "unknown":
            label = f"spot {int(candidate['spot_id'])} (m/z {float(spot['mz']):.4f})"
        apex_x, apex_y = _apex_point(x_values, y_values, float(sample["peak_top"]))
        main_types.append(int(spot["main_type"]))
        series.append({
            "id": f"spot-{int(candidate['spot_id'])}",
            "label": label,
            "spot_id": int(candidate["spot_id"]),
            "name": str(candidate["name"]),
            "ontology": str(candidate["ontology"]),
            "adduct": str(candidate["adduct"]),
            "mz": float(spot["mz"]),
            "rt": float(spot["rt"]),
            "x": x_values,
            "y": y_values,
            "peak_left": float(sample["peak_left"]),
            "peak_top": float(sample["peak_top"]),
            "peak_right": float(sample["peak_right"]),
            "max_intensity": float(sample["max_intensity"]),
            "mean_intensity": float(sample["mean_intensity"]),
            "point_count": int(sample["num_points"]),
            "annotation": {
                "text": f"{label} / {float(sample['peak_top']):.3f}",
                "x": apex_x,
                "y": apex_y,
            },
        })

    for item in dropped:
        notes.append(
            f"spot_id={item['spot_id']} ({item['name'] or 'Unknown'}) を描画から除外: "
            f"{item['reason']}"
        )

    main_type = max(set(main_types), key=main_types.count)
    if len(set(main_types)) > 1:
        notes.append(
            f"選択スポットの main_type が混在しています（採用: {main_type}）。"
        )
    x_label, x_unit = _axis_definition(main_type)
    y_label = "Relative intensity" if normalize == "per_trace_max" else "Intensity"
    record = metadata.get(int(file_id), {})
    sample_name = record.get("file_name")
    class_id = record.get("class_id")
    sample_label = str(sample_name) if sample_name else f"FileID {int(file_id)}"
    plot_title = title or f"EIC overlay | {len(series)} compounds | {sample_label}"

    return {
        "plot_schema": MULTI_PLOT_SCHEMA,
        "plot_type": "line",
        "title": plot_title,
        "source": {
            "file": str(path), "file_name": path.name, "arf2_file": str(arf2),
        },
        "axes": {
            "x": {"label": x_label, "unit": x_unit, "scale": "linear"},
            "y": {"label": y_label, "unit": None, "scale": "linear"},
        },
        "sample": {
            "file_id": int(file_id),
            "sample_name": str(sample_name) if sample_name else None,
            "class_id": str(class_id) if class_id else None,
        },
        "normalization": normalize,
        "series": series,
        "selection": {
            "queries": [str(item) for item in (queries or [])],
            "ontologies": [str(item) for item in (ontologies or [])],
            "candidates": len(candidates),
            "plotted": len(series),
            "top_n": top_n,
            "dropped": dropped,
        },
        "render_hints": {
            "mode": "lines",
            "connect_points": True,
            "show_legend": True,
            "show_annotations": True,
            "hover_fields": [
                "label", "spot_id", "ontology", "adduct", "mz", "rt",
                "peak_left", "peak_top", "peak_right", "max_intensity",
            ],
        },
        "caveats": notes,
    }
```

- [ ] **Step 7: テストが通ることを確認する**

Run: `python -m pytest tests/test_eic_multi_plot.py tests/test_eic_plot.py -v`
Expected: PASS（新規 9 件＋既存すべて）

- [ ] **Step 8: コミット**

```bash
git add eic_plot.py tests/test_eic_multi_plot.py
git commit -m "feat(eic): build lipidmix.eic.multi.v1 overlay payload"
```

---

### Task 4: multi スキーマの matplotlib 描画

**Files:**
- Modify: `eic_plot.py:178-205`（`render_eic_plot` を分岐化し `_render_multi_compound` を追加）
- Test: `tests/test_eic_multi_plot.py`（描画テストクラスを追加）

**Interfaces:**
- Consumes: Task 3 の `EICMultiPlotPayload` と既存 `EICPlotPayload`
- Produces: `render_eic_plot(payload, title=None) -> matplotlib.figure.Figure`（両スキーマ対応、未知スキーマは `ValueError`）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_eic_multi_plot.py` の冒頭 import を差し替える:

```python
import unittest

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from eic_plot import build_multi_compound_plot_payload, render_eic_plot
```

ファイル末尾の `if __name__ == "__main__":` の前に追加する:

```python
class MultiCompoundRenderTests(unittest.TestCase):
    def setUp(self):
        candidates = [
            _candidate(0, "PC(12:0/13:0)", "PC", 13.5, 636.4),
            _candidate(1, "Ceramide (d18:1/25:0)", "Cer", 22.1, 650.6),
        ]
        spots = [
            _spot(0, 13.5, 636.4, 7, [(13.4, 2.0), (13.5, 40.0), (13.6, 3.0)], 13.5, 40.0),
            _spot(1, 22.1, 650.6, 7, [(22.0, 5.0), (22.1, 90.0), (22.2, 4.0)], 22.1, 90.0),
        ]
        self.payload = build_multi_compound_plot_payload(
            candidates, spots, file_id=7,
            file_path="alignment.EIC.aef", arf2_path="alignment.arf2",
        )

    def test_renders_one_line_and_one_annotation_per_series(self):
        fig = render_eic_plot(self.payload)
        try:
            ax = fig.axes[0]
            self.assertEqual(len(ax.get_lines()), 2)
            texts = [annotation.get_text() for annotation in ax.texts]
            self.assertIn("Ceramide (d18:1/25:0) / 22.100", texts)
            self.assertEqual(ax.get_xlabel(), "RT (min)")
        finally:
            plt.close(fig)

    def test_annotations_can_be_switched_off(self):
        self.payload["render_hints"]["show_annotations"] = False
        fig = render_eic_plot(self.payload)
        try:
            self.assertEqual(fig.axes[0].texts, [])
        finally:
            plt.close(fig)

    def test_unknown_schema_raises(self):
        with self.assertRaisesRegex(ValueError, "schema"):
            render_eic_plot({"plot_schema": "lipidmix.eic.v99", "series": []})
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_eic_multi_plot.py::MultiCompoundRenderTests -v`
Expected: FAIL（`render_eic_plot` が multi ペイロードの `payload["series"][i]["x"]` は描けるが `ax.texts` が空、および未知スキーマで `KeyError`）

- [ ] **Step 3: 描画を分岐化する**

`eic_plot.py` の既存 `render_eic_plot`（178 行目以降）を、次の 3 関数に置き換える:

```python
def render_eic_plot(payload, title: str | None = None):
    """Render an approved EIC payload to a matplotlib figure for explicit saving."""
    schema = payload.get("plot_schema")
    if schema == MULTI_PLOT_SCHEMA:
        return _render_multi_compound(payload, title)
    if schema == "lipidmix.eic.v1":
        return _render_single_spot(payload, title)
    raise ValueError(f"Unsupported EIC plot schema: {schema!r}")


def _render_single_spot(payload: EICPlotPayload, title: str | None = None):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 6))
    for item in payload["series"]:
        line = ax.plot(item["x"], item["y"], label=item["label"], linewidth=1.4)[0]
        if item["x"]:
            apex_x, apex_y = _apex_point(item["x"], item["y"], item["peak_top"])
            ax.scatter([apex_x], [apex_y], color=line.get_color(), s=20, zorder=3)
    if len(payload["series"]) == 1:
        item = payload["series"][0]
        ax.axvspan(item["peak_left"], item["peak_right"], color="#999999", alpha=0.12)
    _apply_axes(ax, payload, title)
    ax.legend(fontsize=8, loc="best")
    return fig


def _render_multi_compound(payload: EICMultiPlotPayload, title: str | None = None):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 6))
    show_annotations = payload["render_hints"].get("show_annotations", True)
    for item in payload["series"]:
        line = ax.plot(item["x"], item["y"], label=item["label"], linewidth=1.2)[0]
        annotation = item.get("annotation")
        if not annotation:
            continue
        ax.scatter(
            [annotation["x"]], [annotation["y"]],
            color=line.get_color(), s=16, zorder=3,
        )
        if show_annotations:
            ax.annotate(
                annotation["text"],
                xy=(annotation["x"], annotation["y"]),
                xytext=(0, 6),
                textcoords="offset points",
                fontsize=6,
                rotation=45,
                ha="left",
                va="bottom",
            )
    _apply_axes(ax, payload, title)
    ax.legend(fontsize=6, loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0)
    return fig


def _apply_axes(ax, payload, title: str | None) -> None:
    x_axis = payload["axes"]["x"]
    y_axis = payload["axes"]["y"]
    x_label = x_axis["label"] + (f" ({x_axis['unit']})" if x_axis["unit"] else "")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_axis["label"])
    ax.set_title(title or payload["title"])
    ax.grid(alpha=0.2)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_eic_multi_plot.py tests/test_eic_plot.py -v`
Expected: PASS（`test_png_is_written_only_by_explicit_save_tool` を含む既存 v1 経路も緑）

- [ ] **Step 5: コミット**

```bash
git add eic_plot.py tests/test_eic_multi_plot.py
git commit -m "feat(eic): render multi-compound overlay figures with apex annotations"
```

---

### Task 5: MCP ツール `eic_plot_compounds` と文書

**Files:**
- Modify: `tools_eic.py:19-27`（import と `__all__`）、`tools_eic.py:88` の直後（新ツール）
- Modify: `tests/test_server_registration.py:18-56, 74`（ツール名スナップショットと件数）
- Modify: `docs/output_format/eic.md:3, 83`（表題と 8.6 節）
- Modify: `USAGE.md:50-57`、`README.md:196`
- Test: `tests/test_eic_multi_plot.py`（ツール経路のテストクラスを追加）

**Interfaces:**
- Consumes: `eic_aef_reader.read_eic_spots_css1`、`eic_identity_map.load_arf2_records` / `select_identity_candidates`、`eic_plot.build_multi_compound_plot_payload` / `EICMultiPlotPayload`、`path_resolvers.resolve_eicaef_file_path` / `resolve_arf2_file_path`
- Produces: MCP ツール `eic_plot_compounds(file_id, names=None, ontologies=None, file_path=None, arf2_path=None, normalize="none", top_n=24, title=None) -> EICMultiPlotPayload`。生成したペイロードを `session_state.session.last_eic_plot` に格納する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_eic_multi_plot.py` のファイル末尾（`if __name__ == "__main__":` の前）に追加する:

```python
class EicPlotCompoundsToolTests(unittest.TestCase):
    def setUp(self):
        import os
        import struct
        import tempfile
        from pathlib import Path

        import mcp_core
        import session_state

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "alignment.EIC.aef"
        self.arf2 = self.tmp / "alignment.arf2"
        self.arf2.write_bytes(b"placeholder")

        from tests.test_eic_plot import _write_css1

        _write_css1(self.path, [
            {"rt": 13.5, "mz": 636.4, "samples": [{
                "file_id": 7, "peak_top": 13.5, "peak_left": 13.4,
                "peak_right": 13.6,
                "points": [(13.4, 2.0), (13.5, 40.0), (13.6, 3.0)],
            }]},
            {"rt": 22.1, "mz": 650.6, "samples": [{
                "file_id": 7, "peak_top": 22.1, "peak_left": 22.0,
                "peak_right": 22.2,
                "points": [(22.0, 5.0), (22.1, 90.0), (22.2, 4.0)],
            }]},
        ])

        self.records = [
            {"AlignmentID": 0, "Name": "PC(12:0/13:0)", "Ontology": "PC",
             "AdductType": "[M+H]+", "RT": 13.5, "MassCenter": 636.4,
             "HeightAverage": 10.0},
            {"AlignmentID": 1, "Name": "Ceramide (d18:1/25:0)", "Ontology": "Cer",
             "AdductType": "[M+H]+", "RT": 22.1, "MassCenter": 650.6,
             "HeightAverage": 20.0},
        ]

        self._saved_data_dir = mcp_core.DATA_DIR
        self._saved_reports = os.environ.get("LIPIDMIX_REPORTS_DIR")
        self._saved_plot = session_state.session.last_eic_plot
        mcp_core.DATA_DIR = self.tmp
        os.environ["LIPIDMIX_REPORTS_DIR"] = str(self.tmp / "fallback")
        session_state.session.last_eic_plot = None

        import tools_eic

        self._saved_loader = tools_eic.load_arf2_records
        tools_eic.load_arf2_records = lambda _path: self.records

    def tearDown(self):
        import os

        import mcp_core
        import session_state
        import tools_eic

        tools_eic.load_arf2_records = self._saved_loader
        mcp_core.DATA_DIR = self._saved_data_dir
        session_state.session.last_eic_plot = self._saved_plot
        if self._saved_reports is None:
            os.environ.pop("LIPIDMIX_REPORTS_DIR", None)
        else:
            os.environ["LIPIDMIX_REPORTS_DIR"] = self._saved_reports
        self._tmp.cleanup()

    def test_returns_multi_payload_and_writes_no_png(self):
        import server
        import session_state

        payload = server.eic_plot_compounds(
            7, names=["ceramide"], ontologies=["PC"],
            file_path=str(self.path), arf2_path=str(self.arf2),
        )
        self.assertEqual(payload["plot_schema"], "lipidmix.eic.multi.v1")
        self.assertEqual(
            [item["label"] for item in payload["series"]],
            ["PC(12:0/13:0)", "Ceramide (d18:1/25:0)"],
        )
        self.assertEqual(payload["sample"]["file_id"], 7)
        self.assertIs(session_state.session.last_eic_plot, payload)
        self.assertEqual(list(self.tmp.rglob("*.png")), [])

    def test_no_query_raises(self):
        import server

        with self.assertRaisesRegex(ValueError, "names"):
            server.eic_plot_compounds(
                7, file_path=str(self.path), arf2_path=str(self.arf2),
            )

    def test_query_without_any_arf2_hit_raises(self):
        import server

        with self.assertRaisesRegex(ValueError, "一致"):
            server.eic_plot_compounds(
                7, names=["no-such-lipid"],
                file_path=str(self.path), arf2_path=str(self.arf2),
            )

    def test_save_eic_figure_accepts_the_multi_payload(self):
        import server

        server.eic_plot_compounds(
            7, ontologies=["PC", "Cer"],
            file_path=str(self.path), arf2_path=str(self.arf2),
        )
        message = server.save_eic_figure("overlay-eic")
        png = self.tmp / "reports" / "figures" / "overlay-eic_eic.png"
        self.assertTrue(png.is_file())
        self.assertIn("figures/overlay-eic_eic.png", message)

    def test_fastmcp_exposes_output_schema(self):
        import asyncio

        import server

        tools = asyncio.run(server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "eic_plot_compounds")
        self.assertIsNotNone(tool.outputSchema)
        self.assertIn("plot_schema", tool.outputSchema.get("properties", {}))
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_eic_multi_plot.py::EicPlotCompoundsToolTests -v`
Expected: FAIL（`AttributeError: module 'tools_eic' has no attribute 'load_arf2_records'`）

- [ ] **Step 3: ツールを実装する**

`tools_eic.py` の import 群（19 行目付近）を差し替える:

```python
from path_resolvers import resolve_arf2_file_path, resolve_eicaef_file_path
from eic_aef_reader import (
    read_eic_spot_css1,
    read_eic_spots_css1,
    summarize_eic_data,
    top_eic_spots_by_max_intensity,
    search_eic_by_mz_range,
    search_eic_by_rt_range,
)
from eic_identity_map import load_arf2_records, select_identity_candidates
from eic_plot import (
    EICMultiPlotPayload,
    EICPlotPayload,
    build_eic_plot_payload,
    build_multi_compound_plot_payload,
)
```

既存の `from path_resolvers import resolve_eicaef_file_path` 行は上の 1 行で置き換わるため削除する。

`__all__` に `"eic_plot_compounds"` を追加する:

```python
__all__ = [
    "eic_parser",
    "eic_plot_chromatograms",
    "eic_plot_compounds",
    "eic_rank_by_max_intensity",
    "eic_search_by_mz_range",
    "eic_search_by_rt_range",
]
```

`eic_plot_chromatograms` の直後に新ツールを追加する:

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
    """Overlay several identified compounds' EIC traces for ONE sample.

    Read-only. Returns structured ``lipidmix.eic.multi.v1`` JSON only; it renders
    no image and writes no file. Call ``save_eic_figure`` only after the user
    explicitly requests PNG output.

    One call plots one sample: ``file_id`` is required. To compare samples, call
    this tool once per sample and show the figures side by side.

    Compounds are selected from the ARF2 annotation: ``names`` matches ARF2
    ``Name`` as a case-insensitive substring and ``ontologies`` matches ARF2
    ``Ontology`` exactly (case-insensitive); the two results are unioned. At
    least one of them is required.

    ``spot_id = AlignmentID`` is verified against the EIC spot RT and m/z, so a
    compound whose identity cannot be confirmed is not drawn. Everything that was
    excluded — mismatch, missing trace, or falling outside ``top_n`` — is listed
    with its reason in ``selection.dropped``; read it before concluding that a
    compound is absent from the sample.
    """
    if isinstance(file_id, bool) or not isinstance(file_id, int):
        raise ValueError("file_id must be an integer")
    if normalize not in {"none", "per_trace_max"}:
        raise ValueError("normalize must be 'none' or 'per_trace_max'")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")

    resolved = resolve_eicaef_file_path(file_path)
    if not resolved:
        raise FileNotFoundError("データディレクトリに .aef ファイルが見つかりませんでした。")
    resolved_arf2 = resolve_arf2_file_path(arf2_path)
    if not resolved_arf2:
        raise FileNotFoundError("データディレクトリに .arf2 ファイルが見つかりませんでした。")

    records = load_arf2_records(resolved_arf2)
    candidates, caveats = select_identity_candidates(
        records, names=names, ontologies=ontologies,
    )
    if not candidates:
        raise ValueError(
            f"names={names} / ontologies={ontologies} に一致する ARF2 スポットがありません。"
            "arf2_parser で脂質名・オントロジーの表記を確認してください。"
        )

    spots = read_eic_spots_css1(
        resolved,
        [candidate["spot_id"] for candidate in candidates],
        file_ids=[file_id],
    )
    payload = build_multi_compound_plot_payload(
        candidates,
        spots,
        file_id=file_id,
        file_path=resolved,
        arf2_path=resolved_arf2,
        normalize=normalize,
        top_n=top_n,
        title=title,
        queries=names,
        ontologies=ontologies,
        caveats=caveats,
    )
    session_state.session.last_eic_plot = payload
    return payload
```

- [ ] **Step 4: 登録スナップショットを更新する**

`tests/test_server_registration.py` の `EXPECTED_TOOLS` に `"eic_plot_compounds"` を
`"eic_plot_chromatograms"` の次の行として追加し、件数アサーションを更新する:

```python
def test_tool_count_is_stable():
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 38
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `python -m pytest tests/ -v`
Expected: PASS（全件。`test_tool_count_is_stable` と `test_tool_names_snapshot` を含む）

- [ ] **Step 6: ドキュメントを更新する**

`docs/output_format/eic.md` の 3 行目を差し替える:

```markdown
`.EIC.aef` パーサ、EIC 検索・ランキング、`eic_plot_chromatograms` / `eic_plot_compounds` の描画契約。
```

同ファイルの `### 8.5 ...` の節の直後（`DCLパーサーは…` の行の前）に追加する:

```markdown
### 8.6 `eic_plot_compounds()` の描画契約

`eic_plot_compounds()` は**複数物質 × 1サンプル**のオーバーレイ用に、クライアント中立の
構造化JSON `plot_schema="lipidmix.eic.multi.v1"` を返す。8.5節と同じく画像は作らない。

- **1呼び出し1サンプル**。`file_id` は必須。複数サンプルを比べるときはサンプルごとに
  呼び出し、図を並べる。1物質×全サンプルの比較は従来どおり `eic_plot_chromatograms`。
- 物質は ARF2 の同定情報から選ぶ。`names` は `Name` への大小無視の部分一致、
  `ontologies` は `Ontology` への大小無視の完全一致で、両者は和集合。
- 単一の `spot` フィールドは持たない。代わりに `sample`（`file_id` / `sample_name` /
  `class_id`）を持ち、スポット固有の情報は `series[]` の `spot_id` / `name` /
  `ontology` / `adduct` / `mz` / `rt` に入る。`series` はRT昇順。
- 各系列の `annotation` は `{text: "<脂質名> / <peak_top>", x, y}`。`x`/`y` はピーク頂点に
  最も近いデータ点の座標で、`y` は正規化後の値である。
- **`selection.dropped[]` を必ず読むこと。** `spot_id`（= ARF2 `AlignmentID`）で引いた
  EICスポットは `rt`/`mz` の一致（±0.02 min / ±0.01 Da）を検証しており、通らなかった
  物質は描画されない。除外理由は `rt_mismatch` / `mz_mismatch`（ID対応の崩れ）、
  `spot_out_of_range`、`file_id_absent`（その試料にトレースが無い）、
  `below_top_n`（`top_n` 件からあふれた）の5種。**図に無い＝試料に無い、ではない。**
- `selection.candidates` はクエリ一致数、`selection.plotted` は実際に描画した数。
  一致が300件を超えるとARF2 `HeightAverage` 上位300件に予備選抜され、`caveats` に残る。

PNGが必要だとユーザーが明示した場合に限り `save_eic_figure(analysis_id, title=None)` で
保存する。この保存ツールは `lipidmix.eic.v1` と `lipidmix.eic.multi.v1` の両方に対応する。
```

`USAGE.md` の 50 行目の直後に追加する:

```markdown
| `eic_plot_compounds` | 脂質名/オントロジーで選んだ複数物質のEICを、指定 `file_id` の**1試料分だけ**同一グラフへ重ねる構造化プロット情報(`lipidmix.eic.multi.v1`)を返す。ARF2の同定をrt/mzで検証し、除外した物質は `selection.dropped` に理由付きで残す。画像生成・ファイル保存は行わない。 |
```

`USAGE.md` の「### EICの描画フロー」（52-57 行目）の末尾、`4.` の行の後に追加する:

```markdown

複数物質を1枚に重ねる場合は `eic_plot_compounds(file_id, names=[...], ontologies=[...])` を使う。
1呼び出し1試料なので、試料間で比べるときは試料ごとに呼び出して図を並べる。図に現れない物質は
`selection.dropped` の理由（`rt_mismatch` / `file_id_absent` / `below_top_n` など）を確認する。
```

`README.md` の 196 行目の直後に追加する:

```markdown
- `eic_plot_compounds(file_id, names=None, ontologies=None, file_path=None, arf2_path=None, normalize="none", top_n=24, title=None)` - Overlay several identified compounds' EIC traces for ONE sample and return structured `lipidmix.eic.multi.v1` plot information. Compounds are selected from ARF2 `Name` (case-insensitive substring) and `Ontology` (exact), and each `AlignmentID` is verified against the EIC spot RT and m/z; anything excluded is listed with a reason in `selection.dropped`. The tool renders no image and writes no file.
```

`tests/test_output_format_sections.py:29` の `REQUIRED_MARKERS["eic"]` に新スキーマ名を
足し、分割・改訂で multi 契約が失われないよう固定する:

```python
    "eic": ["peak_top", "lipidmix.eic.v1", "lipidmix.eic.multi.v1"],
```

- [ ] **Step 7: 全テストを再実行する**

Run: `python -m pytest tests/ -v`
Expected: PASS（全件）

- [ ] **Step 8: コミット**

```bash
git add tools_eic.py tests/test_eic_multi_plot.py tests/test_server_registration.py tests/test_output_format_sections.py docs/output_format/eic.md USAGE.md README.md
git commit -m "feat(eic): add eic_plot_compounds multi-compound overlay tool"
```

---

## Self-Review

**Spec coverage:**

| Spec 節 | 対応タスク |
|---|---|
| §3 公開インターフェース | Task 5 Step 3 |
| §4 候補選択・予備選抜・rt/mz 検証 | Task 2（選択・検証）、Task 3 Step 6（適用・`dropped`） |
| §4 手順 4-5（top_n 打ち切り・RT 昇順） | Task 3 Step 6 |
| §5 ペイロード | Task 3 Step 5-6 |
| §6 バッチリーダー・strict 委譲 | Task 1 |
| §7 描画分岐・`save_eic_figure` | Task 4、Task 5 Step 1（保存テスト） |
| §8 エラー処理表 | Task 5 Step 3（ツール入口）、Task 3 Step 6（`normalize`/`top_n`/検証 0 件）、Task 2 Step 3（クエリ未指定） |
| §9 テスト 1-9 | Task 2 Step 1（1）、Task 3 Step 1（2,3,4）、Task 5 Step 1（5,6,9）、Task 1 Step 1（7,8） |
| §10 ドキュメント | Task 5 Step 6 |

**Type consistency:** `spot_id` / `file_id` / `top_n` / `dropped[{spot_id,name,reason}]` /
`annotation{text,x,y}` / `MULTI_PLOT_SCHEMA` の名称は Task 1→5 で一致。除外理由の語彙 5 種は
Task 3 の実装、Task 3・5 のテスト、Task 5 のドキュメントで同一。`_series_xy` / `_apex_point` /
`_apply_axes` は Task 3 で定義し Task 4 で使用。`load_arf2_records` は Task 2 で定義し、
Task 5 で `tools_eic` の名前空間へ import してテストが差し替える。
