"""DatasetState と analysis/ の純関数群をつなぐアダプタ（MCP 非依存）。

DatasetState.feature_matrix は (n_features, n_samples)。analysis/ の関数は
すべて (n_samples, n_features) を期待するため、ここで転置する。
呼び出し側はこの転置を意識しなくてよい。

処理順は lipidmix/arf/tools.py の arf_preprocess と同一に保つ:
  blank_filter → normalize → drift_correct → qc_rsd_filter → impute
  → drop_samples_by_role(blank)
ARF 経路と DatasetState 経路で違う数字が出ないことが、この層の存在意義。

依存は lipidmix.analysis.* のみ。arf/ mztab/ tools/ を import しない
（DatasetState は引数として受け取るだけで型 import もしない）。
"""
from __future__ import annotations

import re

import numpy as np

from lipidmix.analysis import differential, export_contract, preprocessing
from lipidmix.analysis.pca import run_pca

_DATE_RE = re.compile(r"(\d{8})")

# 差次的解析の群に混ぜてはいけないロール。
_NON_SAMPLE_ROLES = ("qc", "blank")

# ここで結果 dict に刻む版とラベルは、dataset_export_differential が検証する値と
# 同一でなければならない。ローカルに複製すると、export_contract.CONTRACT_VERSION を
# 上げた瞬間ここだけ古い値のままになり、エクスポートが「契約非互換」で永久に拒否され
# 続ける（dataset_differential を再実行しても同じ古い値を刻むだけなので、クライアント
# のリプレイでは直らない）。単一情報源として export_contract を直接参照する。


class PreconditionError(Exception):
    """前提が満たされないことを、理由付きで呼び出し側へ返す。

    MCP ツール層がこの例外を捕らえ、`kind` を見て封筒を選ぶ:
      kind="missing_state"  → missing_state() エンベロープ（リプレイで回復可能）
      kind="bad_request"    → 引数エラー。リプレイしても直らない
    None を返して理由を捨てると、上位が一律 missing_state に変換して
    クライアントを無限リプレイに落とす。

    kind="missing_state" のときは `state` に欠けている状態の識別子を入れる。
    ツール層はこれをそのまま missing_state() の第1引数に使う。
    **メッセージ文面から状態を推測させない**——文面を直した瞬間に振り分けが
    静かに壊れる結合になる。
    """

    def __init__(self, kind: str, message: str, details: dict | None = None,
                 state: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details or {}
        if kind == "missing_state" and not state:
            raise ValueError("kind='missing_state' には state が必須です。")
        self.state = state


def build_dataset_pp_inputs(ds):
    """DatasetState から preprocessing.preprocess() の引数を組む。

    Returns:
        matrix        : (n_samples, n_features) — feature_matrix の転置
        sample_names  : list[str]
        feature_names : list[str]（SMF_ID）
        roles         : {sample_name: "sample"|"qc"|"blank"} — preprocess の第3引数
        sample_meta   : {sample_name: {role, batch, batch_source}} — feature-qc.tsv 用
    """
    if ds.feature_matrix is None or not ds.sample_names or not ds.feature_ids:
        raise PreconditionError(
            "missing_state",
            "DatasetState に定量行列がありません。dataset_load を先に実行してください。",
            state="dataset",
        )

    matrix = np.asarray(ds.feature_matrix, dtype=float).T.copy()
    sample_names = list(ds.sample_names)
    feature_names = list(ds.feature_ids)

    # class_ids は mzTab-M に対応物が無いので渡さない（既定 None）。
    roles = preprocessing.detect_sample_roles(sample_names)

    sample_meta: dict = {}
    for name in sample_names:
        m = _DATE_RE.search(name)
        sample_meta[name] = {
            "role": roles.get(name, "sample"),
            "batch": m.group(1) if m else None,
            "batch_source": "filename_date" if m else None,
            # mzTab-M は注入順を持たない。ARF の sample_meta と同じキーを立てて
            # おき、値が None であることを下流（drift_correct）に伝える。
            "run_order": None,
        }
    return matrix, sample_names, feature_names, roles, sample_meta


def run_dataset_preprocess(ds, recipe: dict):
    """DatasetState の feature_matrix に前処理を適用する。

    recipe は preprocessing.preprocess() と同じキー（normalize / blank_min_fold /
    drift_correct / max_qc_rsd / impute）。未知キーは preprocess が無視する。

    Returns: (pp_matrix, pp_sample_names, pp_feature_names, roles, sample_meta, report)
    """
    matrix, sample_names, feature_names, roles, sample_meta = build_dataset_pp_inputs(ds)

    # preprocess の run_order は {name: int | None} の dict。mzTab-M には注入順が
    # 無いので全件 None になり、drift_correct は status="skipped" + caveat を返す。
    run_order = {n: None for n in sample_names}

    try:
        matrix, kept_idx, report = preprocessing.preprocess(
            matrix, sample_names, roles, run_order, recipe,
        )
    except ValueError as exc:
        # normalize の未知メソッド等。引数由来なのでリプレイでは直らない。
        raise PreconditionError(
            "bad_request", f"前処理レシピが不正です: {exc}", {"recipe": recipe},
        ) from exc

    pp_feature_names = [feature_names[i] for i in kept_idx]

    # ブランクは blank_filter の参照として使い終えたので解析行列から外す。
    # 残すと総強度が桁違いに低い行が PCA の PC1 を支配する（arf_preprocess と同じ理由）。
    # QC は残す——QC クラスタの締まり具合を PCA で見るのは品質確認の定番手段。
    matrix, pp_sample_names, dropped = preprocessing.drop_samples_by_role(
        matrix, sample_names, roles, drop_roles=("blank",),
    )
    report["excluded_from_matrix"] = dropped
    if dropped.get("blank"):
        report.setdefault("caveats", []).append(
            f"ブランク {len(dropped['blank'])} 件（{', '.join(dropped['blank'])}）は背景除去に"
            "使用後、解析行列（PCA/差次的解析）から除外しました。QC は PCA での品質確認の"
            "ため残しています。"
        )

    # プールQC が複数バッチに分かれているかの簡易警告（arf_preprocess:327,364 と同じ材料）。
    qc_batches = {sample_meta[n]["batch"] for n in sample_names
                  if sample_meta[n]["role"] == "qc"}
    if len(qc_batches) > 1:
        report.setdefault("caveats", []).append(
            "プールQC が複数バッチ/層に分かれています。全体一律のドリフト補正は近似です。"
        )

    # 層別プール QC の警告（arf_preprocess と同じ材料）
    qc_strata = preprocessing.detect_qc_strata(sample_names, roles)
    if len(qc_strata) > 1:
        labels = ", ".join(sorted(s for s in qc_strata if s))
        report.setdefault("caveats", []).append(
            f"プールQC が層別（{len(qc_strata)} サブグループ"
            f"{f': {labels}' if labels else ''}）と検出されました。"
            "全 QC を1系列として扱うドリフト補正/RSD フィルタは近似です。"
        )
    report.setdefault("caveats", []).append(
        "現在の実装は mzTab-M から注入順（run order）を読み取っていないため、QC ドリフト"
        "補正は実施できません（mzTab-M の assay[N]-custom[...] は injection sequence label /"
        "batch label を運べますが、この経路はまだそれを読みません）。"
        "注入順に依存する品質評価が必要なら ARF 経路（arf_preprocess）を使ってください。"
    )

    return matrix, pp_sample_names, pp_feature_names, roles, sample_meta, report


def run_dataset_pca(ds, n_components: int = 5, log_transform: bool = False) -> dict:
    """DatasetState の pp_matrix に PCA を実行する。"""
    matrix = _require_pp_matrix(ds)
    n_samples, n_features = matrix.shape
    if min(n_samples, n_features) < 2:
        raise PreconditionError(
            "bad_request",
            f"PCA には 2 以上のサンプルと特徴量が必要です"
            f"（現在: サンプル={n_samples}, 特徴量={n_features}）。"
            "前処理のフィルタ閾値が厳しすぎる可能性があります。",
            {"n_samples": n_samples, "n_features": n_features},
        )

    pca = run_pca(matrix, n_components=n_components, log_transform=log_transform)
    components = np.asarray(pca["components"], dtype=float)
    n_pc = components.shape[1]

    scores = []
    for i, name in enumerate(ds.pp_sample_names):
        row = {"name": name, "role": ds.roles.get(name, "sample")}
        for pc in range(n_pc):
            row[f"PC{pc + 1}"] = round(float(components[i, pc]), 4)
        scores.append(row)

    return {
        "explained_variance_ratio": [round(float(v), 4)
                                     for v in pca["explained_variance_ratio"]],
        "scores": scores,
        "n_samples": n_samples,
        "n_features": n_features,
        "log_transform": log_transform,
        # loadings は特徴量数 × 主成分数で巨大になる。要約には載せず、
        # 呼び出し側がセッションに保持する分にだけ含める。
        "loadings": pca["loadings"],
    }


def run_dataset_differential(
    ds,
    group_a_samples: list[str],
    group_b_samples: list[str],
    *,
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
    log_transform: bool = True,
    group_a_label: str = "group_a",
    group_b_label: str = "group_b",
) -> dict:
    """前処理済み DatasetState で 2 群比較を実行する。

    group_a_samples / group_b_samples は pp_sample_names に含まれるサンプル名。
    未知の名前・非 sample ロール・両群への重複指定はすべて caveat で名指しする
    （黙って落とすと群サイズが縮んだことに気付けない）。
    """
    matrix = _require_pp_matrix(ds)
    available = list(ds.pp_sample_names)
    caveats: list[str] = []

    idx_a, names_a = _resolve_group(group_a_samples, available, ds.roles,
                                    group_a_label, caveats)
    idx_b, names_b = _resolve_group(group_b_samples, available, ds.roles,
                                    group_b_label, caveats)

    # 正規化未適用の警告（arf_differential:846-848 と同じ判定）。DatasetState 側は
    # run_dataset_preprocess が recipe 自体を ds へ書き戻さないため、呼び出し側が
    # 事前に ds.preprocessing_recipe へ設定しておく契約。未設定/空は "none" 扱い。
    recipe = ds.preprocessing_recipe or {}
    if recipe.get("normalize", "none") == "none":
        caveats.append(
            "正規化が未適用のため log2FC は測定量差を含み得ます"
            "（dataset_preprocess の normalize を検討）。")

    overlap = sorted(set(names_a) & set(names_b))
    if overlap:
        raise PreconditionError(
            "bad_request",
            f"両群に同じサンプルが指定されています: {', '.join(overlap)}。"
            "群定義を見直してください。",
            {"overlap": overlap},
        )
    if len(idx_a) < 2 or len(idx_b) < 2:
        raise PreconditionError(
            "bad_request",
            f"群サイズ不足（{group_a_label}={len(idx_a)}, {group_b_label}={len(idx_b)}）: "
            "各群 n>=2 が必要です。群名の誤り、または前処理での試料脱落の可能性があります。",
            {"n_a": len(idx_a), "n_b": len(idx_b),
             "available_samples": available, "caveats": caveats},
        )

    # two_group_test はラベル列で群を切る（サンプル名リストではない）。
    # 両群のどちらにも属さない行は検定対象から外すため、行を抜いてラベルを組む。
    keep_idx = idx_a + idx_b
    sub_matrix = matrix[keep_idx, :]
    group_labels = [group_a_label] * len(idx_a) + [group_b_label] * len(idx_b)

    # 交絡（群⟂バッチ）判定は arf_differential:872-879 と同じく「実際に比較した2群」
    # （プール後）に対して行う。プール前の細粒度ラベルで判定すると偽の交絡警告が出る
    # （arf_differential:869-871 のコメントと同じ理由）。
    # names_a/names_b は idx_a/idx_b と同じ順で構築されているため、
    # keep_idx（idx_a + idx_b）と対応するバッチ列を同じ並びで組む。
    names_ordered = names_a + names_b
    batch_labels = [(ds.sample_meta.get(n) or {}).get("batch") for n in names_ordered]
    conf = differential.check_confounding(group_labels, batch_labels)
    # batch_source は比較対象に限らず全サンプルから拾う（arf_differential:858 と同じ）。
    batch_source = next(
        (m.get("batch_source") for m in (ds.sample_meta or {}).values()
         if m.get("batch_source")),
        None,
    )
    src_note = ("（バッチはファイル名の日付から推定。実バッチ設計と異なる場合あり）"
                if batch_source == "filename_date" else "")
    if conf["confounded"]:
        caveats.append("交絡: " + conf["detail"] + src_note)
    elif not conf.get("assessable", True):
        caveats.append("交絡評価不可: " + conf["detail"] + src_note)

    results = differential.two_group_test(
        sub_matrix, ds.pp_feature_names, group_labels,
        group_a_label, group_b_label, log_transform=log_transform,
    )
    results = differential.add_fdr(results)
    summary = differential.summarize_two_group(results, q_threshold, log2fc_threshold)
    volcano = differential.volcano_data(results, q_threshold, log2fc_threshold)

    n_tested = summary["n_tested"]
    if n_tested == 0:
        caveats.append(
            "検定可能な特徴が0件（全特徴で p=NaN）。群が空・分散0・または正規化で試料が"
            "NaN化した可能性があります。『有意0件』を『群間差なし』と解釈しないでください。")
    elif n_tested < 0.2 * len(ds.pp_feature_names):
        caveats.append(
            f"検定できた特徴は {n_tested}/{len(ds.pp_feature_names)} 件のみ（多くが p=NaN）。"
            "群内 n 不足・分散0・欠損が多い可能性があります（前処理の見直しを検討）。")
    if min(len(idx_a), len(idx_b)) < 4:
        caveats.append(
            f"小n（{group_a_label}={len(idx_a)}, {group_b_label}={len(idx_b)}）につき"
            "検出力が限られます。")
    caveats.append(
        f"log2FC の向き: 正なら {group_b_label} が高い（上昇）、負なら {group_a_label} が高い（低下）。")

    return {
        "kind": "two_group",
        "a": group_a_label,
        "b": group_b_label,
        "samples_a": names_a,
        "samples_b": names_b,
        "n_a": len(idx_a),
        "n_b": len(idx_b),
        "q_threshold": q_threshold,
        "log2fc_threshold": log2fc_threshold,
        "log_transform": log_transform,
        "contract_version": export_contract.CONTRACT_VERSION,
        "log2fc_sign": export_contract.LOG2FC_SIGN,
        "summary": summary,
        "caveats": caveats,
        # 全量。呼び出し側はこれをセッションに保持し、戻り値には載せない。
        "results": results,
        "volcano": volcano,
    }


# ---------- 内部ヘルパ ----------

def _require_pp_matrix(ds):
    if ds.pp_matrix is None:
        raise PreconditionError(
            "missing_state",
            "前処理済み行列がありません。dataset_preprocess を先に実行してください。",
            state="dataset_preprocessed",
        )
    return np.asarray(ds.pp_matrix, dtype=float)


def _resolve_group(requested, available, roles, label, caveats):
    """指定サンプル名を pp_sample_names の位置に解決し、落ちた分を caveat に残す。

    requested は先勝ちで重複除去する。ARF 側は class-ID/因子トークンから群を
    解決するため重複の起きようがないが、この経路は呼び出し側が生のサンプル名
    リストを渡す API なので、group_a=["S1", "S1"] のような取り違えが起こり得る。
    重複したまま通すと同一行を2回数えた群内分散ゼロの検定になり、有意性が
    水増しされたうえで誰にも気付かれない。
    """
    seen_requested: set[str] = set()
    duplicates: list[str] = []
    deduped_requested: list[str] = []
    for name in requested:
        if name in seen_requested:
            duplicates.append(name)
            continue
        seen_requested.add(name)
        deduped_requested.append(name)
    if duplicates:
        caveats.append(
            f"{label} に同じサンプル名が重複して指定されたため 1 回に丸めました: "
            f"{', '.join(duplicates)}。重複したまま検定すると同一行を二重に数え、"
            "群内分散を過小評価して有意性を水増しします。")

    index_of = {name: i for i, name in enumerate(available)}
    idx: list[int] = []
    names: list[str] = []
    unknown: list[str] = []
    non_sample: list[str] = []
    for name in deduped_requested:
        if name not in index_of:
            unknown.append(name)
            continue
        role = roles.get(name, "sample")
        if role in _NON_SAMPLE_ROLES:
            non_sample.append(f"{name}({role})")
            continue
        idx.append(index_of[name])
        names.append(name)
    if unknown:
        caveats.append(
            f"{label} に指定されたサンプルのうち {len(unknown)} 件は前処理済み行列に"
            f"存在しないため除外しました: {', '.join(unknown)}。"
            "名前の誤り、または前処理で脱落した可能性があります。")
    if non_sample:
        caveats.append(
            f"{label} から QC/ブランクを除外しました: {', '.join(non_sample)}。"
            "群に混ぜると比較が壊れるため、生体試料のみで検定します。")
    return idx, names
