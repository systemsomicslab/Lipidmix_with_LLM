# 検証済みLC–MSメタボロミクス拡張 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定した測定profileを使い、LC–MS rawから同定証拠、内部標準補正、QC、統計、レポートまで既存pipelineで実行できるようにする。

**Architecture:** 既存の監視・lock・再開機構を維持し、v2 request/stageとprofile adapterを追加する。解析後のfeature binding、注入証拠、前処理matrixを独立した永続結果として扱い、統計はmatrix result IDを参照する。各部分は同じDatasetStateと実行契約に依存するため一つのplanとし、5か所でレビューを区切る。

**Tech Stack:** Windows、Python、FastMCP、NumPy、SciPy、scikit-learn、matplotlib、pytest、既存ARF/mzTab reader。検証Pythonは`C:/Python314/python.exe`。現環境のSciPyは1.17.1で`stats.tukey_hsd`が存在することを確認。新規外部サービスは不要。

**Spec:** [修正版spec](../specs/2026-09-15-validated-lcms-metabolomics-design.md)（2026-09-15自己レビュー7件反映版）。本planは実装指示書の作成であり、コード実装・実Console実行の開始ではない。

## Global Constraints

- 初期実装は、単一LC条件・単一極性・DDA・peak heightのLC–MSバッチを対象とする。
- NASに対する書込み・移動・削除・解析実行は行わない。今回の検証は既にコピー済みのローカルデータを起点とする。
- 研究データや非公開ライブラリをspec、fixture、commitへ含めない。
- 質量データの調査・解析操作はms-data-parser MCPを入口とする。内部workerは既存のPythonサービスを利用し、MCPを自己呼び出ししない。
- schema省略は従来どおりv1。v2を暗黙に推測しない。
- v1 pipelineは旧契約・旧schemaで新規作成・再開する。過去の記録をインプレース変換しない。
- 元行列、検出mask、gap-fill証跡を保存する。
- 分母不正で生じた欠損は後段補完も禁止し、sample/featureの理由を保存する。
- 内部標準比viewへのTIC/median/PQNは初期版では拒否する。二重正規化を暗黙に実行しない。
- v2のlog変換・算術平均比にはpseudocountを加えない。v1のpseudo_count既定値とlog空間の効果量定義は変更しない。
- 既存15列pathway TSVは契約を変えず、二群結果の同定済み対象だけを任意出力する。
- ソフトウェア完成には実Console接続試験、profile検証完了には別の科学的検証証明書が必要。
- プロテオミクス、CE-MS、DIA、絶対濃度、反復測定モデル、装置制御、外部pathway呼出しは追加しない。

## 実行・レビューの規則

コード確認基準は`db41db0`。着手時にHEAD、差分、適用AGENTS.mdを再確認し、specとplanだけを含む作業状態を隔離する。ユーザーの既存変更をstash・削除しない。今回の作成時点ではspecがstage済み、docs/researchは別の未追跡成果物だった。

各TaskはRED確認→実装→対象GREEN→差分レビュー→関連ファイルだけcommitの順。コードblockは接続方針と最小回帰例であり、入力検証・境界ケースは各Taskに列挙した契約も満たすこと。実装を偽の定数返却でテストに合わせない。全チェックボックスは未着手。

前回の文書commitフックでは1652 passed、3 failed。2件はDETACH_UNSUPPORTED、もう1件は切り離し起動失敗だった。この値は今回のテスト結果ではない。実装時は新しいプロセスでbaselineを確認し、環境由来と実装退行を分離する。フック回避を既定手順にしない。

## ファイルと依存順

新規モジュールは以下に限定する。既存の汎用pipelineを複製しない。

| Task | 主な新規ファイル | 既存接続先 |
|---|---|---|
| 1 | `console/profile_schema.py` | `core/atomic_io.py`のDomainError/canonical_hashを利用 |
| 2 | `console/profiles.py`, `console/profile_adapter.py` | `console/method_file.py`, `pipeline/inputs.py` |
| 3 | `pipeline/request_v2.py` | `pipeline/request.py` |
| 4 | `pipeline/stage_plan.py` | store/engine/recovery、handoff/schema、mztab/loading |
| 5 | なし | `analysis/sample_manifest.py`, `dataset_analysis.py`, `result_state.py` |
| 6 | `analysis/assay_evidence.py` | `arf/reader.py`, `mztab/evidence.py`, `mztab/dataset_state.py` |
| 7 | `analysis/feature_bindings.py` | DatasetState、Task 6 |
| 8 | `analysis/internal_standards.py`, `analysis/matrix_state.py` | preprocessing/result_state |
| 9 | `analysis/assay_qc.py` | preprocessing/preprocess_policy |
| 10 | `analysis/statistics_v2.py`, `analysis/multigroup.py` | differential/pca/dataset_analysis |
| 11 | `analysis/feature_export.py` | dataset_export、pipeline/report |
| 12 | `pipeline/metabolomics_handlers.py` | service/engine/recovery/worker |
| 13 | なし | tools/pipeline_tools、tools/dataset_analysis_tools、server登録 |
| 14 | `tests/metabolomics_fixtures.py` | 既存pipeline fixtureと回帰tests |
| 15 | `console/compatibility.py` | 実Console接続とローカル証拠 |
| 16 | なし | profile科学的検証・運用docs |

依存: 1→2→3→4、5は3の後、6→7→8→9→10→11、12は4〜11、13→14→15→16。Task 6は2で決めるadapter識別情報を使う。実データ資料不足でもTask 1〜14の合成入力実装は進められるが、15の接続合格前にソフトウェア完成としない。

## Task 1: 厳密なprofile schemaと検証証明書

**Files:** Create `lipidmix/console/profile_schema.py`, `tests/test_lcms_profile_schema.py`, `docs/schema/lcms-profile-v1.md`。

**Interfaces:** `validate_profile(data: dict) -> dict`は正規化した新しいdictを返す。`profile_content_hash(data: dict) -> str`はvalidationのみ除いたcanonical hash。`validate_certificate(profile: dict, certificate: dict, observed_hashes: dict) -> None`。例外はDomainErrorのPROFILE_INVALID/PROFILE_VALIDATION_INVALID。

- [x] RED: 次の検査と、未知キー、NaN、非整数revision、負ppm、重複target/recipe、循環内部標準、不存在recipe参照、既定recipe欠落を追加する。

```python
from lipidmix.console.profile_schema import profile_content_hash

def test_certificate_metadata_does_not_change_content_identity():
    p = {"schema": "lcms-profile.v1", "validation": {"status": "draft"}}
    q = {**p, "validation": {"status": "validated", "certificate_sha256": "a" * 64}}
    assert profile_content_hash(p) == profile_content_hash(q)
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_lcms_profile_schema.py -q`。新規module不在または仕様assertでREDを確認する。
- [x] Implement: profile各層のallowlist、列挙・型・参照整合性を定義する。`analysis_recipe`は`statistics`と`internal_standards`だけを持ち、数値前処理はmatrix_recipesを唯一の保存先とする。feature_targetsはtarget_idキーのdict、matrix_recipesはrecipe_idキーのdictとする。evidenceはfield pathキーのdict、条件未記載は`{value: null, reason: ...}`とする。

```python
from lipidmix.core.atomic_io import canonical_hash

def profile_content_hash(data: dict) -> str:
    return canonical_hash({k: v for k, v in data.items() if k != "validation"})
```

証明書のhash一致、必須判定pass、適用範囲を検証する。署名を実装しない。profileを手書きvalidatedにしても証明書不足なら拒否する。schema文書へ完全な合成profile例、各キーの型、null可否、routine許容overrideの集合を記載する。runtimeで値を推測しない。
- [x] GREEN: 上記対象テスト。証明書の一要素改変も拒否することを確認。
- [x] Commit: `git add -- lipidmix/console/profile_schema.py tests/test_lcms_profile_schema.py docs/schema/lcms-profile-v1.md`、`git commit -m "feat: LC-MSプロファイル契約を追加"`。

## Task 2: method依存・実行環境・raw指紋

**Files:** Create `lipidmix/console/profiles.py`, `lipidmix/console/profile_adapter.py`, `tests/test_lcms_profile_inputs.py`。Modify `lipidmix/console/method_file.py`, `lipidmix/pipeline/inputs.py`。

**Interfaces:** `hash_files(paths: list[Path]) -> dict[str,str]`、`load_profile(path: Path, purpose: str) -> dict`、`resolve_profile_inputs(profile: dict, source_root: Path) -> dict`、`snapshot_profile(plan: dict, run_dir: Path) -> dict`。adapterは`adapter_id, supported_software, dependency_keys, raw_formats, evidence_reader`を返す`adapter_capabilities(adapter_id: str) -> dict`。

- [x] RED: 同サイズ/mtime改変、LBMなしMSP/TXT、必須依存欠落、DDA/極性矛盾、未知adapter、原本不変、別出力先の計画hash同一を試験する。

```python
import os
from lipidmix.console.profiles import hash_files

def test_hash_detects_stat_preserving_change(tmp_path):
    p = tmp_path / "raw.wiff"
    p.write_bytes(b"aaaa")
    stat = p.stat()
    old = hash_files([p])
    p.write_bytes(b"bbbb")
    os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert hash_files([p]) != old
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_lcms_profile_inputs.py -q`。
- [x] Implement: 既存raw形式選択・sidecar列挙を再利用し、全ファイルhashを追加する。実行環境manifestはexeのほか同梱DLL/設定、adapter版を列挙する。二回のstatが異なるhash計算はINPUT_CHANGEDとして拒否する。

```python
import hashlib

def hash_files(paths):
    out = {}
    for path in sorted(paths):
        with path.open("rb") as stream:
            out[str(path)] = hashlib.file_digest(stream, "sha256").hexdigest()
    return out
```

上記hash処理に前後stat検査を加える。profile相対pathはprofile親から解決し、snapshotだけを書換える。methodの依存キーは対応Consoleのソース/実methodで確認してadapterへ登録し、その証拠位置をdocへ記録する。キー名を推測してMSPをLBM欄へ入れない。確認できない版はPROFILE_ADAPTER_UNSUPPORTEDとしてplanに不足を返す。method実行コピーの絶対pathは計画hashに混ぜない。
- [x] GREEN: 新テストに加え`tests/test_console_plan_method.py tests/test_pipeline_inputs.py`。
- [x] Commit: 新規3ファイルと上記既存2ファイルのみstageし、`git commit -m "feat: method依存と実行環境を固定"`。

## Task 3: request v2の解決・更新

**Files:** Create `lipidmix/pipeline/request_v2.py`, `tests/test_metabolomics_request.py`。Modify `lipidmix/pipeline/request.py`, `docs/schema/pipeline-request-v2.md`（新規）。

**Interfaces:** `request_v2.resolve(data: dict, profile: dict) -> dict`, `validate_statistics(items: list[dict], profile: dict) -> list[dict]`, `merge_updates(current: dict, updates: dict, profile: dict) -> dict`。既存resolve_request/merge_updatesでschema dispatchする。

- [x] RED: unknown、null、旧comparisons、method_file直接指定、重複statistic_id、target不整合、schema省略v1、routine範囲外override。

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.pipeline.request_v2 import validate_statistics

def test_pca_rejects_groups():
    s = {"statistic_id": "p", "kind": "pca", "matrix_recipe_id": "default",
         "transform": "none", "feature_scope": {"mode": "all_eligible"},
         "groups": ["a", "b"]}
    with pytest.raises(DomainError):
        validate_statistics([s], {"matrix_recipes": {"default": {}}})
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_request.py tests/test_pipeline_request.py -q`。
- [x] Implement: spec §6.2のdiscriminated統計schemaを実装する。preprocess overrideは`{recipe_id: {normalize, drift_correct, filter, impute}}`で、存在するrecipeにのみ適用する。standard_assaysはsample ID配列。feature_bindingsは初回指定もresumeも同じ検査を通す。優先順位はfield単位で出所を持つ。

```python
def effective_target(statistics):
    return "differential" if any(s["kind"] != "pca" for s in statistics) else "exploratory"
```

更新不可キーを受けたらNEW_PIPELINE_REQUIRED、誤った型はPIPELINE_REQUEST_INVALID。既定PCAはprofileのdefault recipeを参照する。routine許容範囲は検証証明書の明示許容集合で判定し、validationだけを理由に入力検証を省略しない。
- [x] GREEN: 同じコマンド。v1の既定値・null・更新の既存assertは変更しない。
- [x] Commit: 4ファイルをstageし、`git commit -m "feat: メタボロミクスrequest v2を追加"`。

## Task 4: job v3・run v2・stage契約

**Files:** Create `lipidmix/pipeline/stage_plan.py`, `tests/test_metabolomics_stages.py`。Modify `lipidmix/pipeline/store.py`, `engine.py`, `recovery.py`, `lipidmix/handoff/schema.py`, `lipidmix/mztab/loading.py`。

**Interfaces:** `stage_plan.build_v2(request: dict) -> list[dict]`（stage_id/handler/statistic_id）、`stage_plan.invalidated_v2(changed: set[str], request: dict) -> set[str]`。storeとengineは同じbuilderを呼ぶ。job v3にprofile snapshotとdependency/environment manifestを追加する。

- [x] RED: 混在統計のstage、追加/削除、古いrun/job読込、未知schema拒否、schemaごとの書込版。

```python
from lipidmix.pipeline.stage_plan import build_v2

def test_statistic_ids_expand_once():
    plan = build_v2({"statistics": [{"statistic_id": "a", "kind": "anova_tukey"}]})
    ids = [s["stage_id"] for s in plan]
    assert ids[-3:] == ["statistics:a", "export:a", "report"]
    assert len(ids) == len(set(ids))
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_stages.py tests/test_pipeline_store.py tests/test_pipeline_engine.py -q`。
- [x] Implement: v2 stageのみspec §6.2の順に生成する。

```python
BASE_V2 = ("prepare_inputs", "execute_console", "validate_outputs", "load_dataset",
           "resolve_metadata", "load_assay_evidence", "resolve_feature_bindings",
           "qc_raw", "preprocess", "qc_processed")

def build_v2(request):
    out = [{"stage_id": s, "handler": s, "statistic_id": None} for s in BASE_V2]
    for stat in request["statistics"]:
        for handler in ("statistics", "export"):
            out.append({"stage_id": f"{handler}:{stat['statistic_id']}",
                        "handler": handler, "statistic_id": stat["statistic_id"]})
    return out + [{"stage_id": "report", "handler": "report", "statistic_id": None}]
```

旧BASE_STAGE_IDSは変更しない。schema dispatchをreader/writer両方へ追加し、v1をv2へ自動保存しない。recoveryのstage resetもbuilderと依存表を共有する。artifact hash、append-only、revision競合検査を維持する。
- [x] GREEN: 同じテスト群と既存handoff/loadingのテストを実行。
- [x] Commit: 上記7ファイルをstageし、`git commit -m "feat: v2 stageとjob v3を接続"`。

**Review A:** A01〜A07。未知schema・未検証profileが誤って実行に進まないこと、v1 golden結果が不変であることを確認する。

## Task 5: standard役割と生物試料ID

**Files:** Modify `lipidmix/analysis/sample_manifest.py`, `dataset_analysis.py`, `result_state.py`。Create `tests/test_sample_manifest_v2.py`。

**Interfaces:** 既存parse_manifestはheaderによりv1/v2をdispatch。`validate_independent_samples(rows: list[dict], groups: list[str]) -> None`をsample_manifestへ追加。

- [x] RED: 9列、standard role、exclude、同一生物ID、sourceのmissing/extra、v1の8列を検証する。

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.analysis.sample_manifest import validate_independent_samples

def test_duplicate_biological_id_is_not_independent_n():
    rows = [{"sample_id": x, "biological_sample_id": "bio1", "group": "A",
             "role": "sample", "include": True} for x in ("inj1", "inj2")]
    with pytest.raises(DomainError) as caught:
        validate_independent_samples(rows, ["A"])
    assert caught.value.code == "REPEATED_MEASURES_UNSUPPORTED"
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_sample_manifest_v2.py tests/test_sample_manifest.py -q`。
- [x] Implement: role/include/group選択を共通関数へ集約し、v2の統計対象を明示する。

```python
selected = [r for r in rows if r["include"] and r["role"] == "sample"
            and r["group"] in groups]
ids = [r["biological_sample_id"] for r in selected]
```

上記に欠落・重複検査を加える。standard_assaysはstandardかつincludedのみ。QC pool/orderを名前から作らない。metadata fingerprintへbiological_sample_idとstandard_assaysの依存を追加する。
- [x] GREEN: 上記と`tests/test_result_state.py`。
- [x] Commit: 4ファイルをstageし、`git commit -m "feat: 標準注入と独立試料を区別"`。

## Task 6: 注入単位のRT/mz証拠reader

**Files:** Create `lipidmix/analysis/assay_evidence.py`, `tests/test_assay_evidence.py`。Modify `lipidmix/arf/reader.py`, `lipidmix/mztab/evidence.py`, `lipidmix/mztab/dataset_state.py`。

**Interfaces:** `normalize_cell(cell: dict, source_rt_unit: str) -> dict`、`build_assay_evidence(ds, artifact: Path, adapter: dict) -> dict`。結果はspec assay-feature-evidence.v1とavailability/reasons。

- [x] RED: 2注入の異なる値、秒→分、重複キー、不明単位、列順入替え、対応不明、代表値だけのartifactを試験する。

```python
from lipidmix.analysis.assay_evidence import normalize_cell

def test_per_injection_rt_is_not_representative_rt():
    a = normalize_cell({"rt": 60., "m_z": 104.}, "second")
    b = normalize_cell({"rt": 66., "m_z": 104.001}, "second")
    assert (a["observed_rt_min"], b["observed_rt_min"]) == (1., 1.1)
    assert a["observed_mz"] != b["observed_mz"]
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_assay_evidence.py -q`。
- [x] Implement: 既存`arf.reader._convert_to_alignment_feature`のfile_id、peak_id、rt、m_z、gap-fill値を利用する。現在のKey15/16やKey22は対応版の構造を確認してadapterで限定する。スポット順と名前だけの既存evidence推定を、v2の厳密なID/source照合の代用にしない。

```python
def normalize_cell(cell, source_rt_unit):
    factors = {"minute": 1.0, "second": 1.0 / 60.0}
    if source_rt_unit not in factors:
        raise ValueError("unsupported RT unit")
    return {"observed_rt_min": None if cell.get("rt") is None else cell["rt"] * factors[source_rt_unit],
            "observed_mz": cell.get("m_z")}
```

公開境界ではValueErrorをEVIDENCE_UNIT_UNSUPPORTEDへ変換。SMFとのalignment ID対応、jobのsource→assay対応を照合できない場合、availability=falseと理由を保存する。SMF代表値を複製しない。全SME候補とlibrary識別/scoreもbindingが読むためDatasetStateに保存する。質量データの実検証はTask 15まで行わない。
- [x] GREEN: 上記と既存ARF/mzTab evidence tests。
- [x] Commit: 5ファイルをstageし、`git commit -m "feat: 注入単位の測定証拠を保持"`。

## Task 7: 論理targetをバッチ内featureへ対応付け

**Files:** Create `lipidmix/analysis/feature_bindings.py`, `tests/test_feature_bindings.py`。

**Interfaces:** `resolve_candidates(candidates: list[dict]) -> dict`、`bind_features(ds, profile: dict, evidence: dict, standard_assays: dict, overrides: dict | None) -> dict`。候補dictはfeature_id、qualified、reasons、evidence_refs。結果はfeature-bindings.v1。

- [x] RED: 以下に加え、同規則の2dataset、同名異性候補、adduct/charge不一致、証拠不足、別dataset overrideを拒否する。

```python
from lipidmix.analysis.feature_bindings import resolve_candidates

def test_two_qualified_candidates_need_input():
    out = resolve_candidates([{"feature_id": "1", "qualified": True},
                              {"feature_id": "2", "qualified": True}])
    assert out["status"] == "needs_input"
    assert out["selected"] is None
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_feature_bindings.py -q`。
- [x] Implement: m/z ppm、RT分、化合物ID、adduct/charge、必要証拠をANDで評価する。

```python
def resolve_candidates(candidates):
    valid = [c for c in candidates if c["qualified"]]
    return {"status": "resolved" if len(valid) == 1 else "needs_input",
            "selected": valid[0]["feature_id"] if len(valid) == 1 else None,
            "candidates": candidates}
```

overrideはqualified候補の選択に限定し、理由必須。0候補で無関係featureを強制採用しない。library_matchとauthentic_standard_matchはsource hash・score条件を満たす証拠を要求する。初期profileと解決結果のhashを混ぜない。
- [x] GREEN: 同テスト。0候補と複数候補のneeds_input理由が区別されることを確認。
- [x] Commit: 2ファイルをstageし、`git commit -m "feat: バッチごとのfeature対応付けを追加"`。

## Task 8: 内部標準比と版管理したmatrix

**Files:** Create `lipidmix/analysis/internal_standards.py`, `matrix_state.py`, `tests/test_internal_standards.py`, `tests/test_analysis_matrix.py`。Modify `lipidmix/analysis/preprocessing.py`, `result_state.py`。

**Interfaces:** `ratio(target: np.ndarray, standard: np.ndarray, standard_detected: np.ndarray | None) -> tuple[np.ndarray,np.ndarray]`（値、補完禁止mask）をinternal_standardsへ追加。`make_matrix(ds, recipe: dict, bindings: dict, evidence: dict, eligibility: np.ndarray) -> dict`（補完前）、`finalize_matrix(matrix: dict, eligibility: np.ndarray, impute: str) -> dict`（QC後filterと補完）、`save_matrix(result: dict, directory: Path) -> dict`、`load_matrix(reference: dict, directory: Path) -> dict`をmatrix_stateへ追加する。

- [x] RED: 0分母、欠損/非有限、検出要件、元値不変、support保持、rawと補正後の区別、hash改変を検査する。

```python
import numpy as np
from lipidmix.analysis.internal_standards import ratio

def test_invalid_denominator_is_locked_missing():
    values, locked = ratio(np.array([10., 10.]), np.array([2., 0.]), None)
    assert values[0] == 5 and np.isnan(values[1])
    assert locked.tolist() == [False, True]
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_internal_standards.py tests/test_analysis_matrix.py -q`。
- [x] Implement: ratio計算を元行列のコピーで実施する。

```python
valid = np.isfinite(standard) & (standard > 0)
if standard_detected is not None:
    valid &= standard_detected
out = np.full(target.shape, np.nan)
np.divide(target, standard, out=out, where=valid & np.isfinite(target))
locked = ~valid
```

npz（allow_pickle=False）＋JSON metaをatomic保存する。matrix IDはdataset、recipe、binding、evidence、metadata、eligibilityを含む内容hashで生成。axis順・値hash・mask形状をload時に確認する。統計対象filterはsupport列を削除しない。補完後にもlocked maskを復元し、補完不能の理由を残す。make_matrixは補完を行わず、finalize_matrixだけがQC後の補完を行う。両結果は別IDと親参照を持つ。recipeごとにmatrixを作り、同一ds.pp_matrixへ異なるrecipeを上書きしない。
- [x] GREEN: 上記と`tests/test_preprocessing.py tests/test_result_state.py`。
- [x] Commit: 対象6ファイル、`git commit -m "feat: 内部標準比と解析行列を永続化"`。

## Task 9: 固定母集団のQCとfilter分離

**Files:** Create `lipidmix/analysis/assay_qc.py`, `tests/test_assay_qc.py`。Modify `lipidmix/analysis/preprocess_policy.py`（v2の明示規則接続のみ）。

**Interfaces:** `aggregate_counts(p: int, f: int, u: int, threshold: float) -> str`、`evaluate_qc(matrix: dict, evidence: dict, metadata: list[dict], policy: list[dict], population: dict | None) -> dict`。populationにはmetric別のfeature/assay IDとhash。出力はmetric結果、batch状態、別のeligibility提案。

- [x] RED: 全6metric、標準品とQC混同、QC2本、mask不明、blank0、全欠損、補完後RSD禁止、filter後の分母保持。

```python
from lipidmix.analysis.assay_qc import aggregate_counts

def test_exclusion_does_not_turn_failed_batch_into_pass():
    assert aggregate_counts(60, 40, 0, .8) == "fail"
    assert aggregate_counts(60, 10, 30, .8) == "not_evaluable"
    assert aggregate_counts(0, 0, 0, .8) == "not_evaluable"
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_assay_qc.py -q`。
- [x] Implement:

```python
def aggregate_counts(p, f, u, threshold):
    n = p + f + u
    if not n:
        return "not_evaluable"
    if p / n >= threshold:
        return "pass"
    return "fail" if (p + u) / n < threshold else "not_evaluable"
```

生物群・batch・poolごとにspecの式を適用。infinite blank_foldはJSONでInfinityを出さず`value=null, special_value=positive_infinity`として閾値比較の意味を保持する。初回populationを後段も再利用し、metric不在はnot_evaluable。QC除外はmatrix eligibilityにだけ反映し、QC結果を再集計しない。drift前提不足はneeds_input理由を返す。
- [x] GREEN: 上記と`tests/test_preprocess_policy.py`。
- [x] Commit: 3ファイル、`git commit -m "feat: QC母集団と解析filterを分離"`。

**Review B:** A08〜A13、A21〜A26。2バッチでbindingが変わってもprofile不変、RTは注入単位、0分母は補完されず、filterでQC合格を作れないことを確認する。

## Task 10: v2統計と変換・効果量

**Files:** Create `lipidmix/analysis/statistics_v2.py`, `multigroup.py`, `tests/test_statistics_v2.py`, `tests/test_multigroup.py`。Modify `lipidmix/analysis/differential.py`（純粋数値部品の公開化のみ）, `pca.py`（v2専用引数/関数）, `dataset_analysis.py`。

**Interfaces:** `transform_values(values: np.ndarray, transform: str) -> np.ndarray`、`arithmetic_log2fc(a: np.ndarray, b: np.ndarray) -> float`、`run_statistic(matrix: dict, specification: dict, metadata: list[dict]) -> dict`。`multigroup.test_feature(groups: list[np.ndarray], alpha: float) -> dict`。

- [x] RED: log2 clip防止、算術比と幾何比、n不足、定数、反復生物ID、全件不能、PCA rank、BH母集団を検証する。

```python
import math
import numpy as np
from lipidmix.analysis.statistics_v2 import transform_values, arithmetic_log2fc

def test_v2_transform_and_effect_size():
    assert transform_values(np.array([.25, .5]), "log2").tolist() == [-2., -1.]
    assert math.isclose(arithmetic_log2fc(np.array([1., 9.]), np.array([4., 4.])),
                        math.log2(4 / 5))
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_statistics_v2.py tests/test_multigroup.py -q`。
- [x] Implement: scipyのf_onewayとtukey_hsd（等分散設定）を使い、Tukeyは全群対を一回で評価する。v1 two_group_testは変更せず、Welch/BH部品だけ共有する。

```python
def arithmetic_log2fc(a, b):
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if not len(a) or not len(b) or a.mean() <= 0 or b.mean() <= 0:
        return float("nan")
    return float(np.log2(b.mean() / a.mean()))
```

検定前に全指定群のn≥2を確認し、n不足群を落として別のANOVAにしない。F/df/p/q/Tukey mean difference/CI/p_adjustedを出す。正のTukey差はtest−referenceとし、SciPyの配列方向をadapterで変換する。固定小例`[1,2,3],[2,3,4],[4,5,6]`でF=7、df=(2,6)を手計算照合し、Tukey CI/pはRのTukeyHSDから事前生成した公開可能な合成期待値JSONと照合する。同一ライブラリを期待値生成と本計算の両方に使わない。
- [x] GREEN: 上記と既存differential/PCA tests。SciPyに必要APIがない場合は明示依存エラーとし、別統計へfallbackしない。
- [x] Commit: 上記7ファイル、`git commit -m "feat: v2統計の変換と多群比較を追加"`。

## Task 11: 全feature出力と三軸レポート

**Files:** Create `lipidmix/analysis/feature_export.py`, `tests/test_metabolomics_report.py`。Modify `lipidmix/analysis/dataset_export.py`, `lipidmix/pipeline/report.py`。

**Interfaces:** `export_features(ds, matrices: list[dict], path: Path) -> dict`、`export_statistic(result: dict, path: Path) -> dict`、`report.analysis_status(results: list[dict]) -> str`。全feature量はlong TSVでdataset_id/feature_id/assay_id/matrix_result_id/value/unit/detected/gap_filled/imputed/exclusion_reasonを出し、annotation/evidenceはfeature_idで別表へ接続。

- [x] RED: 全未同定、QC fail+統計ready、全不能、最新result不一致、hash破損、ANOVA専用出力を検査。

```python
from lipidmix.pipeline.report import analysis_status

def test_computable_and_uncomputable_are_limited():
    assert analysis_status([{"status": "ready"}, {"status": "not_evaluable"}]) == "limited"
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_report.py tests/test_pipeline_report.py -q`。
- [x] Implement:

```python
def analysis_status(results):
    ready = sum(r["status"] == "ready" for r in results)
    return "ready" if results and ready == len(results) else "limited" if ready else "not_evaluable"
```

必須出力はspec §11のmanifest/evidence/binding/matrix/QC/stat/report。pathway15列TSVは任意、NO_ANNOTATED_FEATURESはnot_applicable。欠損/±infを有意値にしない。注釈がない行を落とさず、rawとnormalized単位を表示する。既存report.evaluate_targetはv1分岐を維持し、v2必須成果物を別関数で列挙する。
- [x] GREEN: 同テストと既存export契約tests。
- [x] Commit: 4ファイル、`git commit -m "feat: 全feature出力とQC判定レポートを追加"`。

**Review C:** A14〜A17、A27。v1のpseudocount/15列契約が不変、v2の数値が独立期待値に一致し、全未同定でも出力が完成することを確認する。

## Task 12: workerへの接続・再開・依存無効化

**Files:** Create `lipidmix/pipeline/metabolomics_handlers.py`, `tests/test_metabolomics_engine.py`。Modify `lipidmix/pipeline/service.py`, `engine.py`, `recovery.py`, `worker.py`, `lipidmix/analysis/result_state.py`。

**Interfaces:** `metabolomics_handlers.build_handlers() -> dict[str, Callable]`。各handlerは既存contextを受け、既存outcome envelopeへ結果を返す。`restore_results(record: dict, root: Path, ds) -> dict`はcurrentなevidence/binding/matrixをhash検査して復元する。

- [x] RED: binding待ち→再開、standard_assays変更、二つのmatrix recipe、統計追加/削除、古い結果、restartをpytestのfake handlerで試験する。

```python
from lipidmix.pipeline.stage_plan import invalidated_v2

def test_binding_change_does_not_restart_console():
    dirty = invalidated_v2({"feature_bindings"}, {"statistics": [{"statistic_id": "s"}]})
    assert "resolve_feature_bindings" in dirty
    assert "statistics:s" in dirty
    assert "execute_console" not in dirty
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_engine.py tests/test_pipeline_recovery.py -q`。
- [x] Implement: v2 prepare/execute/validateは既存serviceの上流関数を共通部品として呼び、v1のLBM必須アクセスだけをprofile adapterへ移す。

```python
# service.build_handlersのschema別dispatch。既存v1ハンドラは維持する。
if request["schema"] == "pipeline-request.v2":
    handlers = metabolomics_handlers.build_handlers()
```

handler実装でTask 6→7→9(raw)→8→9(processed)→10→11を接続する。preprocess handlerはmake_matrixで補完前値を保存する。qc_processed handlerはその値でQCを評価してからfinalize_matrixを呼び、QC結果と最終matrixを一つのstage outcomeで確定する。統計は最終matrixだけを参照する。needs_inputはstage outcomeに保存し、同じ未解決条件で自動再試行しない。比較群変更は該当統計だけ、recipe変更はmatrix以降、metadataはresolve_metadata以降へ伝播する。QC集合を変えるmetadataではqc_rawも再生成する。

resultの保存はappend-onlyの既存仕組みを使い、runtime復元時に再計算した値で古いresult IDを上書きしない。Console終了証跡・入力不変・sample照合の成功前に下流へ進まない。
- [x] GREEN: 新テストと既存engine/recovery/service tests。
- [x] Commit: 上記7ファイル、`git commit -m "feat: メタボロミクスstageをworkerへ接続"`。

## Task 13: MCP公開経路と操作docs

**Files:** Modify `lipidmix/tools/pipeline_tools.py`, `lipidmix/tools/dataset_analysis_tools.py`, `lipidmix/analysis/dataset_service.py`, `server.py`。Create `tests/test_metabolomics_tools.py`, `docs/workflow/metabolomics.md`。

**Interfaces:** 既存pipeline_*の引数は維持しrequest v2を受ける。追加`dataset_statistic(specification: dict, matrix_result_id: str) -> str`はTask 10を呼ぶ薄いMCP入口。workerはこのtoolを呼ばない。

- [x] RED: registration、read-only plan、routine拒否、needs_input詳細、status三軸、MCPでのmatrix ID不一致を検査する。

```python
from lipidmix.pipeline.metabolomics_handlers import build_handlers

def test_worker_handlers_cover_new_stages():
    assert {"load_assay_evidence", "resolve_feature_bindings", "statistics"} <= set(build_handlers())
```

- [x] Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_tools.py tests/test_pipeline_tools.py tests/test_server_registration.py -q`。
- [x] Implement: dataset_serviceに`statistic_dataset(ds, specification, matrix_result_id)`を追加し、current matrixを検索してTask 10へ渡す。

```python
# MCP側では数値を再実装せず、既存JSON envelopeへ変換する。
result = dataset_service.statistic_dataset(ds, specification, matrix_result_id)
```

profile不足、標準候補、任意QC不能を利用者が区別できる説明を返す。docsへ合成profile/request/manifest、plan→run→status→binding resume、失敗・未検証の意味を記載する。既存ARF/dataset API既定値を変更しない。
- [x] GREEN: 上記とreadme/workflow文書tests。
- [x] Commit: 6ファイル、`git commit -m "feat: MCPからv2メタボロミクス解析を公開"`。

## Task 14: 合成E2Eと互換性・プロセス回帰

**Files:** Create `tests/metabolomics_fixtures.py`, `tests/test_metabolomics_end_to_end.py`。Modify `tests/test_pipeline_process_lifecycle.py`（v2 case追加のみ）。

**Interfaces:** `MetabolomicsHarness(tmp_path)`、`run(binding_mode="unique", annotated=True, qc_fail=False) -> dict`、`resume(updates: dict) -> dict`、属性`launch_count`。既存tests.pipeline_fixturesのfake Consoleの仕組みを再利用する。

- [ ] RED: 以下と、QC fail、全feature不能、2バッチ異ID、統計追加削除、timeout/cancel、二重runを検査する。

```python
from tests.metabolomics_fixtures import MetabolomicsHarness

def test_new_binding_resumes_without_second_console(tmp_path):
    h = MetabolomicsHarness(tmp_path)
    first = h.run(binding_mode="ambiguous", annotated=False)
    assert first["status"] == "needs_input"
    h.resume({"feature_bindings": first["allowed_binding_update"]})
    assert h.launch_count == 1
```

- [ ] Run: `C:/Python314/python.exe -m pytest tests/test_metabolomics_end_to_end.py -q`。
- [ ] Implement fixture: 3生物群×3注入、QC3、blank1、standard2、対象2featureと内部標準1を生成する。値は合成、QC fail用はQC値[1,10,100]、標準分母欠損caseを別に持つ。

```python
raw_values = {"target": [1., 2., 3., 2., 3., 4., 4., 5., 6.],
              "standard": [2.] * 9}
```

fixtureはmockの成功JSONだけを返さず、fake Consoleが実際に生成したmzTab/証拠をread/loadし、workerの保存物を検証する。親終了後の完了をstatus呼出しなしで確認するcaseは通常Windowsプロセスで実施する。
- [ ] GREEN: 対象後、`C:/Python314/python.exe -m pytest tests -q`。既知環境障害は正常環境の新規実行で切り分け、失敗をPASS扱いしない。
- [ ] Commit: 3ファイル、`git commit -m "test: メタボロミクスE2Eと再開を検証"`。

**Review D:** A01〜A28。全suite、fresh-process、出力hash、失敗時レポートを確認。この段階の表記は「合成入力での実装・試験完了」。実Console未接続ならソフトウェア完成としない。

## Task 15: 実Console接続と互換性記録

**Files:** Create `lipidmix/console/compatibility.py`, `tests/test_lcms_compatibility.py`。Modify `docs/workflow/metabolomics.md`。実データ成果物はローカル`analysis/metabolomics-extension/kanzo-20260914/validation/compatibility/`へ保存。

**Interfaces:** `validate_compatibility(report: dict) -> None`。報告schemaはlcms-console-compatibility.v1で環境/input/method/library/output hash、raw形式、注入証拠availability、各checkのpass/failを持つ。

- [ ] RED: exit0のみでは合格しないことを試験する。

```python
import pytest
from lipidmix.core.atomic_io import DomainError
from lipidmix.console.compatibility import validate_compatibility

def test_exit_zero_is_not_compatibility_proof():
    with pytest.raises(DomainError):
        validate_compatibility({"schema": "lcms-console-compatibility.v1", "exit_code": 0})
```

- [ ] Run: `C:/Python314/python.exe -m pytest tests/test_lcms_compatibility.py -q`。
- [ ] Implement: required checksを`raw_read, library_read, height_export, assay_mapping, smf_sme_integrity`とし、一つでも欠ける/不合格なら合格証明にしない。注入証拠は独立した能力欄であり、取得できなければ当該QC機能を未対応にする。

```python
REQUIRED_CHECKS = {"raw_read", "library_read", "height_export", "assay_mapping", "smf_sme_integrity"}
# 全要素の存在・pass、全hash形式、実成果物hash一致を検査してから保存する。
```

- [ ] GREEN後、ローカルコピーから標準品2注入以上を選び、出典確認済みmethodと依存を固定する。現在method一式は未確定なので、特定できるまで実行しない。資料不足はPROFILE_INCOMPLETEとして記録し、架空の設定で進めない。
- [ ] ms-data-parser MCPの`pipeline_plan`へdataset_rootとrequest v2（execution_purpose=validation）を渡す。計画出力のraw形式、依存、assay、methodを確認する。その後の実行工程で`pipeline_run`を使用し、statusから終了・出力参照を検証する。今回はこの呼出しを実行しない。
- [ ] MSP/TXT参照を変えた対照入力で、既知標準annotationの反映を確認する。入力原本を変更せず、別snapshotを使う。標準RT/mzを2注入で照合し、未対応raw形式は対応済みにしない。
- [ ] 合格/不合格・入力不足を互換性報告へ保存。非公開情報はローカル保持。コード3ファイルだけcommitし、`git commit -m "feat: 実Console互換性の検証記録を追加"`。

## Task 16: profileの科学的検証と引渡し

**Files:** Modify `docs/workflow/metabolomics.md`。ローカルのみ作成 `analysis/metabolomics-extension/kanzo-20260914/validation/profile/criteria.json`, `profile.json`, `certificate.json`, `validation-report.md`。実データを含むファイルはgit addしない。

**Interfaces:** Task 1のvalidate_certificateとTask 15互換性報告を消費する。profile改訂前後でraw/method/library/criteria/output hashを証明書へ記録する。

- [ ] コピー済み試料表とrawの不一致を根拠付きmanifestで解消する。名前の日付を推測修正しない。LCmethod 1/2を別runへ分ける。
- [ ] 検証基準にtarget、内部標準、比較群、変換、参照出力のセル/feature対応、許容誤差、必須QCを記載し、実行前に固定する。機器や元表の根拠なく一律誤差を設定しない。
- [ ] MCPでvalidation実行し、GABA/GABA-d6、各群n、ANOVA/Tukey、height出力を参照結果と照合する。元解析の群除外や変換と違う場合は差分を記録し、再現と呼ばない。
- [ ] 基準の必須項目が全passならTask 1の証明書検査を通し、validation欄だけ更新する。確認できないpooled QCやドリフト能力は検証範囲に入れない。欠落が残ればdraftのまま引き渡す。
- [ ] 最終レポートで、ソフトウェア試験、Console接続、科学的profile検証、個別バッチQCを分ける。docs変更だけをstageし、`git commit -m "docs: メタボロミクス運用と検証範囲を記録"`。

**Review E:** A29とprofile検証。未取得method情報や未検証QC能力を「完成」として隠さない。実データ制約でTask 15/16を完了できなければ、その条件と完了済み範囲を記録する。

## specと受け入れ条件の対応

| spec / AC | 主Task |
|---|---|
| §1〜3 範囲・論文根拠・完成条件 | 15,16 |
| §4〜5 profile・合理化・依存、A02〜A06 | 1,2,3 |
| §6 schema/stage/再開、A01,A18,A19,A22,A28 | 3,4,12,14 |
| §7 試料、A07,A08,A15 | 2,5,14 |
| §8 binding/matrix、A09,A10,A21,A25,A26 | 7,8,12 |
| §9 QC、A12,A13,A23,A24 | 6,9 |
| §10 数値、A11,A14,A27 | 10 |
| §11 出力、A16,A17,A20 | 8,11,14 |
| §12 MCP/接続 | 12,13 |
| §13 回帰条件 | 1〜14 |
| §14 接続・科学的検証、A29 | 15,16 |
| §15 v1優先関係 | 3,4,10,11,14 |

## 最終検証コマンドと完了の記録

```powershell
C:/Python314/python.exe -m pytest tests -q
git diff --check
git status --short
```

全テスト実行はコード実装後の工程であり、本plan作成時には再実行しない。実装diff、テスト結果、A01〜A29の証拠位置、Task 15/16の完了状態を報告する。実装の進め方は、レビュー区切りを保つ同一タスク内実行、または承認されたsubagent分担を選べる。いずれも本plan作成だけを理由にコード変更を開始しない。
