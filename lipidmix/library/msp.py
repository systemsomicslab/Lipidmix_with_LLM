"""`.msp`（テキスト）参照ライブラリを読む。

別名表は上流 `MspFileParcer.cs`（`MsdialWorkbench` リポジトリ、
`src/Common/CommonStandard/Parser/MspFileParcer.cs` の `SetMspField` 内
`switch (fieldName.ToLower())`）の分岐を写している。`case "DB#":` は
switch のキーが小文字化された後に比較されるため到達不能な死んだ枝なので
写していない。

`.dbs`（`lipidmix/library/dbs.py`）と同じ正規化レコード（`record.make_record`）
を返す。`spectrum` は `[[mz, intensity], ...]`、`ion_mode` は
`"positive"` / `"negative"` の文字列、値が無いフィールドは `None`。

deps: record のみ。mcp_core / session_state / tools_* / dbs を import しない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from lipidmix.library.record import make_record

#: 小文字化したフィールド名 → 正規化レコードのキー。
#: `None` は「上流では意味を持つが正規化レコードには対応する枠が無いので読み捨てる」
#: （`comment` / `comments` が該当）。ここに無いフィールド名も同様に読み捨てる。
_SPECTRUM_MARKER = "__spectrum__"

_FIELD_ALIASES: dict[str, str | None] = {
    "precursormz": "precursor_mz",
    "precursor_mz": "precursor_mz",
    "precursor_m/z": "precursor_mz",
    "precursortype": "adduct",
    "precursor_type": "adduct",
    "ionmode": "ion_mode",
    "ion_mode": "ion_mode",
    "formula": "formula",
    "inchikey": "inchikey",
    "inchi_key": "inchikey",
    "inchi key": "inchikey",
    "smiles": "smiles",
    "ontology": "ontology",
    "compoundclass": "compound_class",
    "retentiontime": "rt",
    "retention_time": "rt",
    "rt": "rt",
    "num peaks": _SPECTRUM_MARKER,
    "numpeaks": _SPECTRUM_MARKER,
    "num_peaks": _SPECTRUM_MARKER,
    "comment": None,
    "comments": None,
}

#: `float` へ変換するべき正規化レコードのキー。
_FLOAT_FIELDS = {"precursor_mz", "rt"}


def _parse_field_line(line: str) -> tuple[str | None, str | None]:
    """`フィールド名: 値` を分ける。`:` が無ければ `(None, None)`。"""
    if ":" not in line:
        return None, None
    name, value = line.split(":", 1)
    return name.strip(), value.strip()


def _parse_ion_mode(value: str) -> str | None:
    normalized = value.strip().lower()
    return normalized if normalized in ("positive", "negative") else None


def _read_peaks(lines: list[str], start: int) -> tuple[list[list[float]], int]:
    """`Num Peaks:` の次の行からピークを読む。

    宣言された本数は信用しない（実際がそれより少ないファイルがある）。
    空行・次のレコード開始（`name:` 行）・ピークとして解釈できない行の
    どれかに当たるまで読み進める。
    """
    peaks: list[list[float]] = []
    i = start
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            break
        field_name, _ = _parse_field_line(stripped)
        if field_name is not None and field_name.lower() == "name":
            break
        parts = stripped.split()
        if len(parts) < 2:
            break
        try:
            mz = float(parts[0])
            intensity = float(parts[1])
        except ValueError:
            break
        peaks.append([mz, intensity])
        i += 1
    return peaks, i


def _read_record(lines: list[str], start: int, name: str) -> tuple[dict, int]:
    """`NAME:` 行の次から 1 レコードぶんを読む。"""
    fields: dict[str, object] = {"name": name}
    spectrum: list[list[float]] = []
    i = start
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            break
        if stripped.startswith("#"):
            i += 1
            continue
        field_name, field_value = _parse_field_line(stripped)
        if field_name is None:
            i += 1
            continue
        if field_name.lower() == "name":
            break  # 次のレコードの開始行。消費しない。
        canonical = _FIELD_ALIASES.get(field_name.lower())
        if canonical == _SPECTRUM_MARKER:
            i += 1
            peaks, i = _read_peaks(lines, i)
            spectrum.extend(peaks)
            continue
        i += 1
        if canonical is None:
            continue
        if canonical in _FLOAT_FIELDS:
            try:
                fields[canonical] = float(field_value)
            except ValueError:
                fields[canonical] = None
        elif canonical == "ion_mode":
            fields[canonical] = _parse_ion_mode(field_value)
        else:
            fields[canonical] = field_value
    spectrum.sort(key=lambda peak: peak[0])
    fields["spectrum"] = spectrum
    return fields, i


def iter_records(path: str | Path) -> Iterator[dict]:
    """`.msp` を読んで正規化レコードを順に返す。

    レコードの区切りは `NAME:` で始まる行（大小無視）。`#` で始まる行と
    空行は読み飛ばす。
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    n = len(lines)
    i = 0
    record_index = 0
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        field_name, field_value = _parse_field_line(stripped)
        if field_name is None or field_name.lower() != "name":
            i += 1
            continue
        fields, i = _read_record(lines, i + 1, field_value)
        yield make_record(record_index=record_index, library_id=None, **fields)
        record_index += 1
