"""Read MS-DIAL AnalysisFileClass values and join them to ARF samples."""

from __future__ import annotations

from collections import Counter
import io
from pathlib import Path
import zipfile

import lz4.block
import msgpack

from msdial_tags import normalize_sample_name


MSGPACK_LZ4_BLOCK_TYPE = 99

# MsdialDataStorageBase MessagePack keys.
ANALYSIS_FILES_INDEX = 0

# AnalysisFileBean MessagePack keys.
ANALYSIS_FILE_PATH_INDEX = 0
ANALYSIS_FILE_NAME_INDEX = 1
ANALYSIS_FILE_TYPE_INDEX = 2
ANALYSIS_FILE_CLASS_INDEX = 3
ANALYTICAL_ORDER_INDEX = 4
ANALYSIS_FILE_ID_INDEX = 5
ANALYSIS_FILE_INCLUDED_INDEX = 6

# ProjectBaseParameter MessagePack keys.
PROJECT_FOLDER_INDEX = 3
PROJECT_FILE_INDEX = 4

# AlignmentChromPeakFeature MessagePack keys.
ARF_FILE_ID_INDEX = 0
ARF_FILE_NAME_INDEX = 1


def decode_msdial_messagepack(data: bytes):
    """Decode a MessagePack-CSharp Lz4Block payload used by MS-DIAL."""
    packed = msgpack.unpackb(data, raw=False, strict_map_key=False)
    if not isinstance(packed, msgpack.ExtType):
        return packed
    if packed.code != MSGPACK_LZ4_BLOCK_TYPE:
        raise ValueError(f"Unsupported MS-DIAL MessagePack extension type: {packed.code}")

    unpacker = msgpack.Unpacker(io.BytesIO(packed.data), raw=False)
    uncompressed_size = next(unpacker)
    compressed = packed.data[unpacker.tell():]
    unpacked = lz4.block.decompress(
        compressed,
        uncompressed_size=uncompressed_size,
    )
    return msgpack.unpackb(unpacked, raw=False, strict_map_key=False)


def resolve_mddata_path(
    source_path: str | Path,
    mddata_path: str | Path | None = None,
) -> Path | None:
    """Resolve an mddata file explicitly, from mdproject, or beside an ARF."""
    if mddata_path is not None:
        explicit = Path(mddata_path)
        if not explicit.is_file():
            raise FileNotFoundError(f"MS-DIAL dataset file was not found: {explicit}")
        return explicit

    source = Path(source_path)
    if source.suffix.casefold() == ".mddata":
        return source if source.is_file() else None
    if source.suffix.casefold() == ".mdproject":
        candidates = _mddata_paths_from_project(source)
        return _select_single_dataset(candidates, source)

    directory = source.parent if source.suffix else source
    direct = sorted(directory.glob("*.mddata"))
    if direct:
        return _select_single_dataset(direct, directory)

    projects = sorted(directory.glob("*.mdproject"))
    project_candidates = []
    for project in projects:
        project_candidates.extend(_mddata_paths_from_project(project))
    return _select_single_dataset(project_candidates, directory)


def parse_analysis_file_classes(mddata_path: str | Path) -> list[dict]:
    """Extract AnalysisFileBean metadata, including user-defined Class ID."""
    path = Path(mddata_path)
    storage = decode_msdial_messagepack(path.read_bytes())
    if not isinstance(storage, list) or len(storage) <= ANALYSIS_FILES_INDEX:
        raise ValueError(f"Invalid MS-DIAL mddata structure: {path}")
    analysis_files = storage[ANALYSIS_FILES_INDEX]
    if not isinstance(analysis_files, list):
        raise ValueError(f"AnalysisFiles is not a list in: {path}")

    records = []
    for row in analysis_files:
        if not isinstance(row, list) or len(row) <= ANALYSIS_FILE_ID_INDEX:
            continue
        records.append({
            "file_id": _at(row, ANALYSIS_FILE_ID_INDEX),
            "file_name": _decode_text(_at(row, ANALYSIS_FILE_NAME_INDEX)),
            "file_path": _decode_text(_at(row, ANALYSIS_FILE_PATH_INDEX)),
            "class_id": _decode_text(_at(row, ANALYSIS_FILE_CLASS_INDEX)),
            "analysis_file_type": _at(row, ANALYSIS_FILE_TYPE_INDEX),
            "analytical_order": _at(row, ANALYTICAL_ORDER_INDEX),
            "included": bool(_at(row, ANALYSIS_FILE_INCLUDED_INDEX, True)),
        })
    return records


def discover_arf_class_index(
    arf_path: str | Path,
    mddata_path: str | Path | None = None,
) -> dict | None:
    """Resolve mddata and build FileID/FileName lookup tables for an ARF."""
    resolved = resolve_mddata_path(arf_path, mddata_path)
    if resolved is None:
        return None
    records = parse_analysis_file_classes(resolved)

    by_file_id: dict[int, dict] = {}
    by_file_name: dict[str, dict] = {}
    for record in records:
        file_id = record["file_id"]
        if isinstance(file_id, int):
            if file_id in by_file_id:
                raise ValueError(f"Duplicate AnalysisFileId in {resolved}: {file_id}")
            by_file_id[file_id] = record

        name_key = normalize_sample_name(
            record["file_name"], strip_processing_timestamp=False,
        )
        if name_key:
            if name_key in by_file_name:
                raise ValueError(
                    f"Duplicate AnalysisFileName after normalization in {resolved}: "
                    f"{record['file_name']}"
                )
            by_file_name[name_key] = record

    class_counts = Counter(record["class_id"] for record in records)
    return {
        "mddata_path": str(resolved),
        "records": records,
        "by_file_id": by_file_id,
        "by_file_name": by_file_name,
        "class_counts": dict(sorted(class_counts.items(), key=lambda item: str(item[0]))),
    }


def attach_class_ids_to_spots(features: list[dict], class_index: dict | None) -> list[dict]:
    """Attach per-sample Class ID metadata to every ARF alignment spot."""
    if class_index is None:
        return features
    for spot in features:
        sample_classes = {}
        for row in spot.get("AlignedPeakProperties") or []:
            file_id, file_name = _arf_sample_identity(row)
            record = resolve_sample_class(class_index, file_id, file_name)
            if record is None:
                continue
            sample_classes[_sample_key(file_id, file_name)] = record
        spot["SampleClasses"] = sample_classes
    return features


def _class_tokens(class_id) -> set[str]:
    """Split a Class ID into its underscore-delimited factor tokens (casefold)."""
    return {token for token in str(class_id).casefold().split("_") if token}


def expand_class_specs(
    specs: list[str],
    available_class_ids: list[str],
) -> dict[str, list[str]]:
    """Expand each (possibly partial) Class spec to the matching full Class IDs.

    A spec is split into underscore-delimited tokens; a Class ID matches when it
    contains ALL of the spec's tokens (order-independent AND). A full exact Class
    ID therefore matches only itself. Each spec must match at least one Class ID
    or a ValueError is raised.

    Returns a mapping {original_spec: [matched_class_id, ...]} (original casing
    preserved for both keys and values).
    """
    result: dict[str, list[str]] = {}
    for spec in specs:
        spec_str = str(spec).strip()
        if not spec_str:
            continue
        spec_tokens = _class_tokens(spec_str)
        matched = [
            class_id
            for class_id in available_class_ids
            if spec_tokens <= _class_tokens(class_id)
        ]
        if not matched:
            raise ValueError(
                f"No Class ID matched spec '{spec_str}'. "
                f"Available: {', '.join(str(c) for c in available_class_ids)}"
            )
        result[spec] = matched
    return result


def assign_sample_groups(
    sample_names: list[str],
    class_index: dict | None,
    group_levels: list[str] | None = None,
) -> dict[str, str | None]:
    """Map each PCA sample name to a group label for coloring.

    - Without ``group_levels``: the group is the sample's full Class ID.
    - With ``group_levels`` (factor value tokens, e.g. ["gf", "spf"]): the group
      is whichever listed token the sample's Class ID contains. A sample matching
      no listed level becomes "other"; a sample matching two or more raises a
      ValueError (the levels are not mutually exclusive).
    - Samples with no resolvable Class ID (or no metadata at all) get ``None``.
    """
    levels = [str(level).strip() for level in group_levels or [] if str(level).strip()]
    groups: dict[str, str | None] = {}
    for name in sample_names:
        if class_index is None:
            groups[name] = None
            continue
        record = resolve_sample_class(class_index, None, name)
        if record is None:
            groups[name] = None
            continue
        class_id = record["class_id"]
        if not levels:
            groups[name] = class_id
            continue
        tokens = _class_tokens(class_id)
        hits = [level for level in levels if level.casefold() in tokens]
        if len(hits) > 1:
            raise ValueError(
                f"Class ID '{class_id}' matches multiple group_levels "
                f"({', '.join(hits)}); levels must be mutually exclusive."
            )
        groups[name] = hits[0] if hits else "other"
    return groups


def filter_arf_by_class_ids(
    features: list[dict],
    class_index: dict | None,
    class_ids: list[str] | None,
    *,
    missing_sample_policy: str = "error",
) -> tuple[list[dict], dict]:
    """Keep only sample rows whose AnalysisFileClass is selected.

    ``class_ids`` accepts partial factor specs (token-subset AND within a spec,
    OR across specs); each spec is expanded to the matching full Class IDs via
    :func:`expand_class_specs`.
    """
    specs = [str(value) for value in class_ids or [] if str(value).strip()]
    before_rows = _count_rows(features)
    if not specs:
        return features, {
            "requested_class_ids": [],
            "matched_class_ids": [],
            "before_spots": len(features),
            "after_spots": len(features),
            "before_sample_peaks": before_rows,
            "after_sample_peaks": before_rows,
        }
    if class_index is None:
        raise ValueError("No MS-DIAL mddata file was found for Class ID filtering.")
    if missing_sample_policy not in {"error", "exclude"}:
        raise ValueError("missing_sample_policy must be 'error' or 'exclude'")

    available_class_ids = sorted({str(record["class_id"]) for record in class_index["records"]})
    expanded = expand_class_specs(specs, available_class_ids)  # 一致ゼロは ValueError
    matched_class_ids = sorted({cid for ids in expanded.values() for cid in ids})
    selected = {cid.casefold() for cid in matched_class_ids}

    filtered = []
    missing_samples = set()
    for spot in features:
        kept_rows = []
        for row in spot.get("AlignedPeakProperties") or []:
            file_id, file_name = _arf_sample_identity(row)
            record = resolve_sample_class(class_index, file_id, file_name)
            if record is None:
                missing_samples.add(file_name or str(file_id))
                continue
            if str(record["class_id"]).casefold() in selected:
                kept_rows.append(row)
        if kept_rows:
            copied = spot.copy()
            copied["AlignedPeakProperties"] = kept_rows
            filtered.append(copied)

    if missing_samples and missing_sample_policy == "error":
        raise ValueError(
            "No Class ID metadata matched these ARF samples: "
            + ", ".join(sorted(missing_samples))
        )

    return filtered, {
        "requested_class_ids": specs,
        "matched_class_ids": matched_class_ids,
        "before_spots": len(features),
        "after_spots": len(filtered),
        "before_sample_peaks": before_rows,
        "after_sample_peaks": _count_rows(filtered),
        "missing_samples": len(missing_samples),
    }


def resolve_sample_class(
    class_index: dict,
    file_id: int | None,
    file_name: str | None,
) -> dict | None:
    """Resolve class metadata by FileID first and FileName second."""
    by_id = class_index.get("by_file_id", {})
    by_name = class_index.get("by_file_name", {})
    id_record = by_id.get(file_id) if file_id is not None else None
    name_key = normalize_sample_name(file_name, strip_processing_timestamp=False)
    name_record = by_name.get(name_key) if name_key else None
    if id_record is not None and name_record is not None and id_record is not name_record:
        raise ValueError(
            f"ARF FileID/FileName resolve to different mddata samples: "
            f"FileID={file_id}, FileName={file_name}"
        )
    return id_record or name_record


def get_sample_class_id(
    spot: dict,
    file_id: int | None,
    file_name: str | None,
) -> str | None:
    record = (spot.get("SampleClasses") or {}).get(_sample_key(file_id, file_name))
    return record.get("class_id") if record else None


def _mddata_paths_from_project(project_path: Path) -> list[Path]:
    if not project_path.is_file():
        return []
    with zipfile.ZipFile(project_path) as archive:
        project_data = decode_msdial_messagepack(archive.read("Project"))
    parameters = project_data.get("ProjectParameters", []) if isinstance(project_data, dict) else []
    candidates = []
    for parameter in parameters:
        if not isinstance(parameter, list) or len(parameter) <= PROJECT_FILE_INDEX:
            continue
        folder = Path(_decode_text(parameter[PROJECT_FOLDER_INDEX]) or project_path.parent)
        filename = Path(_decode_text(parameter[PROJECT_FILE_INDEX]) or "")
        local_candidate = project_path.parent / filename.name
        saved_candidate = folder / filename
        if local_candidate.is_file():
            candidates.append(local_candidate)
        elif saved_candidate.is_file():
            candidates.append(saved_candidate)
    return candidates


def _select_single_dataset(candidates, source: Path) -> Path | None:
    unique = sorted({Path(candidate).resolve() for candidate in candidates if Path(candidate).is_file()})
    if not unique:
        return None
    if len(unique) > 1:
        raise ValueError(
            f"Multiple mddata files were found near {source}: "
            + ", ".join(str(path) for path in unique)
        )
    return unique[0]


def _arf_sample_identity(row: list) -> tuple[int | None, str | None]:
    if not isinstance(row, list):
        return None, None
    file_id = row[ARF_FILE_ID_INDEX] if len(row) > ARF_FILE_ID_INDEX else None
    file_name = _decode_text(row[ARF_FILE_NAME_INDEX]) if len(row) > ARF_FILE_NAME_INDEX else None
    return file_id if isinstance(file_id, int) else None, file_name


def _sample_key(file_id: int | None, file_name: str | None) -> int | str:
    if file_id is not None:
        return file_id
    return normalize_sample_name(file_name, strip_processing_timestamp=False)


def _decode_text(value) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if value is None:
        return None
    return str(value)


def _at(row: list, index: int, default=None):
    return row[index] if len(row) > index else default


def _count_rows(features: list[dict]) -> int:
    return sum(len(spot.get("AlignedPeakProperties") or []) for spot in features)
