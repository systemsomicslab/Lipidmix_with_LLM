"""AnalysisJob の CRUD とランディレクトリ管理。

ランディレクトリはデータフォルダ内にのみ作成する（リポジトリ外を強制）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lipidmix.handoff.schema import (
    SCHEMA_VERSION,
    AnalysisJob,
    MeasureType,
    OmicsType,
    Polarity,
)

JOB_FILENAME = "analysis-job.json"
RUNS_SUBDIR = "runs"

# MS-DIAL の SupportMsRawDataExtension と同じ集合。
_RAW_EXTENSIONS = frozenset({
    "abf", "ibf", "cdf", "mzml", "wiff", "raw", "d", "wiff2", "qgd", "lcd", "lrp", "imzml",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _job_id(polarity: str, measure: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    m_short = "h" if measure == "peak_height" else "a"
    p_short = polarity[:3]
    return f"job_{ts}_{p_short}_{m_short}"


def create_job(
    dataset_root: Path,
    method_file: Path,
    polarity: Polarity,
    measure: MeasureType,
    omics: OmicsType = "lipidomics",
    software_version: str = "",
    input_count: int = 0,
) -> tuple[AnalysisJob, Path]:
    """新規ジョブを作成してランディレクトリと analysis-job.json を返す。

    ランディレクトリが BASE_DIR（このリポジトリ）の配下にないことを確認する。
    """
    _assert_not_in_repo(dataset_root)

    job_id = _job_id(polarity, measure)
    run_dir = dataset_root / RUNS_SUBDIR / job_id
    run_dir.mkdir(parents=True, exist_ok=True)

    now = _now_iso()
    job = AnalysisJob(
        schema=SCHEMA_VERSION,
        job_id=job_id,
        status="planned",
        created_at=now,
        updated_at=now,
        dataset_root=str(dataset_root),
        input_count=input_count,
        software_name="MS-DIAL",
        software_version=software_version,
        execution_mode="console",
        method_file=str(method_file),
        omics=omics,
        polarity=polarity,
        measure=measure,
        run_dir=str(run_dir),
    )
    job_path = run_dir / JOB_FILENAME
    job.save(job_path)
    return job, job_path


def load_job(job_path: Path) -> AnalysisJob:
    if not job_path.is_file():
        raise FileNotFoundError(f"analysis-job.json が見つかりません: {job_path}")
    return AnalysisJob.load(job_path)


def update_status(job_path: Path, status: str, error: str | None = None) -> AnalysisJob:
    job = load_job(job_path)
    job.status = status  # type: ignore[assignment]
    job.updated_at = _now_iso()
    if error is not None:
        job.error = error
    job.save(job_path)
    return job


def save_job(job: AnalysisJob, job_path: Path) -> None:
    job.updated_at = _now_iso()
    job.save(job_path)


def list_jobs(dataset_root: Path) -> list[Path]:
    """dataset_root/runs/ 以下の analysis-job.json を新しい順に返す。"""
    runs_dir = dataset_root / RUNS_SUBDIR
    if not runs_dir.is_dir():
        return []
    found = sorted(
        runs_dir.rglob(JOB_FILENAME),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return found


def raw_input_summary(dataset_root: Path) -> dict[str, int]:
    """データフォルダ直下の計測ファイルを拡張子ごとに数える。"""
    counts: dict[str, int] = {}
    for entry in dataset_root.iterdir():
        ext = entry.suffix.lower().lstrip(".")
        if ext in _RAW_EXTENSIONS:
            counts[ext] = counts.get(ext, 0) + 1
    return counts


def count_raw_inputs(dataset_root: Path) -> int:
    """データフォルダ内の計測ファイル数を返す（raw_input_summary の合計）。"""
    return sum(raw_input_summary(dataset_root).values())


def _assert_not_in_repo(path: Path) -> None:
    """path が「リポジトリ内かつデータディレクトリ外」でないことを確認する。

    ランディレクトリを版管理下のソースツリーに掘らせないためのガード。
    ただし既定のデータディレクトリは <repo>/data（data_config.DEFAULT_DATA_DIR）で
    あり、これはリポジトリ配下だが .gitignore 済みの運用領域なので許可する。
    「リポジトリ配下すべて禁止」にすると既定構成が丸ごと使えなくなる。
    """
    from lipidmix.core import mcp_core
    from lipidmix.core.data_config import get_data_dir

    target = path.resolve()
    repo_root = mcp_core.BASE_DIR.resolve()
    if not _is_relative_to(target, repo_root):
        return  # リポジトリ外。何も言わない
    data_dir = get_data_dir().resolve()
    if _is_relative_to(target, data_dir):
        return  # <repo>/data 配下は運用領域として許可
    raise ValueError(
        f"ランディレクトリをリポジトリのソースツリー内 ({repo_root}) に作成しようとしました。"
        f"dataset_root はリポジトリ外か、データディレクトリ ({data_dir}) 配下を"
        f"指定してください: {path}"
    )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """child が parent 配下かを bool で返す（例外を制御フローに使わない）。"""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
