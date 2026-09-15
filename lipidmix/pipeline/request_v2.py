"""pipeline-request.v2 の厳密な検証・解決・更新契約（spec §6, §6.2）。

v1（`lipidmix.pipeline.request`）が生データ由来の`method_file`/`lbm_file`を
直接受け取るのに対し、v2は検証済みLC–MSプロファイル（`lcms-profile.v1`、
`lipidmix.console.profile_schema`）を上流条件の唯一の出所とする。上流条件
（method・依存・極性・measure）はprofile経由でのみ解決し、v2ではこれらを
requestへ直接指定できない。

**このモジュールはファイルを読まない**（`lipidmix.console.profile_schema`と
同じ規約）。`profile_file`が指す実体の読込・検証・hash計算は呼び出し側の
責務——ここでは既に検証済みのprofile dict（`profile_schema.validate_profile`の
戻り値と同じ形）を受け取るだけ。呼び出し側の解決経路（`profile_file`の実パス
解決・`load_profile`呼び出し）は本タスクの範囲外（後続タスクで
`pipeline_plan`/`pipeline_run`側が担う）。同様に、`analysis-request.json`に
よるファイル層の読込みもここでは行わない——v1の`resolve_request`が
`explicit["schema"] == SCHEMA`を検出した時点で`resolve(explicit, profile)`へ
委譲する（`lipidmix/pipeline/request.py`参照）。

## 3段ではなく4段の優先順位

v1は「明示値 > analysis-request.json > 既定値」の3段だが、v2は
`statistics`に限り「MCP明示値 > analysis-request.json > profile既定値 >
v2既定値」の4段になる（spec §6）。analysis-request.jsonの層は前述のとおり
本タスクでは未配線なので、実際に効くのは「明示値 > profile既定値 >
v2既定値」の3段——`value_sources["statistics"]`にどの段が採用されたかを
`"explicit"` / `"profile_default"` / `"v2_default"` として記録する。

## routine許容範囲の解釈（controller裁定が必要な曖昧箇所への対応）

spec §6「routineで証明書の許容範囲外になる場合は`PROFILE_SCOPE_MISMATCH`」・
`docs/schema/lcms-profile-v1.md`「routine実行が上書きできる範囲」は、
`matrix_recipes`/`feature_targets`/`analysis_recipe`/`qc_policy`をprofileの
固定契約と位置づけ、routineが実行時に選べるのは既存recipe/statisticの
「選択」と`preprocess`の明示上書きに限る、とだけ述べる——だが「許容範囲」を
機械的に判定する具体的な集合は、証明書schema（`profile_schema._CERTIFICATE_KEYS`）
にも profile schema にも存在しない。Task 1の報告書・schema文書はこの実装を
明示的に本タスク（request v2）へ委譲している。

ここで採る解釈: **routine実行では、`preprocess`上書きの各フィールド値は
profile自身が持ついずれかの`matrix_recipes`エントリに既に現れる値でなければ
ならない**（そのフィールドについて、profile内のどのrecipeも使っていない
値への上書きは、証明書が検証した範囲の外に出るとみなす）。`execution_purpose
="validation"`ではこの制限を外す——validation目的は新しい条件を検証する
ためのものであり、まだprofileに無い値を試すことこそが目的だからである。
"""
from __future__ import annotations

import copy
import math
import re

from lipidmix.core.atomic_io import DomainError

__all__ = [
    "SCHEMA",
    "UPDATABLE",
    "merge_updates",
    "resolve",
    "validate_statistics",
]

SCHEMA = "pipeline-request.v2"

#: resumeで変更可能なトップレベルキー（spec §6.1「sample manifest・許可された
#: 前処理・統計定義・§8.1のバッチ固有対応付けの修正は新request revisionで
#: 再開する」）。feature_bindings自体はrequestの一部ではない別の仕組み
#: （§6.2の`resolve_feature_bindings`stageとpipeline_resumeの専用payload）
#: なのでここには含めない。
UPDATABLE = {"target", "sample_manifest", "preprocess", "statistics", "standard_assays"}

_TOP_LEVEL_KEYS = frozenset({
    "schema", "omics", "profile_file", "execution_purpose", "target",
    "sample_manifest", "standard_assays", "preprocess", "statistics",
    "timeout_s", "save_project", "output_root", "keep_extension",
})

#: 内部専用（merge_updates内部でのみ現れる。外部入力には許可しない）。
_INTERNAL_KEYS = frozenset({"effective_target", "value_sources"})

_OMICS_VALUES = frozenset({"metabolomics"})
_EXECUTION_PURPOSE_VALUES = frozenset({"routine", "validation"})
_TARGET_VALUES = frozenset({"auto", "exploratory", "differential"})

_STATISTIC_KINDS = frozenset({"pca", "welch", "anova_tukey"})
_TRANSFORM_VALUES = frozenset({"none", "log2"})
_STATISTIC_COMMON_KEYS = frozenset({
    "statistic_id", "kind", "matrix_recipe_id", "transform", "feature_scope",
})
_STATISTIC_EXTRA_KEYS = {
    "pca": frozenset({"scaling", "n_components"}),
    "welch": frozenset({"reference_group", "test_group", "q_threshold", "log2fc_threshold"}),
    "anova_tukey": frozenset({"groups", "alpha"}),
}

_PREPROCESS_OVERRIDE_KEYS = frozenset({"normalize", "drift_correct", "filter", "impute"})
_NORMALIZE_VALUES = frozenset({"none", "tic", "median", "pqn"})
_IMPUTE_VALUES = frozenset({"none", "half_min", "knn", "column_mean"})
_FILTER_KEYS = frozenset({"min_detection_rate"})

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_DEFAULT_TIMEOUT_S = 21600

#: statisticsが省略され、かつprofileにも既定統計が無い場合の唯一のfallback
#: （spec §6.2「既定PCAはstatistic_id=pca, matrix_recipe_id=default,
#: transform=none, scaling=autoscale, n_components=2」）。
_DEFAULT_PCA_STATISTIC = {
    "statistic_id": "pca", "kind": "pca", "matrix_recipe_id": "default",
    "transform": "none", "feature_scope": {"mode": "all_eligible"},
    "scaling": "autoscale", "n_components": 2,
}

#: 明示nullを拒否するトップレベルキー（v1の`_NULL_REJECTED_TOP_LEVEL_KEYS`と
#: 同じ規約——「無効化」を意味しない設定に明示nullを許すと未指定と区別が
#: つかなくなる）。`sample_manifest`だけがv1同様に例外（明示解除の語彙）。
#: `preprocess`はここに含めない——全体をnullにすることは「上書き無し」と
#: 同義であり、無効化ではなく単なる省略の言い換えとして扱う。
_NULL_REJECTED_TOP_LEVEL_KEYS = frozenset({
    "schema", "omics", "profile_file", "execution_purpose", "target",
    "standard_assays", "statistics", "output_root", "keep_extension",
})


def _fail(message: str, **details) -> None:
    raise DomainError("PIPELINE_REQUEST_INVALID", message, details)


def _fail_scope(message: str, **details) -> None:
    raise DomainError("PROFILE_SCOPE_MISMATCH", message, details)


def _reject_disallowed_explicit_null(source: dict, keys) -> None:
    for key in keys:
        if key in source and source[key] is None:
            _fail(f"{key}に明示nullは許可されません（無効化を意味しない設定のため、"
                  f"未指定にしてください）。", **{key: None})


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _require_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(f"{label}は正の整数である必要があります（boolは不可）: {value!r}",
              **{label: value})
    return value


def _require_probability(value: object, label: str) -> float:
    if not (_is_finite_number(value) and 0 < float(value) < 1):
        _fail(f"{label}は0より大きく1未満である必要があります: {value!r}",
              **{label: value})
    return float(value)


def _require_optional_str(data: dict, field: str) -> None:
    value = data.get(field)
    if value is not None and (not isinstance(value, str) or value == ""):
        _fail(f"{field}は空でない文字列またはnullである必要があります: {value!r}",
              **{field: value})


# ---------- statistics（spec §6.2） ----------

def _validate_feature_scope(value: object, label: str, feature_target_ids: frozenset) -> dict:
    if not isinstance(value, dict):
        _fail(f"{label}はオブジェクトである必要があります: {value!r}")
    mode = value.get("mode")
    if mode == "all_eligible":
        unknown = set(value) - {"mode"}
        if unknown:
            _fail(f"{label}に未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))
        return {"mode": "all_eligible"}
    if mode == "targets":
        unknown = set(value) - {"mode", "target_ids"}
        if unknown:
            _fail(f"{label}に未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))
        target_ids = value.get("target_ids")
        if not isinstance(target_ids, list) or not target_ids:
            _fail(f"{label}.target_idsは空でない配列である必要があります: {target_ids!r}")
        if len(set(target_ids)) != len(target_ids):
            _fail(f"{label}.target_idsに重複があります。", target_ids=target_ids)
        for target_id in target_ids:
            if target_id not in feature_target_ids:
                _fail(f"{label}.target_idsが存在しないtarget_idを参照しています: "
                      f"{target_id!r}", target_id=target_id)
        return {"mode": "targets", "target_ids": list(target_ids)}
    _fail(f"{label}.modeが不正です: {mode!r}", mode=mode)


def _validate_statistic_item(
    value: object, index: int, matrix_recipe_ids: frozenset, feature_target_ids: frozenset,
) -> dict:
    label = f"statistics[{index}]"
    if not isinstance(value, dict):
        _fail(f"{label}はオブジェクトである必要があります: {value!r}")

    missing_common = _STATISTIC_COMMON_KEYS - set(value)
    if missing_common:
        _fail(f"{label}に必須キーが不足しています: {sorted(missing_common)}",
              missing_keys=sorted(missing_common))

    kind = value["kind"]
    if kind not in _STATISTIC_KINDS:
        _fail(f"{label}.kindが不正です: {kind!r}", kind=kind)

    allowed = _STATISTIC_COMMON_KEYS | _STATISTIC_EXTRA_KEYS[kind]
    unknown = set(value) - allowed
    if unknown:
        _fail(f"{label}に未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))

    statistic_id = value["statistic_id"]
    if not isinstance(statistic_id, str) or not _SAFE_ID_RE.fullmatch(statistic_id):
        _fail(f"{label}.statistic_idは安全な文字だけのID（英数字・アンダースコア・"
              f"ハイフン）である必要があります: {statistic_id!r}", statistic_id=statistic_id)

    matrix_recipe_id = value["matrix_recipe_id"]
    if matrix_recipe_id not in matrix_recipe_ids:
        _fail(f"{label}.matrix_recipe_idが存在しないrecipeを参照しています: "
              f"{matrix_recipe_id!r}", matrix_recipe_id=matrix_recipe_id)

    transform = value["transform"]
    if transform not in _TRANSFORM_VALUES:
        _fail(f"{label}.transformが不正です: {transform!r}", transform=transform)

    feature_scope = _validate_feature_scope(
        value["feature_scope"], f"{label}.feature_scope", feature_target_ids)

    out = {
        "statistic_id": statistic_id, "kind": kind, "matrix_recipe_id": matrix_recipe_id,
        "transform": transform, "feature_scope": feature_scope,
    }

    if kind == "pca":
        scaling = value.get("scaling", "autoscale")
        if scaling not in ("none", "autoscale"):
            _fail(f"{label}.scalingが不正です: {scaling!r}", scaling=scaling)
        n_components = value.get("n_components", 2)
        out["scaling"] = scaling
        out["n_components"] = _require_positive_int(n_components, f"{label}.n_components")
    elif kind == "welch":
        reference_group = value["reference_group"]
        test_group = value["test_group"]
        if not isinstance(reference_group, str) or not reference_group:
            _fail(f"{label}.reference_groupが不正です: {reference_group!r}")
        if not isinstance(test_group, str) or not test_group:
            _fail(f"{label}.test_groupが不正です: {test_group!r}")
        if reference_group == test_group:
            _fail(f"{label}: reference_groupとtest_groupが同一です: {reference_group!r}",
                  group=reference_group)
        q_threshold = value.get("q_threshold", 0.05)
        log2fc_threshold = value.get("log2fc_threshold", 1.0)
        out["reference_group"] = reference_group
        out["test_group"] = test_group
        out["q_threshold"] = _require_probability(q_threshold, f"{label}.q_threshold")
        if not (_is_finite_number(log2fc_threshold) and float(log2fc_threshold) >= 0):
            _fail(f"{label}.log2fc_thresholdは非負である必要があります: "
                  f"{log2fc_threshold!r}", log2fc_threshold=log2fc_threshold)
        out["log2fc_threshold"] = float(log2fc_threshold)
    else:  # anova_tukey
        groups = value["groups"]
        if not isinstance(groups, list) or len(groups) < 3:
            _fail(f"{label}.groupsは3群以上である必要があります: {groups!r}")
        if len(set(groups)) != len(groups):
            _fail(f"{label}.groupsに重複があります: {groups!r}", groups=groups)
        for group in groups:
            if not isinstance(group, str) or not group:
                _fail(f"{label}.groups[]は空でない文字列である必要があります: {group!r}")
        alpha = value.get("alpha", 0.05)
        out["groups"] = list(groups)
        out["alpha"] = _require_probability(alpha, f"{label}.alpha")

    return out


def validate_statistics(items: list, profile: dict) -> list:
    """`statistics`配列を検証し、既定値を埋めた正規化済みリストを返す（spec §6.2）。

    ``profile``は``matrix_recipes``（recipe_id存在確認）と``feature_targets``
    （target_id存在確認）だけを参照する——``lcms-profile.v1``の完全な形で
    ある必要はない（呼び出し側のテストは最小限のdictでよい）。
    """
    if not isinstance(profile, dict):
        _fail("profileはオブジェクトである必要があります。", value=profile)
    matrix_recipe_ids = frozenset((profile.get("matrix_recipes") or {}).keys())
    feature_target_ids = frozenset((profile.get("feature_targets") or {}).keys())

    if not isinstance(items, list):
        _fail("statisticsは配列である必要があります。", value=items)
    if not items:
        _fail("statisticsは非空の配列である必要があります。")

    normalized: list = []
    seen_ids: set = set()
    for index, item in enumerate(items):
        normalized_item = _validate_statistic_item(
            item, index, matrix_recipe_ids, feature_target_ids)
        statistic_id = normalized_item["statistic_id"]
        if statistic_id in seen_ids:
            _fail(f"statistic_idが重複しています: {statistic_id!r}", statistic_id=statistic_id)
        seen_ids.add(statistic_id)
        normalized.append(normalized_item)
    return normalized


def _effective_target(statistics: list) -> str:
    """spec §6.2「target=autoは検定を含めばdifferential、PCAだけなら
    exploratory」。"""
    return "differential" if any(s["kind"] != "pca" for s in statistics) else "exploratory"


def _compute_effective_target(target: str, statistics: list) -> str:
    if target != "auto":
        return target
    return _effective_target(statistics)


def _validate_target_consistency(target: str, statistics: list) -> None:
    """spec §6.2「exploratoryに検定を指定、またはdifferentialに検定がなければ
    request不正とする」。"""
    has_test = any(s["kind"] != "pca" for s in statistics)
    if target == "exploratory" and has_test:
        _fail(
            "target='exploratory'に検定(welch/anova_tukey)を含むstatisticsは指定"
            "できません（検定を実行するならtarget='differential'または'auto'に"
            "してください）。",
            target=target,
            statistic_ids=[s["statistic_id"] for s in statistics if s["kind"] != "pca"],
        )
    if target == "differential" and not has_test:
        _fail(
            "target='differential'には検定(welch/anova_tukey)を含むstatisticsが"
            "少なくとも1つ必要です。",
            target=target,
        )


# ---------- standard_assays ----------

def _validate_standard_assays(value: object, feature_target_ids: frozenset) -> dict:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        _fail("standard_assaysはオブジェクトである必要があります。", value=value)
    out: dict = {}
    for target_id, sample_ids in value.items():
        if target_id not in feature_target_ids:
            _fail(f"standard_assaysが存在しないtarget_idを参照しています: "
                  f"{target_id!r}", target_id=target_id)
        if not isinstance(sample_ids, list) or not sample_ids:
            _fail(f"standard_assays[{target_id!r}]は空でない配列である必要があります: "
                  f"{sample_ids!r}", target_id=target_id)
        normalized_ids = []
        for sample_id in sample_ids:
            if not isinstance(sample_id, str) or not sample_id:
                _fail(f"standard_assays[{target_id!r}]の要素は空でない文字列である"
                      f"必要があります: {sample_id!r}", target_id=target_id)
            normalized_ids.append(sample_id)
        out[target_id] = normalized_ids
    return out


# ---------- preprocess（matrix_recipe override） ----------

def _validate_matrix_recipe_override(value: object, label: str, recipe: dict) -> dict:
    if not isinstance(value, dict):
        _fail(f"{label}はオブジェクトである必要があります: {value!r}")
    unknown = set(value) - _PREPROCESS_OVERRIDE_KEYS
    if unknown:
        _fail(f"{label}に未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))

    out: dict = {}
    if "normalize" in value:
        normalize = value["normalize"]
        if normalize not in _NORMALIZE_VALUES:
            _fail(f"{label}.normalizeが不正です: {normalize!r}", normalize=normalize)
        out["normalize"] = normalize
    if "drift_correct" in value:
        drift_correct = value["drift_correct"]
        if not isinstance(drift_correct, bool):
            _fail(f"{label}.drift_correctはboolである必要があります: {drift_correct!r}")
        out["drift_correct"] = drift_correct
    if "impute" in value:
        impute = value["impute"]
        if impute not in _IMPUTE_VALUES:
            _fail(f"{label}.imputeが不正です: {impute!r}", impute=impute)
        out["impute"] = impute
    if "filter" in value:
        filter_value = value["filter"]
        if filter_value is not None:
            if not isinstance(filter_value, dict) or set(filter_value) != _FILTER_KEYS:
                _fail(f"{label}.filterはnullまたは{{min_detection_rate}}である"
                      f"必要があります: {filter_value!r}")
            rate = filter_value["min_detection_rate"]
            if not (_is_finite_number(rate) and 0.0 <= float(rate) <= 1.0):
                _fail(f"{label}.filter.min_detection_rateは0以上1以下である"
                      f"必要があります: {rate!r}")
            filter_value = {"min_detection_rate": float(rate)}
        out["filter"] = filter_value

    # base='internal_standard_ratio'のrecipeに対するnormalize上書きは、
    # 上書き後も二重正規化禁止（spec §8）を満たす必要がある
    # （profile_schema._validate_matrix_recipeと同じ規則をoverride後の
    # 実効値に対しても適用する）。
    effective_normalize = out.get("normalize", recipe.get("normalize"))
    effective_base = recipe.get("base")
    if effective_base == "internal_standard_ratio" and effective_normalize != "none":
        _fail(
            f"{label}: base='internal_standard_ratio'のrecipeにnormalize="
            f"{effective_normalize!r}への上書きは許可されません（二重正規化の禁止）。",
            base=effective_base, normalize=effective_normalize,
        )
    return out


def _check_routine_scope(recipe_id: str, override: dict, matrix_recipes: dict) -> None:
    """routine実行での上書きを、profile自身が既に持つ値の範囲内に限定する。

    このモジュールのdocstring「routine許容範囲の解釈」を参照。証明書の
    「明示許容集合」に相当する機械可読なフィールドが現行schemaに無いため、
    「profile内のいずれかのrecipeが既にその値を使っている」ことを許容の
    根拠とする。
    """
    for field, value in override.items():
        allowed_values = [recipe[field] for recipe in matrix_recipes.values() if field in recipe]
        if value not in allowed_values:
            _fail_scope(
                f"execution_purpose='routine'ではpreprocess[{recipe_id!r}].{field}を"
                "profileが検証済みの値以外へ上書きできません"
                "（validationとして再検証してください）。",
                recipe_id=recipe_id, field=field, value=value, allowed_values=allowed_values,
            )


def _validate_preprocess(value: object, matrix_recipes: dict, execution_purpose: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail("preprocessはオブジェクトである必要があります。", value=value)

    out: dict = {}
    for recipe_id, override in value.items():
        if recipe_id not in matrix_recipes:
            _fail(f"preprocessが存在しないrecipeを参照しています: {recipe_id!r}",
                  recipe_id=recipe_id)
        normalized_override = _validate_matrix_recipe_override(
            override, f"preprocess[{recipe_id!r}]", matrix_recipes[recipe_id])
        if execution_purpose == "routine":
            _check_routine_scope(recipe_id, normalized_override, matrix_recipes)
        out[recipe_id] = normalized_override
    return out


# ---------- 公開API ----------

def resolve(data: dict, profile: dict) -> dict:
    """`pipeline-request.v2`を検証・解決する（spec §6, §6.2）。

    ``data``は「MCP明示値 > profile既定値 > v2既定値」の階層で解決する対象
    そのもの（``analysis-request.json``の層はここでは扱わない——
    ``lipidmix.pipeline.request.resolve_request``がschemaディスパッチの時点で
    ``explicit``をそのまま渡す）。``profile``は既に検証済みの
    ``lcms-profile.v1``相当のdict（このモジュールが実際に参照するのは
    ``matrix_recipes`` / ``feature_targets`` / ``analysis_recipe.statistics``
    の3つだけ）。

    未指定と明示nullの区別（``_NULL_REJECTED_TOP_LEVEL_KEYS``）はここ
    ――**新規の明示入力に対してだけ**行う。``merge_updates``が
    ``_resolve_core``を直接呼ぶのはこのため：既に解決済みの要求
    （``sample_manifest``省略なら``keep_extension``等の未指定フィールドが
    素の``None``で埋まっている）を丸ごと``resolve``へ再度通すと、
    「省略していた」という事実が失われ、すべてのNoneが「明示null」に
    見えてしまう（v1の``resolve_request`` vs ``validate_request``と同じ
    区別）。
    """
    if not isinstance(data, dict):
        _fail("requestはオブジェクトである必要があります。", value=data)
    _reject_disallowed_explicit_null(data, _NULL_REJECTED_TOP_LEVEL_KEYS)
    return _resolve_core(data, profile)


def _resolve_core(data: dict, profile: dict) -> dict:
    """``resolve``/``merge_updates``が共有する検証本体（明示null拒否を含まない）。"""
    if not isinstance(profile, dict):
        _fail("profileはオブジェクトである必要があります"
              "（v2はprofileの解決結果が必須です）。", value=profile)

    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        if {"method_file", "lbm_file"} & unknown:
            _fail(
                "pipeline-request.v2ではmethod_file/lbm_fileを直接指定できません"
                "（profile_fileから解決してください）。", unknown_keys=sorted(unknown),
            )
        if "comparisons" in unknown:
            _fail(
                "pipeline-request.v2ではcomparisonsではなくstatisticsを指定して"
                "ください。", unknown_keys=sorted(unknown),
            )
        _fail(f"未知のキーがあります: {sorted(unknown)}", unknown_keys=sorted(unknown))

    matrix_recipes = profile.get("matrix_recipes") or {}
    feature_target_ids = frozenset((profile.get("feature_targets") or {}).keys())

    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        _fail(f"schemaは{SCHEMA!r}のみ許可されます: {schema!r}", schema=schema)

    omics = data.get("omics", "metabolomics")
    if omics not in _OMICS_VALUES:
        _fail(f"omicsが不正です: {omics!r}", omics=omics)

    profile_file = data.get("profile_file")
    if not isinstance(profile_file, str) or not profile_file:
        _fail(f"profile_fileは空でない文字列である必要があります: {profile_file!r}",
              profile_file=profile_file)

    execution_purpose = data.get("execution_purpose", "routine")
    if execution_purpose not in _EXECUTION_PURPOSE_VALUES:
        _fail(f"execution_purposeが不正です: {execution_purpose!r}",
              execution_purpose=execution_purpose)

    target = data.get("target", "auto")
    if target not in _TARGET_VALUES:
        _fail(f"targetが不正です: {target!r}", target=target)

    _require_optional_str(data, "sample_manifest")
    sample_manifest = data.get("sample_manifest")

    _require_optional_str(data, "output_root")
    output_root = data.get("output_root")

    _require_optional_str(data, "keep_extension")
    keep_extension = data.get("keep_extension")

    timeout_s = data.get("timeout_s", _DEFAULT_TIMEOUT_S)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
        _fail(f"timeout_sは正の整数である必要があります（boolは不可）: {timeout_s!r}",
              timeout_s=timeout_s)

    save_project = data.get("save_project", True)
    if not isinstance(save_project, bool):
        _fail(f"save_projectはboolのみ許可されます: {save_project!r}",
              save_project=save_project)

    if "statistics" in data:
        statistics_source = "explicit"
        statistics_input = data["statistics"]
    else:
        profile_statistics = (profile.get("analysis_recipe") or {}).get("statistics") or []
        if profile_statistics:
            statistics_source = "profile_default"
            statistics_input = profile_statistics
        else:
            statistics_source = "v2_default"
            statistics_input = [copy.deepcopy(_DEFAULT_PCA_STATISTIC)]
    statistics = validate_statistics(statistics_input, profile)
    _validate_target_consistency(target, statistics)
    effective_target = _compute_effective_target(target, statistics)

    standard_assays = _validate_standard_assays(data.get("standard_assays"), feature_target_ids)
    preprocess = _validate_preprocess(data.get("preprocess"), matrix_recipes, execution_purpose)

    value_sources = {
        key: ("explicit" if key in data else "default") for key in _TOP_LEVEL_KEYS
    }
    value_sources["statistics"] = statistics_source

    return {
        "schema": SCHEMA,
        "omics": omics,
        "profile_file": profile_file,
        "execution_purpose": execution_purpose,
        "target": target,
        "sample_manifest": sample_manifest,
        "standard_assays": standard_assays,
        "preprocess": preprocess,
        "statistics": statistics,
        "timeout_s": timeout_s,
        "save_project": save_project,
        "output_root": output_root,
        "keep_extension": keep_extension,
        "effective_target": effective_target,
        "value_sources": value_sources,
    }


def merge_updates(current: dict, updates: dict, profile: dict) -> dict:
    """resumeの入力訂正を反映し、再検証した v2 request を返す（spec §6.1, §6.2）。

    ``UPDATABLE``（target・sample_manifest・preprocess・statistics・
    standard_assays）以外のキーを変えようとした場合は``NEW_PIPELINE_REQUIRED``
    にする——profile_file・execution_purpose・omics等の変更は上流条件その
    ものの変更であり、新しいpipelineが要る（spec §6.1「profile・method・
    library・raw・実行環境・極性・measureの変更はNEW_PIPELINE_REQUIRED」）。
    """
    if not isinstance(current, dict):
        _fail("requestはオブジェクトである必要があります。", value=current)
    if not isinstance(updates, dict):
        _fail("updatesはオブジェクトである必要があります。", value=updates)

    unknown = set(updates) - UPDATABLE
    if unknown:
        raise DomainError(
            "NEW_PIPELINE_REQUIRED", "上流条件の変更には新しい解析が必要です",
            {"unknown_keys": sorted(unknown)},
        )
    _reject_disallowed_explicit_null(updates, _NULL_REJECTED_TOP_LEVEL_KEYS & UPDATABLE)

    merged = {key: copy.deepcopy(value) for key, value in current.items()
              if key not in _INTERNAL_KEYS}
    for key, value in updates.items():
        merged[key] = copy.deepcopy(value)

    resolved = _resolve_core(merged, profile)

    # 出所は「保存済みの既存値」を土台に、updatesへ挙がったキーだけを
    # explicit_updateへ格上げする——resolve()が計算したexplicit/defaultの
    # 区別（merged自体は常に全キー揃っているので常に"explicit"寄りに見える）
    # をそのまま使うと、更新していないフィールドの出所情報が失われる。
    resolved["value_sources"] = copy.deepcopy(
        current.get("value_sources", resolved["value_sources"]))
    for key in updates:
        resolved["value_sources"][key] = "explicit_update"
    return resolved
