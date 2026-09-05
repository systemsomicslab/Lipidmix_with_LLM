"""pipeline-request.v1 の厳密な検証・解決・更新契約（spec §10.1）。

このモジュールは以降のTask（13〜18）がrequestを読み書きする唯一の入口になる。
守るべき区別は次の2つ:

1. **未指定 と 明示null の区別**。例えば ``sample_manifest`` を省略すると
   「既定シート名を探索する」(既定探索)ことを意味し、明示的に ``null`` を
   渡すと「探索せず自動一覧生成へ切り替える」(明示解除)ことを意味する——
   両者ともPython上の値は``None``だが、``value_sources`` の出所
   （``"default"`` か ``"explicit"``/``"explicit_update"``）で区別する。
   同様に ``preprocess.blank_min_fold`` / ``max_qc_rsd`` は明示 ``null`` で
   無効化でき、``normalize=none`` ``drift_correct=false``
   ``min_detection_rate=0.0`` も明示無効化として ``auto`` と区別される
   （値そのものは既定と一致しうるので、ここでも出所が唯一の手がかり）。

2. **要求済みの上流条件 と resumeで訂正可能な下流条件の区別**。
   ``UPDATABLE`` に挙げた4キー（target・sample_manifest・preprocess・
   comparisons）だけが ``merge_updates`` で変更でき、それ以外
   （method_file・lbm_file・polarity・measure・keep_extension・timeout_s・
   save_project・output_root、および内部専用キーeffective_target・
   value_sources）を変えようとすると ``NEW_PIPELINE_REQUIRED`` になる——
   上流の実行条件が変わるなら別pipelineを作るべきで、resumeを
   「同じ入力を繰り返し送るだけの無限ループ」にしないための境界線。

解決順序は「明示値 > (前revisionを読み戻した)既存値 > 既定値」の3段。
``resolve_request`` が最初の revision を「既定値」から作り、
``merge_updates`` がその戻り値（＝保存され読み戻された「既存値」）へ
新しい明示値を重ねる、という形でこの3段が表現される。

不正JSON・未知キー・不正値はこの場でDomainError（コード
``PIPELINE_REQUEST_INVALID``）にする。上流情報が単に「まだ無い」ケースを
``missing_state`` 風の封筒に化けさせて同じ呼び出しを無限反復させるのは
後続タスク（pipeline_plan/pipeline_run, Task 13/14）の責務であり、ここでは
やらない。
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path

from lipidmix.core.atomic_io import DomainError, canonical_hash

__all__ = [
    "SCHEMA",
    "UPDATABLE",
    "merge_updates",
    "request_fingerprint",
    "resolve_request",
    "validate_request",
]

SCHEMA = "pipeline-request.v1"

#: resumeで変更可能なトップレベルキー（spec §10.1: 「resumeで変更可能なのは
#: target、sample_manifest、preprocess、comparisonsに限定する」）。
UPDATABLE = {"target", "sample_manifest", "preprocess", "comparisons"}

#: request/updatesが受け付けるトップレベルキー（spec §10.1本文の列挙そのまま）。
_TOP_LEVEL_KEYS = frozenset({
    "schema", "target", "method_file", "lbm_file", "polarity", "measure",
    "keep_extension", "timeout_s", "save_project", "output_root",
    "sample_manifest", "preprocess", "comparisons",
})

#: 内部でだけ使う2キー。外部入力（explicit/updates）には現れてはいけない。
_INTERNAL_KEYS = frozenset({"effective_target", "value_sources"})

_TARGET_VALUES = frozenset({"auto", "exploratory", "differential"})
_POLARITY_VALUES = frozenset({"positive", "negative"})
_MEASURE_VALUES = frozenset({"peak_height"})  # 初期版はpeak_heightのみ(spec §10.1)
_NORMALIZE_VALUES = frozenset({"auto", "none", "tic", "median", "pqn"})
_IMPUTE_VALUES = frozenset({"none", "half_min", "knn", "column_mean"})
#: 自動前処理は現行版ではconservative-v1のみ（common-context.md）。
_POLICY_VALUES = frozenset({"conservative-v1"})

_PREPROCESS_KEYS = frozenset({
    "policy", "normalize", "blank_min_fold", "drift_correct", "max_qc_rsd",
    "impute", "min_detection_rate",
})

_COMPARISON_KEYS = frozenset({
    "comparison_id", "reference_group", "test_group", "q_threshold",
    "log2fc_threshold", "log_transform", "allow_confounded",
})
_COMPARISON_REQUIRED_KEYS = frozenset({"comparison_id", "reference_group", "test_group"})

#: comparison_idは結果ディレクトリ名やstage id(`export:<comparison_id>`等)に
#: 使うため、パス区切り・"."を含む脱出パターンを一切許さない安全な文字だけに絞る。
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_DEFAULT_PREPROCESS = {
    "policy": "conservative-v1", "normalize": "auto", "blank_min_fold": "auto",
    "drift_correct": "auto", "max_qc_rsd": "auto", "impute": "half_min",
    "min_detection_rate": 0.0,
}

#: 個別comparisonの未指定時デフォルト(comparison_id/reference_group/test_groupは
#: 必須で既定を持たない)。既存dataset_analysis_tools.dataset_differentialの
#: 既定(q_threshold=0.05, log2fc_threshold=1.0, log_transform=True)と揃える。
_COMPARISON_DEFAULTS = {
    "q_threshold": 0.05, "log2fc_threshold": 1.0,
    "log_transform": True, "allow_confounded": False,
}

_DEFAULT_TIMEOUT_S = 21600  # 既存Consoleの既定（common-context.md）に合わせる


def _fail(message: str, **details) -> None:
    raise DomainError("PIPELINE_REQUEST_INVALID", message, details)


def _is_finite_number(value: object) -> bool:
    """bool・非数値・NaN/Infinityを除いた「本物の有限数値」だけを真にする。

    Pythonの``bool``は``int``のサブクラスなので、素朴な``isinstance(v, (int, float))``
    だけでは``True``/``False``がしきい値として通ってしまう——timeout_sや
    各種閾値でboolを拒否するという契約の核心はここにある。
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


# ---------- preprocess ----------

def _validate_preprocess(preprocess: object) -> dict:
    """conservative-v1の既定へ指定キーだけを重ね、全キーを検証して返す。

    ``preprocess``がNoneなら「何も指定されていない」ことを表す（resolve_requestの
    初回解決、またはmerge_updatesの対象外フィールド）。dict以外・未知キーは
    ここで拒否する。返り値は必ず7キー全部そろった新しいdict（呼び出し側の
    dictを書き換えない）。
    """
    if preprocess is None:
        preprocess = {}
    if not isinstance(preprocess, dict):
        _fail("preprocessはオブジェクトである必要があります。", value=preprocess)
    unknown = set(preprocess) - _PREPROCESS_KEYS
    if unknown:
        _fail(f"preprocessに未知のキーがあります: {sorted(unknown)}",
              unknown_keys=sorted(unknown))

    merged = dict(_DEFAULT_PREPROCESS)
    merged.update(preprocess)

    if merged["policy"] not in _POLICY_VALUES:
        _fail(f"preprocess.policyが不正です: {merged['policy']!r}",
              policy=merged["policy"])
    if merged["normalize"] not in _NORMALIZE_VALUES:
        _fail(f"preprocess.normalizeが不正です: {merged['normalize']!r}",
              normalize=merged["normalize"])
    if merged["impute"] not in _IMPUTE_VALUES:
        _fail(f"preprocess.imputeが不正です: {merged['impute']!r}",
              impute=merged["impute"])

    drift_correct = merged["drift_correct"]
    if not (drift_correct == "auto" or isinstance(drift_correct, bool)):
        _fail(f"preprocess.drift_correctが不正です: {drift_correct!r}",
              drift_correct=drift_correct)

    # blank_min_fold・max_qc_rsdだけは明示nullで無効化できる（自動閾値の停止）。
    # "auto"文字列・None・有限の正数、以外はすべて拒否する。
    for field in ("blank_min_fold", "max_qc_rsd"):
        value = merged[field]
        if not (value == "auto" or value is None
                or (_is_finite_number(value) and value > 0)):
            _fail(f"preprocess.{field}が不正です: {value!r}", **{field: value})

    min_detection_rate = merged["min_detection_rate"]
    if not (_is_finite_number(min_detection_rate)
            and 0.0 <= float(min_detection_rate) <= 1.0):
        _fail(f"preprocess.min_detection_rateが不正です: {min_detection_rate!r}",
              min_detection_rate=min_detection_rate)

    return merged


def _preprocess_sources(explicit_preprocess: object) -> dict:
    """preprocessの子キーごとの出所（explicit/default）を返す。

    型が不正な場合でも例外を投げない（後続の``_validate_preprocess``が
    正しく拒否するので、ここでは安全側に倒して"default"扱いにするだけでよい）。
    """
    keys = explicit_preprocess if isinstance(explicit_preprocess, dict) else {}
    return {key: ("explicit" if key in keys else "default") for key in _PREPROCESS_KEYS}


# ---------- comparisons ----------

def _validate_comparisons(comparisons: object) -> list:
    """配列全体を検証し、各要素へ未指定フィールドの既定値を埋めて返す。

    ``comparisons``は「置換」対象（部分更新はない）なので、ここでは常に
    配列全体を一から検証・正規化する。comparison_id重複・パスとして安全でない
    ID・reference_group==test_groupは1件でもあれば例外にする。
    """
    if comparisons is None:
        comparisons = []
    if not isinstance(comparisons, list):
        _fail("comparisonsは配列である必要があります。", value=comparisons)

    normalized: list = []
    seen_ids: set = set()
    for index, item in enumerate(comparisons):
        if not isinstance(item, dict):
            _fail(f"comparisons[{index}]はオブジェクトである必要があります。", index=index)
        unknown = set(item) - _COMPARISON_KEYS
        if unknown:
            _fail(f"comparisons[{index}]に未知のキーがあります: {sorted(unknown)}",
                  index=index, unknown_keys=sorted(unknown))
        missing = _COMPARISON_REQUIRED_KEYS - set(item)
        if missing:
            _fail(f"comparisons[{index}]に必須キーが不足しています: {sorted(missing)}",
                  index=index, missing_keys=sorted(missing))

        comparison_id = item["comparison_id"]
        if not isinstance(comparison_id, str) or not _SAFE_ID_RE.fullmatch(comparison_id):
            _fail(
                f"comparison_idが不正です（安全な文字だけのIDが必要・パス脱出不可）: "
                f"{comparison_id!r}",
                index=index, comparison_id=comparison_id,
            )
        if comparison_id in seen_ids:
            _fail(f"comparison_idが重複しています: {comparison_id!r}",
                  comparison_id=comparison_id)
        seen_ids.add(comparison_id)

        reference_group = item["reference_group"]
        test_group = item["test_group"]
        if not isinstance(reference_group, str) or not reference_group:
            _fail(f"reference_groupが不正です: {reference_group!r}", index=index)
        if not isinstance(test_group, str) or not test_group:
            _fail(f"test_groupが不正です: {test_group!r}", index=index)
        if reference_group == test_group:
            _fail(
                f"reference_groupとtest_groupが同一です（比較の向きを明示できません）: "
                f"{reference_group!r}",
                index=index, group=reference_group,
            )

        q_threshold = item.get("q_threshold", _COMPARISON_DEFAULTS["q_threshold"])
        if not (_is_finite_number(q_threshold) and 0 < float(q_threshold) <= 1):
            _fail(f"q_thresholdが不正です: {q_threshold!r}",
                  index=index, q_threshold=q_threshold)

        log2fc_threshold = item.get("log2fc_threshold", _COMPARISON_DEFAULTS["log2fc_threshold"])
        if not (_is_finite_number(log2fc_threshold) and float(log2fc_threshold) >= 0):
            _fail(f"log2fc_thresholdが不正です: {log2fc_threshold!r}",
                  index=index, log2fc_threshold=log2fc_threshold)

        log_transform = item.get("log_transform", _COMPARISON_DEFAULTS["log_transform"])
        if not isinstance(log_transform, bool):
            _fail(f"log_transformはboolのみ許可されます: {log_transform!r}", index=index)

        allow_confounded = item.get("allow_confounded", _COMPARISON_DEFAULTS["allow_confounded"])
        if not isinstance(allow_confounded, bool):
            _fail(f"allow_confoundedはboolのみ許可されます: {allow_confounded!r}", index=index)

        normalized.append({
            "comparison_id": comparison_id,
            "reference_group": reference_group,
            "test_group": test_group,
            "q_threshold": float(q_threshold),
            "log2fc_threshold": float(log2fc_threshold),
            "log_transform": log_transform,
            "allow_confounded": allow_confounded,
        })
    return normalized


# ---------- トップレベルフィールド ----------

def _validate_optional_str(data: dict, field: str) -> None:
    value = data.get(field)
    if value is not None and (not isinstance(value, str) or value == ""):
        _fail(f"{field}は空でない文字列またはnullである必要があります: {value!r}",
              **{field: value})


def _validate_fields(data: dict) -> None:
    """dataが13キー全部そろった形である前提で、型・enum・数値域を全検査する。

    ここで``data["preprocess"]``/``data["comparisons"]``を正規化済みの値へ
    書き換える（呼び出し側の意図した「返り値は常に完全な形」を満たすため）。
    """
    if data.get("schema") != SCHEMA:
        _fail(f"schemaは{SCHEMA!r}のみ許可されます。", schema=data.get("schema"))

    target = data.get("target")
    if target not in _TARGET_VALUES:
        _fail(f"targetが不正です: {target!r}", target=target)

    for field in ("method_file", "lbm_file", "output_root", "sample_manifest"):
        _validate_optional_str(data, field)

    polarity = data.get("polarity")
    if polarity is not None and polarity not in _POLARITY_VALUES:
        _fail(f"polarityが不正です: {polarity!r}", polarity=polarity)

    measure = data.get("measure")
    if measure not in _MEASURE_VALUES:
        _fail(f"measureが不正です: {measure!r}", measure=measure)

    _validate_optional_str(data, "keep_extension")

    timeout_s = data.get("timeout_s")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
        _fail(f"timeout_sは正の整数である必要があります（boolは不可）: {timeout_s!r}",
              timeout_s=timeout_s)

    save_project = data.get("save_project")
    if not isinstance(save_project, bool):
        _fail(f"save_projectはboolのみ許可されます: {save_project!r}",
              save_project=save_project)

    data["preprocess"] = _validate_preprocess(data.get("preprocess"))
    data["comparisons"] = _validate_comparisons(data.get("comparisons"))


def _compute_effective_target(target: str, comparisons: list) -> str:
    """spec §9: target=autoは、有効な比較定義があればdifferential、なければ
    exploratoryへ固定する。target自体が明示されていればそれを採る。"""
    if target != "auto":
        return target
    return "differential" if comparisons else "exploratory"


def _promote_value_sources(value_sources: dict, updated_fields: dict) -> None:
    """updated_fieldsに挙げたフィールドの出所をexplicit_updateへ書き換える。

    preprocessだけは子キー単位（updates["preprocess"]に実際に含まれていた
    キーだけ）を更新し、挙げられなかった子キー・トップレベルフィールドの
    出所には一切触れない。
    """
    for key, value in updated_fields.items():
        if key == "preprocess":
            sub_sources = value_sources.setdefault("preprocess", {})
            for sub_key in value:
                sub_sources[sub_key] = "explicit_update"
        else:
            value_sources[key] = "explicit_update"


# ---------- 公開API ----------

def validate_request(data: dict, *, internal: bool = False,
                      updated_fields: dict | None = None) -> dict:
    """要求の形・値を検証し、``effective_target``を(再)計算して返す。

    ``internal=False``（既定）は外部入力の検査専用モード。``data``に
    ``effective_target``/``value_sources``という内部専用キーが含まれていたら
    （＝未知キーとして）拒否する。

    ``internal=True``は「検証済みの要求を更新したあとの再検証」専用。
    ``data``は既に``value_sources``/``effective_target``を持つ完全な要求で
    ある前提とし、``effective_target``を最新のtarget/comparisonsから
    再計算し、``updated_fields``に挙げたフィールドの``value_sources``を
    ``"explicit_update"``へ書き換える（挙げられなかったフィールドの出所は
    保持する）。
    """
    allowed = _TOP_LEVEL_KEYS | _INTERNAL_KEYS if internal else _TOP_LEVEL_KEYS
    unknown = set(data) - allowed
    if unknown:
        _fail(f"未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))

    if internal:
        missing = {"value_sources", "effective_target"} - set(data)
        if missing:
            _fail(f"internal=Trueの検証にはvalue_sources/effective_targetが必要です"
                  f"（不足: {sorted(missing)}）。", missing=sorted(missing))

    _validate_fields(data)

    data["effective_target"] = _compute_effective_target(data["target"], data["comparisons"])

    if internal and updated_fields:
        _promote_value_sources(data["value_sources"], updated_fields)

    return data


def resolve_request(source_root: Path, explicit: dict | None = None) -> dict:
    """要求を既定値で解決し、``value_sources``/``effective_target``を付けて返す。

    ``source_root``はTask 13/14以降が相対パス（method_file・sample_manifest等）
    を実解決する基準として持ち回るためだけに受け取る——このtask自体は
    パス脱出検証をsource_root基準では行わない（comparison_idを除く各パス系
    フィールドは、相対と絶対のどちらも許す。spec §10.1の例で``method_file``に
    ラボ共有フォルダの絶対パスが使われている通り、source_rootの外を指す
    正当なケースがあるため）。
    """
    source_root = Path(source_root)  # 型を揃えるだけ（未使用に見えるが公開契約）。
    explicit = explicit if explicit is not None else {}
    if not isinstance(explicit, dict):
        _fail("requestはオブジェクトである必要があります。", value=explicit)

    unknown = set(explicit) - _TOP_LEVEL_KEYS
    if unknown:
        _fail(f"未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))

    data = {
        "schema": explicit.get("schema", SCHEMA),
        "target": explicit.get("target", "auto"),
        "method_file": explicit.get("method_file"),
        "lbm_file": explicit.get("lbm_file"),
        "polarity": explicit.get("polarity"),
        "measure": explicit.get("measure", "peak_height"),
        "keep_extension": explicit.get("keep_extension"),
        "timeout_s": explicit.get("timeout_s", _DEFAULT_TIMEOUT_S),
        "save_project": explicit.get("save_project", True),
        "output_root": explicit.get("output_root"),
        "sample_manifest": explicit.get("sample_manifest"),
        "preprocess": explicit.get("preprocess"),
        "comparisons": explicit.get("comparisons"),
    }

    value_sources = {
        key: ("explicit" if key in explicit else "default")
        for key in _TOP_LEVEL_KEYS if key not in ("preprocess", "comparisons")
    }
    value_sources["preprocess"] = _preprocess_sources(explicit.get("preprocess"))
    value_sources["comparisons"] = "explicit" if "comparisons" in explicit else "default"

    validated = validate_request(data, internal=False)
    validated["value_sources"] = value_sources
    return validated


def merge_updates(request: dict, updates: dict) -> dict:
    """resumeの入力訂正を反映し、再検証した要求を返す（spec §9/§10.1）。

    ``UPDATABLE``（target・sample_manifest・preprocess・comparisons）以外の
    キーを変えようとした場合は、上流の実行条件そのものの変更とみなし
    ``NEW_PIPELINE_REQUIRED``にする——effective_target/value_sourcesという
    内部キーも``UPDATABLE``に含まれないため、ここで同じ扱いになる
    （updates自身に内部キーを許可しないという契約を、UPDATABLEの外側として
    自然に満たす）。
    """
    if set(updates) - UPDATABLE:
        raise DomainError("NEW_PIPELINE_REQUIRED", "上流条件の変更には新しい解析が必要です")
    out = copy.deepcopy(request)
    for key, value in updates.items():
        if key == "preprocess":
            # out[key].update(value) より前に、更新値がdictであること・
            # そのキーが既知の子キーだけであることを確認する
            # （検証前にupdate()してしまうと不正な子キーが紛れ込む）。
            if not isinstance(value, dict):
                _fail("preprocessの更新値はオブジェクトである必要があります。", value=value)
            unknown = set(value) - _PREPROCESS_KEYS
            if unknown:
                _fail(f"preprocessに未知の更新キーがあります: {sorted(unknown)}",
                      unknown_keys=sorted(unknown))
            out[key].update(value)
        else:
            out[key] = copy.deepcopy(value)
    return validate_request(out, internal=True, updated_fields=updates)


def request_fingerprint(request: dict) -> str:
    """要求の内容hashを返す（`value_sources`/`effective_target`のような
    由来情報は対象から外す)。

    出所がexplicit/default/explicit_updateのどれであっても、最終的な
    フィールドの値が同じなら同一内容として扱う——Task 14がこのhashで
    run/request_idの再送・冪等性を判定する土台になるため、由来だけの違いで
    別内容と誤判定してはいけない。
    """
    content = {key: request[key] for key in _TOP_LEVEL_KEYS if key in request}
    return canonical_hash(content)
