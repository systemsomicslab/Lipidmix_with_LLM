"""参照ライブラリの永続 store（sha256 を鍵とする SQLite キャッシュ）。

`.dbs` / `.msp` の `iter_records` はどちらもジェネレータで正規化レコードを吐くが、
`.dbs` の LZ4 block はチャンク単位でしか展開できず全走査が避けられない一方、
必要な情報は元の 1% 以下（`SpectrumPeak` の 13 フィールドのうち使うのは
`[m/z, intensity]` だけ）。そこで初回に 1 度だけコンパクトな SQLite へ変換し、
以後は precursor m/z 窓 × 極性で候補を引く（spec §5.2）。

キャッシュ先は環境変数 `LIPIDMIX_LIBRARY_CACHE_DIR`、無ければ
`mcp_core.DATA_DIR / ".library-cache"`。ファイル名は `<stem>-<sha256[:16]>.sqlite`
なので、元ファイルが変われば別ファイルになり、無効化の判断が要らない（古い
キャッシュは孤立するだけで、参照されなくなる）。

構築は一時ファイルに書いてから rename する（途中で落ちた store を次回の
`open_store` が掴まないため）。

deps: record 経由で `.dbs` / `.msp` の iter_records、mcp_core（leaf の DATA_DIR
解決のみ）。session_state / tools_* は import しない。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable

import msgpack

from lipidmix.core import mcp_core
from lipidmix.library import dbs as dbs_reader
from lipidmix.library import msp as msp_reader

LIBRARY_CACHE_ENV = "LIPIDMIX_LIBRARY_CACHE_DIR"

_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE record(
    id INTEGER PRIMARY KEY, name TEXT, precursor_mz REAL NOT NULL,
    ion_mode TEXT, adduct TEXT, rt REAL, formula TEXT, inchikey TEXT,
    smiles TEXT, compound_class TEXT, ontology TEXT, spectrum BLOB NOT NULL);
CREATE INDEX record_mz ON record(precursor_mz);
"""

_HASH_CHUNK_SIZE = 1 << 20  # 1 MiB


def cache_dir() -> Path:
    """store の置き場所。環境変数優先、無ければ `mcp_core.DATA_DIR` 配下。"""
    override = os.getenv(LIBRARY_CACHE_ENV)
    if override:
        return Path(override)
    return mcp_core.DATA_DIR / ".library-cache"


def source_sha256(path: str | Path) -> str:
    """元ファイルの sha256 を、全体をメモリへ載せずに計算する。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _store_path_for_digest(path: str | Path, digest: str, cache_dir: Path | None = None) -> Path:
    base = cache_dir if cache_dir is not None else globals()["cache_dir"]()
    stem = Path(path).stem
    return base / f"{stem}-{digest[:16]}.sqlite"


def store_path_for(path: str | Path, cache_dir: Path | None = None) -> Path:
    """`path` に対応する store ファイルのパス。sha256 が鍵。

    呼ぶたびに現在の内容から sha256 を計算し直す（元ファイルが変われば
    別のパスを返す）。大きなファイルに対して繰り返し呼ぶと重複計算になる
    ので、`open_store` 内部では digest を 1 度だけ計算して使い回す。
    """
    digest = source_sha256(path)
    return _store_path_for_digest(path, digest, cache_dir)


def _iter_records_for(path: Path) -> Iterable[dict]:
    """拡張子でリーダを選ぶ。`.msp` はテキスト、それ以外（`.dbs`/`.lbm2`）は
    `dbs.iter_records` に任せる（ZIP かどうかは向こうが中身で判定する）。"""
    if path.suffix.lower() == ".msp":
        return msp_reader.iter_records(path)
    return dbs_reader.iter_records(path)


def _build(source_path: Path, dest_path: Path, digest: str) -> None:
    """`source_path` を読んで `dest_path` に SQLite store を作る（一時ファイル経由）。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest_path.parent), suffix=".sqlite.tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            conn.executescript(_SCHEMA)
            ion_mode_counts: dict[str, int] = {}
            record_count = 0
            with conn:
                for record in _iter_records_for(source_path):
                    spectrum_blob = msgpack.packb(record["spectrum"] or [])
                    conn.execute(
                        "INSERT INTO record(name, precursor_mz, ion_mode, adduct, rt, "
                        "formula, inchikey, smiles, compound_class, ontology, spectrum) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record["name"],
                            record["precursor_mz"],
                            record["ion_mode"],
                            record["adduct"],
                            record["rt"],
                            record["formula"],
                            record["inchikey"],
                            record["smiles"],
                            record["compound_class"],
                            record["ontology"],
                            spectrum_blob,
                        ),
                    )
                    record_count += 1
                    ion_mode = record["ion_mode"]
                    if ion_mode:
                        ion_mode_counts[ion_mode] = ion_mode_counts.get(ion_mode, 0) + 1

                search_params = None
                storage_meta = dbs_reader.read_storage_meta(source_path)
                if storage_meta is not None:
                    search_params = storage_meta.get("search_params") or {}

                meta_rows = [
                    ("record_count", json.dumps(record_count)),
                    ("ion_modes", json.dumps(ion_mode_counts)),
                    ("source_sha256", json.dumps(digest)),
                    ("source_path", json.dumps(str(source_path))),
                    ("search_params", json.dumps(search_params)),
                ]
                conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", meta_rows)
        finally:
            conn.close()
        os.replace(str(tmp_path), str(dest_path))
    finally:
        tmp_path.unlink(missing_ok=True)


class LibraryStore:
    """構築済み SQLite を開いて候補検索するだけの薄い読み取り層。"""

    def __init__(self, conn: sqlite3.Connection, path: Path):
        self._conn = conn
        self._path = path
        meta = self._load_meta()
        self.record_count: int = meta["record_count"]
        self._ion_modes: dict[str, int] = meta["ion_modes"]
        self._source_sha256: str = meta["source_sha256"]
        self._search_params = meta["search_params"]

    def _load_meta(self) -> dict:
        rows = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        return {
            "record_count": json.loads(rows["record_count"]),
            "ion_modes": json.loads(rows["ion_modes"]),
            "source_sha256": json.loads(rows["source_sha256"]),
            "search_params": json.loads(rows["search_params"]),
        }

    def summary(self) -> dict:
        return {
            "record_count": self.record_count,
            "ion_modes": dict(self._ion_modes),
            "source_sha256": self._source_sha256,
            "search_params": self._search_params,
        }

    def candidates(
        self,
        precursor_mz: float,
        *,
        mz_tol: float,
        ion_mode: str | None = None,
        rt: float | None = None,
        rt_tol: float | None = None,
    ) -> list[dict]:
        query = (
            "SELECT name, precursor_mz, ion_mode, adduct, rt, formula, inchikey, "
            "smiles, compound_class, ontology, spectrum FROM record "
            "WHERE precursor_mz BETWEEN ? AND ?"
        )
        params: list = [precursor_mz - mz_tol, precursor_mz + mz_tol]
        if ion_mode is not None:
            query += " AND ion_mode = ?"
            params.append(ion_mode)
        if rt is not None:
            query += " AND (rt IS NULL OR ABS(rt - ?) <= ?)"
            params.extend([rt, rt_tol])

        rows = self._conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            (name, row_mz, row_ion_mode, adduct, row_rt, formula, inchikey,
             smiles, compound_class, ontology, spectrum_blob) = row
            results.append({
                "name": name,
                "precursor_mz": row_mz,
                "ion_mode": row_ion_mode,
                "adduct": adduct,
                "rt": row_rt,
                "formula": formula,
                "inchikey": inchikey,
                "smiles": smiles,
                "compound_class": compound_class,
                "ontology": ontology,
                "spectrum": msgpack.unpackb(spectrum_blob, use_list=True),
            })
        return results

    def close(self) -> None:
        self._conn.close()


def open_store(path: str | Path, *, cache_dir: Path | None = None, rebuild: bool = False) -> LibraryStore:
    """`path` の store を開く。無ければ（または `rebuild=True` なら）構築してから開く。"""
    source_path = Path(path)
    digest = source_sha256(source_path)
    base = cache_dir if cache_dir is not None else globals()["cache_dir"]()
    dest_path = _store_path_for_digest(source_path, digest, cache_dir=base)

    if rebuild or not dest_path.exists():
        _build(source_path, dest_path, digest)

    conn = sqlite3.connect(str(dest_path))
    return LibraryStore(conn, dest_path)
