"""結果の同一性・来歴・派生結果の無効化（spec §6）。

数値層の純ロジックで、MCP にも session にも依存しない。ここが答えるのは 2 つの問い:

**この結果はどの入力から出たのか。** 図と TSV が別々の前処理から出た数字を並べても、
どちらも「直近の結果」を名乗る限り読み手には見分けがつかない。結果自身に
`provenance.result_id` と `input_fingerprint` を持たせ、あとから照合できるようにする。

**入力が変わったとき、どの結果が古くなるのか。** 前処理をやり直せば PCA も差次的解析も
無効になる。群の付け替えだけなら PCA は生きている。この依存関係を 1 か所
（`invalidate_results`）に書き、呼び出し側ごとに違う判断をさせない。

指紋は**バイト列**から作る。JSON 経由にすると NaN が null になって 0 と混ざり、
「欠測のまま」と「0 として埋めた」が同じ指紋になる——前処理の違いそのものが
消える。dtype と shape も混ぜる（転置は別の行列で、要素の並びだけでは区別できない）。
"""
from __future__ import annotations

import hashlib
import uuid

import numpy as np

from lipidmix.core.atomic_io import DomainError, canonical_hash

__all__ = [
    "PP_FIELDS",
    "array_fingerprint",
    "assert_current",
    "dataset_fingerprint",
    "invalidate_results",
    "is_current",
    "metadata_fingerprints",
    "new_provenance",
    "preprocess_fingerprint",
    "register_result",
]

#: 前処理の結果を左右するサンプルメタデータの列。ここが変われば前処理済み行列が
#: 変わるので、派生結果（PCA・差次的解析）まで無効になる。`group` は入っていない
#: ——群は比較の指定であって前処理の入力ではない。
PP_FIELDS = frozenset({"role", "batch", "injection_order", "qc_pool", "include",
                       "sample_id", "source_file"})

#: 前処理そのものをやり直す必要がある変更。
_PP_INVALIDATING = PP_FIELDS | {"dataset", "detection", "recipe"}


def array_fingerprint(array) -> str:
    """NumPy 配列の指紋。dtype・shape・並びを含むバイト列から作る。

    `array` が None なら「無い」ことの指紋を返す（空配列と区別する）。
    """
    if array is None:
        return canonical_hash({"array": None})
    arr = np.ascontiguousarray(array)
    h = hashlib.sha256()
    h.update(str(arr.dtype).encode("ascii"))
    h.update(b"|")
    h.update(str(arr.shape).encode("ascii"))
    h.update(b"|")
    h.update(arr.tobytes())
    return h.hexdigest()


def dataset_fingerprint(ds) -> str:
    """解析の入力としてのデータセットの指紋。

    含めるのは「入力」だけ——定量行列・特徴 ID・検体の並び・検出マスク・出所。
    派生結果（pp_matrix / last_pca / last_differential）は含めない。含めると
    結果を 1 つ計算するたびに入力が変わったことになり、無限に再計算が要る。
    """
    return canonical_hash({
        "matrix": array_fingerprint(getattr(ds, "feature_matrix", None)),
        "features": list(getattr(ds, "feature_ids", []) or []),
        "samples": list(getattr(ds, "sample_names", []) or []),
        "assays": list(getattr(ds, "sample_assay_ids", []) or []),
        "measure": getattr(ds, "quantification_measure", None),
        "detected_mask": array_fingerprint(getattr(ds, "detected_mask", None)),
        "source_files": dict(getattr(ds, "source_files", {}) or {}),
    })


def metadata_fingerprints(rows: list[dict]) -> dict:
    """サンプルメタデータを**列ごとに**指紋化する。

    列ごとに分けるのは、何が変わったかで無効化の範囲が変わるから。全体を 1 つの
    指紋にすると、群ラベルを直しただけで前処理からやり直す羽目になる。
    行の並び順には依存させない（`sample_id` で整列してから見る）。
    """
    ordered = sorted(rows, key=lambda r: str(r.get("sample_id", "")))
    fields: set[str] = set()
    for row in ordered:
        fields.update(k for k in row if isinstance(k, str))
    return {field: canonical_hash([row.get(field) for row in ordered])
            for field in sorted(fields)}


def preprocess_fingerprint(ds, recipe: dict, metadata_hash: str | None = None) -> str:
    """前処理の入力（データセット・レシピ・関与するメタデータ）の指紋。"""
    return canonical_hash({
        "dataset": dataset_fingerprint(ds),
        "recipe": recipe,
        "metadata": metadata_hash,
    })


def invalidate_results(ds, changed: set[str]) -> None:
    """変更内容に応じて、古くなった状態だけを捨てる。

    捨てる範囲は変更の性質で決まる:

    - 前処理の入力（`PP_FIELDS` ・`dataset` ・`detection` ・`recipe`）が変われば、
      前処理済み行列ごと捨てる。行列を残して結果だけ消すと、次の PCA が古い行列で
      走り、しかもそれが新しい設定の結果として記録される。
    - 群・比較の指定が変われば差次的解析だけ。PCA は群を入力にしていない。
    - PCA の設定だけなら PCA だけ。

    「消す」を選ぶのは、古い数字を返し続けるより無いことにするほうが安全だから。
    """
    changed = set(changed)
    if changed & _PP_INVALIDATING:
        ds.pp_matrix = None
        ds.pp_sample_names = []
        ds.pp_feature_names = []
        ds.preprocessing_recipe = {}
        ds.preprocess_id = None
        ds.preprocess_metadata_hash = None
        ds.last_pca = None
        ds.last_differential = None
    elif changed & {"group", "comparison"}:
        ds.last_differential = None
    elif "pca_settings" in changed:
        ds.last_pca = None


def new_provenance(ds, *, kind: str, input_fingerprint: str,
                   effective_parameters: dict,
                   parent_ids: list[str] | None = None,
                   warnings: list[str] | None = None,
                   request_revision: int | None = None) -> dict:
    """結果に付ける来歴を作る（result_id はここでだけ発行する）。

    `request_revision` は pipeline 要求の版で、単体ツール経由では None。
    ID を発行するのは新しい計算をしたときだけ——古い結果の result_id を別の計算へ
    付け替えると、来歴が「同じ結果」と嘘をつく。
    """
    from lipidmix.core.version import server_version

    return {
        "result_id": f"res_{uuid.uuid4().hex}",
        "kind": kind,
        "dataset_id": getattr(ds, "dataset_id", None),
        "request_revision": request_revision,
        "input_fingerprint": input_fingerprint,
        "parent_ids": list(parent_ids or []),
        "code_version": server_version(),
        "effective_parameters": effective_parameters,
        "warnings": list(warnings or []),
    }


def register_result(ds, result: dict) -> dict:
    """結果を ID で引けるようにデータセットへ登録する。"""
    ds.results[result["provenance"]["result_id"]] = result
    return result


def is_current(ds, result: dict) -> bool:
    """その結果が、今のデータセットの状態から出たものとして通用するか。"""
    prov = (result or {}).get("provenance") or {}
    if prov.get("dataset_id") != getattr(ds, "dataset_id", None):
        return False
    parents = prov.get("parent_ids") or []
    if parents and getattr(ds, "preprocess_id", None) not in parents:
        return False
    if prov.get("kind") == "preprocess":
        return prov.get("result_id") == getattr(ds, "preprocess_id", None)
    return True


def assert_current(ds, result: dict) -> None:
    """古い結果の利用を止める。理由は機械可読に返す。

    黙って使わせると、図と TSV が別々の前処理から出た数字を並べる。
    """
    if is_current(ds, result):
        return
    prov = (result or {}).get("provenance") or {}
    raise DomainError(
        "RESULT_STALE",
        "この結果は現在のデータセットの状態から出たものではありません"
        "（前処理のやり直し・別データセットの結果）。再計算してください。",
        {"result_id": prov.get("result_id"),
         "result_dataset_id": prov.get("dataset_id"),
         "dataset_id": getattr(ds, "dataset_id", None),
         "result_parent_ids": prov.get("parent_ids"),
         "preprocess_id": getattr(ds, "preprocess_id", None)})
