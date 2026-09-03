"""DatasetState: mzTab-M を読んだ後の正準モデル。spec §11 参照。

session.arf の ARF 専用構造とは完全に独立している。session.dataset スロットへ格納する。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from lipidmix.mztab.identity import derive_inchikey
from lipidmix.mztab.reader import extract_abundance_matrix
from lipidmix.mztab.validator import detect_quantification_measure, validate_mztab

# assay メタデータ MTD 行のキーパターン。`assay[N]-<suffix>`（ms_run_ref 等）と、
# 表示名を運ぶ素の `assay[N]` を区別する。
_ASSAY_BARE_RE = re.compile(r"^assay\[(\d+)\]$")
_ASSAY_SUFFIX_RE = re.compile(r"^assay\[(\d+)\]-(.+)$")
# abundance 列名から assay 番号を取り出す（reader.py の _ABUNDANCE_RE と同じ規則）。
_ABUNDANCE_ASSAY_RE = re.compile(r"abundance_assay\[(\d+)\]", re.IGNORECASE)


class DatasetState:
    """mzTab-M 読み込み後の正準モデル。多変量解析の共通入口。"""

    def __init__(self):
        self.source_format: str = "mztab"
        self.source_files: dict[str, str] = {}          # path -> sha256
        self.quantification_measure: str | None = None  # peak_height | peak_area_above_zero
        self.quantification_confidence: str | None = None
        self.feature_matrix: np.ndarray | None = None   # shape (n_features, n_samples)
        self.sample_names: list[str] = []                # assay 表示名（無ければ abundance 列名にフォールバック）
        self.feature_ids: list[str] = []                # SMF_ID 列
        self.assay_metadata: dict = {}                  # assay_id -> MTD 情報
        self.feature_metadata: dict = {}                # smf_id -> {name, mz, rt, inchikey, ...}
        self.sml_rows: list[dict] | None = None
        self.sme_rows: list[dict] | None = None
        self.validation_result: dict = {}
        self.inchikey_coverage: dict = {}

        # --- ジョブ由来フィールド（dataset_load(job_path=...) で設定） ---
        # job_path: analysis-job.json の絶対パス。mzTab-M を直接指定した場合は None。
        self.job_path: str | None = None
        # artifact_paths: role -> [絶対パス, ...] のマップ。
        # Console 出力の ARF / DCL / EIC へのルックアップに使う。
        self.artifact_paths: dict[str, list[str]] = {}

        # --- 解析状態フィールド（dataset_preprocess / dataset_pca / dataset_differential が設定） ---
        # pp_matrix: 前処理済み行列。shape は (n_samples, n_features) — feature_matrix の転置。
        # ARF 側の session.arf.feature_matrix / pp_sample_names / pp_feature_names と
        # 同じ役割で、スロットだけが独立している。
        self.pp_matrix = None
        self.pp_sample_names: list[str] = []
        self.pp_feature_names: list[str] = []
        # roles: {sample_name: "sample"|"qc"|"blank"}。preprocess() と差次的解析の群構成で使う。
        self.roles: dict[str, str] = {}
        # sample_meta: {sample_name: {role, batch, batch_source}}。feature-qc.tsv の元。
        self.sample_meta: dict = {}
        self.preprocessing_recipe: dict = {}
        # last_pca / last_differential: 直近結果の全量。戻り値には要約だけを載せ、
        # 全量はここに置く（CLAUDE.md の戻り値肥大禁止）。
        # 現時点で読むのは dataset_export_differential のみ。図の保存ツール
        # （save_pca_figure / save_volcano_figure）は session.arf 側を見ており、
        # DatasetState 経路には未対応（次フェーズ）。
        self.last_pca = None
        self.last_differential = None


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_dataset_state(
    parse_result: dict,
    filename: str,
    source_path: str | Path,
) -> DatasetState:
    """parse_mztab() の戻り値から DatasetState を構築する。"""
    ds = DatasetState()

    # ファイルハッシュ
    p = Path(source_path)
    if p.is_file():
        ds.source_files[str(p)] = _sha256(p)

    # バリデーション
    ds.validation_result = validate_mztab(parse_result)

    # 定量種別
    measure, confidence = detect_quantification_measure(parse_result, filename)
    ds.quantification_measure = measure
    ds.quantification_confidence = confidence

    # abundance 行列
    matrix, sample_names, feature_ids = extract_abundance_matrix(parse_result)
    ds.feature_matrix = matrix
    ds.sample_names = sample_names
    ds.feature_ids = feature_ids

    # SMF メタデータ + InChIKey 導出
    #
    # mzTab-M 2.0.0-M では **構造・名称は SME セクションにしか無い**。SMF が持つのは
    # SMF_ID / SME_ID_REFS / exp_mass_to_charge / retention_time_in_seconds /
    # abundance_assay[N] で、database_identifier・smiles・inchi・chemical_name は
    # SME 専用の列である。SMF 行からこれらを読もうとすると常に None になり、
    # 「この測定には同定が無い」と誤読される（実データで InChIKey 0/714 になっていた）。
    smf_rows = parse_result["sections"].get("SMF", {}).get("rows", [])
    sme_by_id = _index_sme_rows(parse_result["sections"].get("SME", {}).get("rows", []))
    by_source: dict[str, int] = {"database_identifier": 0, "inchi_derived": 0, "smiles_derived": 0, "none": 0}
    for row in smf_rows:
        fid = row.get("SMF_ID", "")
        evidence = _best_evidence(row.get("SME_ID_REFS"), sme_by_id)
        ik, src = derive_inchikey(
            evidence.get("database_identifier"),
            evidence.get("inchi"),
            evidence.get("smiles"),
        )
        ds.feature_metadata[fid] = {
            "name": evidence.get("chemical_name"),
            "mz": _to_float(row.get("exp_mass_to_charge")),
            # mzTab-M は RT を**秒**で持つ。ARF 経路は分で持ち、両者は同じ
            # エクスポート契約の rt 列を共有するので、ここで分へ揃える。
            "rt": _seconds_to_minutes(_to_float(row.get("retention_time_in_seconds"))),
            "inchikey": ik,
            "inchikey_source": src,
            "smiles": evidence.get("smiles"),
            "inchi": evidence.get("inchi"),
        }
        by_source[src] = by_source.get(src, 0) + 1

    with_ik = sum(v for k, v in by_source.items() if k != "none")
    ds.inchikey_coverage = {
        "total_features": len(smf_rows),
        "with_inchikey": with_ik,
        "by_source": by_source,
    }

    # SML / SME 行
    ds.sml_rows = parse_result["sections"].get("SML", {}).get("rows")
    ds.sme_rows = parse_result["sections"].get("SME", {}).get("rows")

    # assay メタデータ
    # `assay[N]-<suffix>` 形式（ms_run_ref 等）に加え、素の `assay[N]` 行も拾う。
    # 素の行が MS-DIAL の表示名（例: 20220901_RAW_control_0h_1_NEG）を持つ唯一の場所で、
    # これを取りこぼすと sample_names が abundance_assay[N] という不透明な列識別子の
    # ままになり、群選択（dataset_differential）と QC/blank ロール検出
    # （detect_sample_roles はサンプル名のトークンを見る）が機能しなくなる。
    meta = parse_result.get("metadata", {})
    for k, v in meta.items():
        m = _ASSAY_SUFFIX_RE.match(k)
        if m:
            aid = f"assay[{m.group(1)}]"
            ds.assay_metadata.setdefault(aid, {})[m.group(2)] = v
            continue
        m = _ASSAY_BARE_RE.match(k)
        if m:
            aid = f"assay[{m.group(1)}]"
            # "name" キーで保持する。dataset_status など将来の呼び出し元は
            # assay_metadata[aid]["name"] を見れば表示名に到達できる。
            ds.assay_metadata.setdefault(aid, {})["name"] = v

    # abundance 列 → assay 表示名の解決。feature_matrix の列順（assay 番号昇順）は
    # extract_abundance_matrix が既に確定させているので、ここでは並べ替えず
    # 1:1 で置き換えるだけにする（列順を変えると全サンプルが黙って誤ラベルされる）。
    ds.sample_names, name_warnings = _resolve_sample_names(ds.sample_names, ds.assay_metadata)
    if name_warnings:
        ds.validation_result.setdefault("warnings", []).extend(name_warnings)

    return ds


def _resolve_sample_names(abundance_cols: list[str], assay_metadata: dict) -> tuple[list[str], list[str]]:
    """abundance_assay[N] 列名を assay[N] の表示名へ解決する。

    表示名が無い assay（既存フィクスチャは全てこれに該当。現実のファイルでも
    起こり得る）は列識別子のままフォールバックする——意味のある名前が無いより、
    一意で追跡可能な旧識別子を残すほうが安全。

    表示名が複数 assay で重複する不正ファイルは、置き換え自体は行いつつ
    warning を返す（例外にはしない。読み込み自体を止めるほどではなく、
    群選択が曖昧になり得ることだけ呼び出し元に伝えれば足りる）。
    """
    resolved: list[str] = []
    cols_by_name: dict[str, list[str]] = {}
    for col in abundance_cols:
        m = _ABUNDANCE_ASSAY_RE.search(col)
        name = None
        if m:
            aid = f"assay[{m.group(1)}]"
            name = (assay_metadata.get(aid) or {}).get("name")
        resolved_name = name if name else col
        resolved.append(resolved_name)
        cols_by_name.setdefault(resolved_name, []).append(col)

    warnings = [
        f"assay 表示名が重複しています（{name!r}）: {', '.join(cols)}。"
        "sample_names での群選択が意図しないアッセイを指す恐れがあります。"
        for name, cols in cols_by_name.items() if len(cols) > 1
    ]
    return resolved, warnings

def _seconds_to_minutes(value: float | None) -> float | None:
    """mzTab-M の秒表記 RT を分へ揃える。"""
    return None if value is None else value / 60.0


def _index_sme_rows(sme_rows) -> dict:
    """SME 行を SME_ID で引けるようにする。"""
    return {str(r.get("SME_ID")): r for r in (sme_rows or []) if r.get("SME_ID") is not None}


def _sme_rank(row: dict) -> tuple[int, int]:
    """rank の昇順キー。rank が無い/数値でない証拠は最後に回す。

    mzTab-M の rank は 1 が最上位。同一特徴に複数の候補が付くのは常態なので、
    どれを採るかを暗黙にしない。
    """
    raw = row.get("rank")
    try:
        return (0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return (1, 0)


def _best_evidence(refs, sme_by_id: dict) -> dict:
    """SME_ID_REFS が指す SME 行のうち、rank が最上位のものを返す。

    参照が無い（SMF_ID だけあって同定が付かなかった）特徴では空 dict を返す。
    呼び出し側はこれを「同定を取得していない」として扱う。
    """
    if not refs:
        return {}
    candidates = []
    for ref in str(refs).split("|"):
        row = sme_by_id.get(ref.strip())
        if row is not None:
            candidates.append(row)
    if not candidates:
        return {}
    return min(candidates, key=_sme_rank)


def _to_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
