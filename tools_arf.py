"""ARF（PeakProperties 等・サンプル別強度）ツール群。

arf_list_tags/classes/sample_roles, arf_exclude, arf_preprocess,
arf_pca_preprocessed, arf_parser, arf_differential。deps: mcp_core / session_state /
path_resolvers / tool_helpers / msdial_* / preprocessing / differential /
arf_reader（arf_reader は関数内 import で、テストの patch.object(server.arf_reader)
が共有 module 経由で効くようにする）。

_filter_arf_spots / _pp_build_matrix はテストが差し替える対象なので、正準定義元を
module 修飾（path_resolvers.* / tool_helpers.*）で参照し patch が確実に効くようにする。
"""
import json
import re
from pathlib import Path

import differential
import exclusions
import path_resolvers
import preprocessing
import sample_factors
import session_state
import tool_helpers
from mcp_core import mcp
from msdial_classes import assign_sample_groups, expand_class_specs, filter_arf_by_class_ids
from msdial_tags import filter_arf_by_tags
from path_resolvers import resolve_arf_file_path
from session_state import _build_sample_meta
from tool_helpers import (
    _pp_has_preprocessed,
    _class_factors_by_position,
    _remember_arf_pca_plot,
    _format_pca_plot_block,
    _format_pca_loadings_md,
    _format_arf_tag_summary,
    _format_arf_class_summary,
    _format_arf_parse_summary,
    _format_arf_class_filter,
    _format_arf_tag_filter,
)

__all__ = [
    "arf_list_tags",
    "arf_list_classes",
    "arf_list_sample_roles",
    "arf_exclude",
    "arf_preprocess",
    "arf_pca_preprocessed",
    "arf_parser",
    "arf_differential",
]


@mcp.tool()
def arf_list_tags() -> str:
    """List MS-DIAL tags discovered for the currently loaded ARF dataset."""
    if (
        session_state.session.features is None
        or session_state.session.arf_tag_index is None
        or not str(session_state.session.current_file_path or "").lower().endswith(".arf")
    ):
        return "先に arf_parser を実行してARFデータとタグファイルを読み込んでください。"
    return json.dumps(session_state.session.arf_tag_index.get("summary", {}), ensure_ascii=False, indent=2)


@mcp.tool()
def arf_list_classes() -> str:
    """List MS-DIAL Class ID values available for the current ARF dataset."""
    if (
        session_state.session.features is None
        or session_state.session.arf_class_index is None
        or not str(session_state.session.current_file_path or "").lower().endswith(".arf")
    ):
        return "先に arf_parser を実行してARFデータとClass IDメタデータを読み込んでください。"
    class_counts = session_state.session.arf_class_index.get("class_counts", {})
    return json.dumps({
        "mddata_path": session_state.session.arf_class_index["mddata_path"],
        "class_counts": class_counts,
        "factors_by_position": _class_factors_by_position(class_counts.keys()),
    }, ensure_ascii=False, indent=2)




@mcp.tool()
def arf_list_sample_roles() -> str:
    """ロード済み ARF のサンプルを sample/qc/blank に分類して返す（前処理の適用前確認）。"""
    if session_state.session.filtered_features is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_parser で ARF を読み込んでください。"},
                          ensure_ascii=False, indent=2)
    _, sample_names, _ = tool_helpers._pp_build_matrix(session_state.session.filtered_features, ["height"])
    meta = _build_sample_meta(sample_names, session_state.session.arf_class_index)
    counts = {"sample": 0, "qc": 0, "blank": 0}
    for m in meta.values():
        counts[m["role"]] = counts.get(m["role"], 0) + 1
    for name, m in meta.items():
        m["excluded"] = name in session_state.session.excluded_samples
    return json.dumps({"status": "success", "counts": counts, "samples": meta},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def arf_exclude(
    exclude_samples: list[str] | None = None,
    exclude_spots: list[int] | None = None,
    mode: str = "add",
) -> str:
    """PCA 外れサンプルや特定ピークを名前/ID で手動除外・再包含する（可逆・非破壊）。

    先に arf_parser で ARF を読み込んでおくこと。除外は session に保持され、以降の
    arf_re_pca / arf_preprocess（→ arf_pca_preprocessed / arf_differential）へ反映される。
    filtered_features 自体は変更しないため、mode="remove"/"clear" で元に戻せる。

    引数:
    - exclude_samples: 除外するサンプル名（file_name、完全一致）のリスト。
    - exclude_spots: 除外するスポットの MasterAlignmentID（int）のリスト。
    - mode: add（既定・追加）/ remove（再包含）/ clear（全消去）/ list（現状表示のみ）。
    """
    spots = session_state.session.filtered_features
    if spots is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_parser で ARF を読み込んでください。"},
                          ensure_ascii=False, indent=2)

    avail_samples, avail_ids = exclusions.roster(spots)
    es = session_state.session.excluded_samples
    esp = session_state.session.excluded_spots
    req_samples = list(exclude_samples or [])
    req_spots = list(exclude_spots or [])
    unmatched_samples: list[str] = []
    unmatched_spots: list[int] = []
    caveats: list[str] = []

    if mode == "clear":
        es.clear()
        esp.clear()
    elif mode == "list":
        pass
    elif mode in ("add", "remove"):
        matched_samples = [n for n in req_samples if n in avail_samples]
        unmatched_samples = [n for n in req_samples if n not in avail_samples]
        matched_spots = [i for i in req_spots if i in avail_ids]
        unmatched_spots = [i for i in req_spots if i not in avail_ids]
        if mode == "add":
            es.update(matched_samples)
            esp.update(matched_spots)
        else:  # remove
            es.difference_update(req_samples)
            esp.difference_update(req_spots)
        if unmatched_samples:
            preview = ", ".join(sorted(avail_samples)[:10])
            caveats.append(
                f"未一致サンプル {unmatched_samples} は現データに存在しません（無視）。"
                f"利用可能サンプル例: {preview}")
        if unmatched_spots:
            caveats.append(
                f"未一致スポット {unmatched_spots} は現データに存在しません（無視）。")
    else:
        return json.dumps({"status": "error",
                           "message": f"unknown mode: {mode!r}（add/remove/clear/list）"},
                          ensure_ascii=False, indent=2)

    pruned = exclusions.prune_spots(spots, es, esp)
    pruned_names, pruned_ids = exclusions.roster(pruned)
    payload = {
        "status": "success",
        "mode": mode,
        "excluded_samples": sorted(es),
        "excluded_spots": sorted(esp),
        "samples_before": len(avail_samples),
        "samples_after": len(pruned_names),
        "spots_before": len(avail_ids),
        "spots_after": len(pruned_ids),
        "unmatched_samples": unmatched_samples,
        "unmatched_spots": unmatched_spots,
        "caveats": caveats,
    }
    if pruned_names == set() or pruned_ids == set():
        payload["caveats"].append(
            "除外の結果、残サンプルまたは残スポットが 0 件です。PCA/差次的解析は実行できません。")
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def arf_preprocess(
    normalize: str = "none",
    blank_min_fold: float | None = None,
    drift_correct: bool = False,
    max_qc_rsd: float | None = None,
    impute: str = "half_min",
    props: list[str] | None = None,
) -> str:
    """ロード済み ARF 行列に前処理レシピを適用し、session を更新して報告を返す。

    以降の PCA/差次的解析は session_state.session.feature_matrix（前処理後）を消費する。
    """
    if session_state.session.filtered_features is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_parser で ARF を読み込んでください。"},
                          ensure_ascii=False, indent=2)
    props = props or ["height"]
    # 手動除外（PCA 外れサンプル / 特定ピーク）を行列構築前に適用（非破壊）
    active = exclusions.prune_spots(
        session_state.session.filtered_features,
        session_state.session.excluded_samples,
        session_state.session.excluded_spots,
    )
    matrix, sample_names, feature_names = tool_helpers._pp_build_matrix(active, props)
    meta = _build_sample_meta(sample_names, session_state.session.arf_class_index)
    roles = {n: meta[n]["role"] for n in sample_names}
    run_order = {n: meta[n]["run_order"] for n in sample_names}

    # プールQC が層別（複数 QC サブグループ）かの簡易警告材料
    qc_batches = {meta[n]["batch"] for n in sample_names if meta[n]["role"] == "qc"}
    # バッチ(日付)だけでなく QC 試料名の層別（部位別 QC 等）も検出する。
    qc_strata = preprocessing.detect_qc_strata(sample_names, roles)

    recipe = {
        "normalize": normalize,
        "blank_min_fold": blank_min_fold,
        "drift_correct": drift_correct,
        "max_qc_rsd": max_qc_rsd,
        "impute": impute,
        "props": props,
    }
    matrix2, kept_idx, report = preprocessing.preprocess(
        matrix, sample_names, roles, run_order, recipe,
    )
    kept_feature_names = [feature_names[i] for i in kept_idx]

    # ブランクは背景除去（blank_filter）の参照として使い終えたので、ここで解析行列から
    # 外す。残すと生体試料と桁違いに低い総強度が PCA の PC1 を支配し、群分離の解釈が
    # 壊れる。QC は残す——QC クラスタの締まり具合を PCA で見るのは分析の定番手段。
    # 群に混ざると困る差次的解析側は arf_differential が別途 QC を群から外す。
    matrix2, pp_sample_names, dropped = preprocessing.drop_samples_by_role(
        matrix2, sample_names, roles, drop_roles=("blank",),
    )
    report["excluded_from_matrix"] = dropped
    if dropped.get("blank"):
        report.setdefault("caveats", []).append(
            f"ブランク {len(dropped['blank'])} 件（{', '.join(dropped['blank'])}）は背景除去に"
            "使用後、解析行列（PCA/差次的解析）から除外しました。QC は PCA での品質確認の"
            "ため残しています。"
        )

    session_state.session.feature_matrix = matrix2
    session_state.session.pp_sample_names = pp_sample_names
    session_state.session.pp_feature_names = kept_feature_names
    session_state.session.sample_meta = meta
    session_state.session.preprocessing_recipe = recipe
    if len(qc_batches) > 1:
        report.setdefault("caveats", []).append(
            "プールQC が複数バッチ/層に分かれています。全体一律のドリフト補正は近似です。"
        )
    if len(qc_strata) > 1:
        labels = ", ".join(sorted(s for s in qc_strata if s))
        report.setdefault("caveats", []).append(
            f"プールQC が層別（{len(qc_strata)} サブグループ{f': {labels}' if labels else ''}）"
            "と検出されました。全 QC を1系列として扱うドリフト補正/RSD フィルタは近似です。"
        )
    n_excl_s = len(session_state.session.excluded_samples)
    n_excl_p = len(session_state.session.excluded_spots)
    if n_excl_s or n_excl_p:
        report.setdefault("caveats", []).append(
            f"ユーザ手動除外: サンプル {n_excl_s} 件 / スポット {n_excl_p} 件を除外済み。")
    # 除外が過度で行列が空（残サンプル0 または 残特徴量0）になった場合を前景化する。
    if matrix2.size == 0:
        report.setdefault("caveats", []).append(
            "前処理後の行列が空です（残サンプルまたは残特徴量が 0 件）。手動除外が過度な"
            "可能性があります。PCA/差次的解析は実行できません（arf_exclude の mode=remove/clear で復帰）。")
    report["status"] = "success"
    report["matrix_shape"] = list(matrix2.shape)
    report["recipe"] = recipe
    return json.dumps(report, ensure_ascii=False, indent=2)




@mcp.tool()
def arf_pca_preprocessed(
    components: int | None = None,
    top_features: int = 10,
    log_transform: bool = False,
    group_levels: list[str] | None = None,
) -> str:
    """arf_preprocess で用意した前処理後行列で PCA を実行する（前処理後経路の PCA 入口）。

    正規化・QC フィルタ・欠損補完を経た行列に対する PCA。生スポットへ直接フィルタして
    やり直す PCA は arf_parser。手順は arf_preprocess → 本ツール。色分け(group_levels)や
    log 変換だけを変えて再実行しても、前処理はやり直さない。
    """
    if not _pp_has_preprocessed():
        return "前処理後の行列がありません。先に arf_preprocess を実行してください。"
    from arf_reader import run_pca, get_pca_loading_features
    matrix = session_state.session.feature_matrix
    sample_names = session_state.session.pp_sample_names
    feature_names = session_state.session.pp_feature_names
    try:
        pca_result = run_pca(matrix, n_components=components, log_transform=log_transform)
    except Exception as exc:
        return f"[ERROR] PCA 実行に失敗しました: {exc}"

    sample_groups = assign_sample_groups(sample_names, session_state.session.arf_class_index, group_levels)
    plot_block = _format_pca_plot_block(
        pca_result, sample_names,
        title="PCA (preprocessed ARF)",
        intro="\n#### 📊 PCA スコアプロット用データ（前処理後）\n",
        groups=sample_groups,
    )
    _remember_arf_pca_plot(pca_result, sample_names,
                           title="PCA (preprocessed ARF)", groups=sample_groups)
    loading_features = get_pca_loading_features(
        pca_result, session_state.session.features or [], feature_names, top_n=top_features,
    )
    loadings_block = _format_pca_loadings_md(
        loading_features, header="#### 📊 PCA Loadings 寄与度分析（前処理後）\n",
    )
    text = (
        f"### 📈 前処理後 ARF PCA 解析\n"
        f"- **前処理レシピ**: {session_state.session.preprocessing_recipe}\n"
        f"- **PCA入力行列の形状**: {tuple(matrix.shape)} (サンプル数 x 特徴量数)\n"
        f"- **PC1 説明分散比**: {pca_result['explained_variance_ratio'][0]*100:.2f}%\n"
        f"- **PC2 説明分散比**: {pca_result['explained_variance_ratio'][1]*100:.2f}%\n"
        f"{loadings_block}{plot_block}"
    )
    return text


@mcp.tool()
def arf_parser(
    file_path: str | None = None,
    props: list[str] | None = None,
    components: int | None = None,
    top_features: int = 10,
    log_transform: bool = False,
    min_detection_rate: float = 0.0,
    min_intensity: float = 0.0,
    annotation_keyword: str | None = None,
    tag_labels: list[str] | None = None,
    tag_mode: str = "any",
    tag_scope: str = "sample_peak",
    tag_directory: str | None = None,
    missing_sample_policy: str = "error",
    class_ids: list[str] | None = None,
    class_missing_sample_policy: str = "error",
    group_levels: list[str] | None = None,
) -> str:
    """.arf（サンプル別強度）を読み込み、フィルタを適用して PCA を実行する。

    生スポット行列に対する PCA の唯一の入口。**フィルタ条件を変えて PCA をやり直したい
    ときは、引数を変えて本ツールを再呼び出しする**（ファイルはセッションキャッシュされ
    再パースは走らない）。手動除外（arf_exclude）も PCA 前に反映される。
    正規化・QC・欠損補完を経た「前処理後」行列での PCA は arf_preprocess → arf_pca_preprocessed。

    正負の Loading 上位とスコアプロット用 JSON を含む Markdown 要約を返す。

    引数:
    - file_path: 解析する .arf ファイルのパス (省略時は自動検索)
    - props: PCAに使用するプロパティのリスト (デフォルト: ["height"])
    - components: 計算する主成分の数
    - top_features: 各主成分から抽出する正・負の寄与トップ件数 (デフォルト: 10)
    - log_transform: [任意] PCA前に log10 変換を適用する（強度の歪みを抑え条件分離が向上しやすい。既定 False）
    - min_detection_rate: [任意] 特徴量の実検出率(非ギャップフィル)による足切り 0.0-1.0（既定 0.0=無効）
    - min_intensity: [任意] スポット平均強度(HeightAverage)の最小閾値（既定 0.0=無効）
    - annotation_keyword: [任意] 脂質クラス/化合物名の部分一致キーワード（例: "PC", "LPC", "TG"）
    - tag_labels: [任意] MS-DIALタグ名またはタグIDのリスト
    - tag_mode: any/all/none/not_all のいずれか
    - tag_scope: sample_peak（サンプル別Peak ID）または alignment_spot（MasterAlignmentID）
    - tag_directory: [任意] *_tags.xml の探索先。既定はARFと同じディレクトリ
    - missing_sample_policy: タグファイル未対応サンプルの扱い。error/exclude/untagged（既定 error）
    - class_ids: [任意] Class ID の指定リスト。各要素は `_` 区切りの部分指定が可能で、
      指定した全トークンを含む Class ID に一致する（要素内AND・順不同）。要素間はOR。
      例: `["gf"]`=gfを含む全クラス、`["Cerebellum_gf"]`=両方を含むクラス、完全一致も可。
    - class_missing_sample_policy: Class IDメタデータ未対応サンプルの扱い。error/exclude（既定 error）
    - group_levels: [任意] PCA点の色分け因子の値トークン（例: `["gf","spf"]`）。
      未指定なら各サンプルの完全Class IDで色分け。指定するとその因子だけで統合し、
      該当しないサンプルは "other" 群になる。
    """
    props = props or ["height"]

    file_path = resolve_arf_file_path(file_path)
    if not file_path:
        return "データディレクトリに .arf ファイルが見つかりませんでした。"

    # 外部モジュールからのインポート
    from arf_reader import extract_peak_properties, build_pca_matrix, run_pca, get_pca_loading_features

    try:
        deserialized_and_formatted_data = session_state.session.load_data(file_path, tag_directory=tag_directory)
        if not isinstance(deserialized_and_formatted_data, list):
            return "デシリアライズ結果がリストではありません。"

        analysis_data, tag_filter_stats = filter_arf_by_tags(
            deserialized_and_formatted_data,
            session_state.session.arf_tag_index or {},
            tag_labels,
            mode=tag_mode,
            scope=tag_scope,
            missing_sample_policy=missing_sample_policy,
        )
        if not analysis_data:
            return "指定されたMS-DIALタグ条件に一致するARFピークが見つかりませんでした。arf_list_tags で利用可能タグと件数を確認してください。"

        analysis_data, class_filter_stats = filter_arf_by_class_ids(
            analysis_data,
            session_state.session.arf_class_index,
            class_ids,
            missing_sample_policy=class_missing_sample_policy,
        )
        if not analysis_data:
            return "指定されたClass IDに一致するARFサンプルが見つかりませんでした。arf_list_classes で利用可能なClass IDと件数を確認してください。"

        # 強度閾値/アノテーションキーワードによる軽量フィルタ（既定は恒等）
        analysis_data = path_resolvers._filter_arf_spots(analysis_data, min_intensity, annotation_keyword)
        if not analysis_data:
            return (
                f"指定された条件（強度 >= {min_intensity}, キーワード: '{annotation_keyword or '指定なし'}'）"
                "に一致するARFピークが見つかりませんでした。"
            )
        session_state.session.filtered_features = analysis_data

        # 手動除外（PCA 外れサンプル / 特定ピーク）を PCA 前に適用（非破壊）
        active_data = exclusions.prune_spots(
            analysis_data,
            session_state.session.excluded_samples,
            session_state.session.excluded_spots,
        )

        peak_df = extract_peak_properties(active_data)
        avg_samples = 0
        if len(active_data) > 0 and len(peak_df) > 0:
            avg_samples = len(peak_df) / len(active_data)

        # PCA行列構築（min_detection_rate は任意の検出率フィルタ）
        matrix, sample_names, feature_names = build_pca_matrix(
            active_data, use_properties=props,
            min_detection_rate=min_detection_rate,
        )

        if matrix.size == 0:
            return "[ERROR] PCA 用データを構築できませんでした（フィルタ・除外が過度な可能性があります）。"

        # PCA実行（log_transform は任意のlog10変換）
        pca_result = run_pca(matrix, n_components=components, log_transform=log_transform)

        # サンプル別の群ラベル（既定=完全Class ID、group_levels 指定時はその因子で統合）
        sample_groups = assign_sample_groups(
            sample_names, session_state.session.arf_class_index, group_levels,
        )

        # PCAスコアプロット用データ（共通ヘルパー）
        plot_instruction_text = _format_pca_plot_block(
            pca_result, sample_names,
            title=f"PCA Score Plot ({Path(file_path).name})",
            intro=(
                "\n#### 📊 PCA スコアプロット用データ\n"
                "以下のJSONデータを用いて、見やすい散布図（Scatter Plot）を描画してください。\n"
                "各点には `sample` の名前をラベルとして表示するか、ホバー時に確認できるようにしてください。\n"
                "`group` フィールドがある場合は、群ごとに色分け（凡例付き）して群間比較が分かるようにしてください。\n"
            ),
            groups=sample_groups,
        )
        _remember_arf_pca_plot(
            pca_result, sample_names,
            title=f"PCA Score Plot ({Path(file_path).name})",
            groups=sample_groups,
        )

        # Loadings 寄与上位（arf_reader の構造化関数 + 共通整形ヘルパー）
        loading_features = get_pca_loading_features(
            pca_result, session_state.session.features, feature_names, top_n=top_features,
        )
        loadings_summary_text = _format_pca_loadings_md(
            loading_features, header="#### 📊 PCA Loadings 寄与度分析 (各極値トップ件数)\n",
        )

        n_excl_s = len(session_state.session.excluded_samples)
        n_excl_p = len(session_state.session.excluded_spots)
        exclude_note = (
            f"- **ユーザ手動除外**: サンプル {n_excl_s} 件 / スポット {n_excl_p} 件\n"
            if (n_excl_s or n_excl_p) else ""
        )
        filter_note = (
            f"- **適用フィルタ条件**: 強度最小値=`{min_intensity}`, アノテーションキーワード=`'{annotation_keyword or '指定なし'}'`\n"
            if (min_intensity or annotation_keyword) else ""
        )

        # 基本的な要約テキストの作成
        output_text = (
            f"### 📈 ARF 多変量PCA解析完了: {Path(file_path).name}\n"
            f"- **読み込んだ総スポット数**: {len(deserialized_and_formatted_data)}\n"
            f"{filter_note}"
            f"{exclude_note}"
            f"{_format_arf_parse_summary(deserialized_and_formatted_data)}"
            f"{_format_arf_class_summary(session_state.session.arf_class_index)}"
            f"{_format_arf_class_filter(class_filter_stats)}"
            f"{_format_arf_tag_summary(session_state.session.arf_tag_index)}"
            f"{_format_arf_tag_filter(tag_filter_stats)}"
            f"- **抽出された総ピークレコード数**: {len(peak_df)}\n"
            f"- **平均サンプル数/スポット**: {avg_samples:.2f}\n"
            f"- **PCA入力行列の形状**: {matrix.shape} (サンプル数 x 特徴量数)\n"
            f"- **PC1 説明分散比**: {pca_result['explained_variance_ratio'][0]*100:.2f}%\n"
            f"- **PC2 説明分散比**: {pca_result['explained_variance_ratio'][1]*100:.2f}%\n"
            f"{loadings_summary_text}"
            f"{plot_instruction_text}"  # ← 座標ブロックは末尾（loadings の後）へ
        )

        return session_state.session.maybe_prepend_caveat(output_text, topic="arf")

    except Exception as e:
        return f"[ERROR] ARF解析に失敗しました: {str(e)}"


_SPOT_ID_RE = re.compile(r"^Spot_(\d+)")


def _pool_group_labels(sample_names, group_labels, group_a, group_b):
    """完全 Class ID / 群ラベルだけでなく、サンプル名の因子トークンでもプール群を作る。

    Class ID は MS-DIAL 上で入力された1文字列にすぎず、時点や複製のような因子は
    サンプル名にしか無いことがある。トークン空間を tokens(サンプル名) ∪ tokens(群ラベル)
    に統合し、`group_a="ILG_6h"` のような多因子指定を通す。完全な群ラベルを渡した
    場合は（そのトークン集合を含む他のサンプルが無い限り）従来どおりの2群比較になる。

    group_labels が None のサンプル（QC/blank・群未解決）は候補から外す。
    戻り値 (relabeled, resolved, resolved_samples)。一致ゼロ・両群の重複は ValueError。
    """
    if str(group_a) == str(group_b):
        raise ValueError(f"group_a と group_b が同一です: {group_a!r}")
    available = sorted({str(g) for g in group_labels if g is not None})
    if not available:
        raise ValueError(
            "Class ID メタデータが解決できていないため群を特定できません（.mddata 未検出）。")

    meta = {name: {"group": label, "role": "sample"}
            for name, label in zip(sample_names, group_labels) if label is not None}
    facets = sample_factors.build_sample_facets(list(meta), None, sample_meta=meta)
    try:
        # role は呼び出し側が group_labels=None で既に落としているので、ここでは絞らない。
        matches, _ = sample_factors.expand_sample_specs(
            [group_a, group_b], facets, include_roles=None)
    except ValueError as exc:
        raise ValueError(f"{exc} 利用可能な群ラベル: {', '.join(available)}") from exc

    a_names, b_names = set(matches[group_a]), set(matches[group_b])
    overlap = sorted({str(meta[n]["group"]) for n in (a_names & b_names)})
    if overlap:
        raise ValueError(
            f"group_a='{group_a}' と group_b='{group_b}' が同じサンプルを含みます "
            f"({', '.join(overlap)})。群は排他である必要があります。")

    relabeled = []
    for name, label in zip(sample_names, group_labels):
        if label is None:
            relabeled.append(None)
        elif name in a_names:
            relabeled.append(group_a)
        elif name in b_names:
            relabeled.append(group_b)
        else:
            relabeled.append(None)
    resolved = {
        "group_a": sorted({str(meta[n]["group"]) for n in a_names}),
        "group_b": sorted({str(meta[n]["group"]) for n in b_names}),
    }
    resolved_samples = {"group_a": sorted(a_names), "group_b": sorted(b_names)}
    return relabeled, resolved, resolved_samples


def _spot_id_of(feature_name) -> int | None:
    match = _SPOT_ID_RE.match(str(feature_name))
    return int(match.group(1)) if match else None


def _sibling_arf2_path() -> Path | None:
    """読み込み中の ARF と同一アラインメント実行の .arf2 を返す。

    MasterAlignmentID はアラインメント実行ごとに振り直されるため、別バッチの
    .arf2 を引くと ID 対応が黙って崩れる。`AlignmentResult_<timestamp>` の語幹が
    一致する兄弟ファイルだけを許し、無ければ None（注釈は諦める）。
    """
    current = getattr(session_state.session, "current_file_path", None)
    if not current:
        return None
    path = Path(current)
    match = re.match(r"(AlignmentResult_\d{4}(?:_\d{2}){5})", path.name)
    if not match:
        return None
    sibling = path.with_name(f"{match.group(1)}.arf2")
    return sibling if sibling.is_file() else None


def _annotate_with_names(rows: list[dict]) -> dict:
    """差次的解析の上位ヒットに脂質名/Ontology を付す。ARF が Unknown なら ARF2 を引く。

    同一アラインメントの ARF と ARF2 は同じ MasterAlignmentID を指すが、代表 Name は
    食い違うことがある（ARF 側 Unknown・ARF2 側は注釈あり）。上位ヒットが
    `Spot_474_height` のままだと解釈に到達できないため橋渡しし、出所を name_source で
    開示する。ARF2 は大きいので、ARF で埋まらない ID が残るときだけ読む。
    """
    spots = {s.get("MasterAlignmentID"): s for s in (session_state.session.features or [])}
    unresolved: list[dict] = []
    for row in rows:
        sid = _spot_id_of(row.get("feature"))
        row["spot_id"] = sid
        name = (spots.get(sid) or {}).get("Name") or ""
        if name and name.strip().lower() != "unknown":
            row["name"] = name
            row["name_source"] = "arf"
        else:
            row["name"] = None
            row["name_source"] = None
            if sid is not None:
                unresolved.append(row)
    report = {"annotated_from_arf": len(rows) - len(unresolved)}
    if not unresolved:
        return report

    arf2_path = _sibling_arf2_path()
    if not arf2_path:
        report["arf2_lookup"] = "skipped: 同一アラインメントの .arf2 が隣接していません"
        return report
    import io as _io
    from arf2_reader import deserialize as _arf2_deserialize
    with open(arf2_path, "rb") as fh:
        catalog = {s.get("MasterAlignmentID"): s for s in _arf2_deserialize(_io.BytesIO(fh.read()))}
    filled = 0
    for row in unresolved:
        spot = catalog.get(row["spot_id"])
        if not spot:
            continue
        name = spot.get("Name") or ""
        if name and name.strip().lower() != "unknown":
            row["name"] = name
            row["ontology"] = spot.get("Ontology") or None
            row["name_source"] = "arf2"
            filled += 1
    report["annotated_from_arf2"] = filled
    report["arf2_path"] = str(arf2_path)
    if filled:
        report["note"] = (
            f"{filled} 件は ARF 側 Name が Unknown で、ARF2 カタログの注釈を採用しました"
            "（name_source=arf2）。ARF と ARF2 で代表 Name は食い違い得ます。")
    return report


@mcp.tool()
def arf_differential(
    group_a: str | None = None,
    group_b: str | None = None,
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
    log_transform: bool = True,
) -> str:
    """前処理後行列で2群差次的解析（Welch t 検定 + log2FC）を行う。

    group_a / group_b の両方を指定する。多群 ANOVA は「因子（加齢/菌叢等）→水準」の
    対応が MS-DIAL メタに無く安全に導けないため現状は非対応（誤って全 Class ID を
    水準にした結果を返さないよう封鎖している）。3群以上を比べたいときは、下記の
    因子トークン・プール指定で関心のある2群を切り出して呼ぶこと。

    先に arf_preprocess を実行して session_state.session.feature_matrix を用意すること
    （未実行ならエラーを返す。生行列への暗黙フォールバックはしない）。

    - group_a / group_b: 完全な Class ID（`24M_GF_F`）に加え、**因子トークンによる
      プール群指定**を受け付ける。`group_a="24M", group_b="9w"` のように書くと、その
      トークンを含む全 Class ID がプールされる（`24M_GF_F` + `24M_SPF_M` + …）。
      複数トークンの AND 指定も可（`"24M_GF"`）。両群が同じ Class ID を掴むとエラー。
    - log_transform: [既定 True] log2(x+1) 空間で検定する。MS 強度は対数正規に近く、
      生強度での t 検定/ANOVA は正規性仮定を外れやすいため既定で有効。2群では log2FC も
      log2 空間の群平均差（＝幾何平均比）になる。生スケールで検定したい場合のみ False。
    """
    matrix = getattr(session_state.session, "feature_matrix", None)
    if matrix is None:
        return json.dumps({"status": "error",
                           "message": "先に arf_preprocess を実行してください（前処理後行列が必要）。"},
                          ensure_ascii=False, indent=2)
    sample_names = session_state.session.pp_sample_names
    feature_names = session_state.session.pp_feature_names
    meta = session_state.session.sample_meta or {}
    batch_labels = [(meta.get(n) or {}).get("batch") for n in sample_names]

    # 生体試料以外（QC・ブランク）は比較対象から外す。Class ID が group_a/group_b の
    # 因子トークンを含むと（例 QC_24M と group_a="24M"）プールに紛れ込み、群平均を
    # 汚染するため、ラベルを None にして _pool_group_labels のどちらにも寄らせない。
    # ブランクは arf_preprocess で行ごと落ちている想定だが、ここでも二重に守る。
    _NON_SAMPLE_ROLES = {"qc", "blank"}
    excluded_roles: dict[str, list[str]] = {}
    group_labels: list = []
    for name in sample_names:
        entry = meta.get(name) or {}
        role = entry.get("role", "sample")
        if role in _NON_SAMPLE_ROLES:
            excluded_roles.setdefault(role, []).append(name)
            group_labels.append(None)
        else:
            group_labels.append(entry.get("group"))

    caveats: list[str] = []
    recipe = session_state.session.preprocessing_recipe or {}
    if recipe.get("normalize", "none") == "none":
        caveats.append("正規化が未適用のため log2FC は測定量差を含み得ます（arf_preprocess の normalize を検討）。")
    if log_transform:
        caveats.append("log2(x+1) 変換後に検定を実施（強度の歪みを補正）。log2FC は群平均の log2 差＝幾何平均比です。")
    if excluded_roles:
        detail = "; ".join(
            f"{role}={len(names)}件（{', '.join(names)}）"
            for role, names in sorted(excluded_roles.items())
        )
        caveats.append(f"比較対象から除外（生体試料でないため）: {detail}。")

    batch_source = next((m.get("batch_source") for m in meta.values() if m.get("batch_source")), None)
    src_note = "（バッチはファイル名の日付から推定。実バッチ設計と異なる場合あり）" \
        if batch_source == "filename_date" else ""

    if group_a is not None and group_b is not None:
        try:
            group_labels, resolved, resolved_samples = _pool_group_labels(
                sample_names, group_labels, group_a, group_b)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)},
                              ensure_ascii=False, indent=2)

        # 交絡は「実際に比較した2群」に対して見る。プール前の Class ID 単位で判定すると、
        # 細粒度ラベルほど各群が単一バッチになりやすく偽の交絡警告を出す（プールすれば
        # 両群ともバッチ混在、という設計を交絡と誤報していた）。
        paired = [(g, b) for g, b in zip(group_labels, batch_labels) if g is not None]
        conf = differential.check_confounding(
            [g for g, _ in paired], [b for _, b in paired],
        )
        if conf["confounded"]:
            caveats.append("交絡: " + conf["detail"] + src_note)
        elif not conf.get("assessable", True):
            caveats.append("交絡評価不可: " + conf["detail"] + src_note)
        if any(len(ids) > 1 for ids in resolved.values()):
            caveats.append(
                "プール群として解決: "
                + "; ".join(f"{spec} = {' + '.join(ids)}"
                            for spec, ids in ((group_a, resolved["group_a"]),
                                              (group_b, resolved["group_b"])))
                + "。因子内の他要因（性・菌叢等）はプール内で平均化されます。")
        caveats.append(
            "比較サンプル: "
            + "; ".join(
                f"{spec} = {', '.join(names)}"
                for spec, names in ((group_a, resolved_samples["group_a"]),
                                    (group_b, resolved_samples["group_b"]))
            )
            + "。指定トークンは Class ID とサンプル名の両方から解決されます。"
        )
        results = differential.two_group_test(matrix, feature_names, group_labels,
                                              group_a, group_b, log_transform=log_transform)
        results = differential.add_fdr(results)
        summary = differential.summarize_two_group(results, q_threshold, log2fc_threshold)
        annotation = _annotate_with_names(summary.get("top") or [])
        if annotation.get("note"):
            caveats.append(annotation["note"])
        volcano = differential.volcano_data(results, q_threshold, log2fc_threshold)
        n_a = group_labels.count(group_a)
        n_b = group_labels.count(group_b)
        if n_a < 2 or n_b < 2:
            caveats.append(
                f"群サイズ不足（{group_a}={n_a}, {group_b}={n_b}）: 各群 n>=2 が必要です。"
                "群名の誤り、または前処理での試料脱落の可能性があります。")
        elif min(n_a, n_b) < 4:
            caveats.append(f"小n（{group_a}={n_a}, {group_b}={n_b}）につき検出力が限られます。")
        n_tested = summary["n_tested"]
        if n_tested == 0:
            caveats.append(
                "検定可能な特徴が0件（全特徴で p=NaN）。群が空・分散0・または正規化で試料が"
                "NaN化した可能性があります。『有意0件』を『群間差なし』と解釈しないでください。")
        elif n_tested < 0.2 * len(feature_names):
            caveats.append(
                f"検定できた特徴は {n_tested}/{len(feature_names)} 件のみ（多くが p=NaN）。"
                "群内 n 不足・分散0・欠損が多い可能性があります（前処理の見直しを検討）。")
        session_state.session.last_differential = {"kind": "two_group", "a": group_a, "b": group_b,
                                     "results": results, "volcano": volcano}
        # 全量 volcano（~特徴数）は上の last_differential に保持し save_volcano_figure から
        # 使う。payload には載せない——先頭の summary が巨大 volcano 配列＋文脈切り詰めで
        # 埋没し、解釈モデルが有意件数を読めず「全て ns」と誤読する退行を避けるため。
        payload = {"status": "success", "kind": "two_group",
                   "group_a": group_a, "group_b": group_b,
                   "resolved_class_ids": resolved,
                   "resolved_samples": resolved_samples,
                   "n_a": n_a, "n_b": n_b,
                   "summary": summary, "caveats": caveats,
                   "volcano_note": "全特徴の volcano 点列は本要約に非同梱。"
                                   "save_volcano_figure で図示できます。"}
    else:
        return json.dumps({"status": "error",
                           "message": "group_a と group_b の両方を指定してください（2群比較）。"
                                      "完全 Class ID か因子トークン（例 group_a='24M', group_b='9w'）で"
                                      "関心のある2群を切り出せます。多群 ANOVA は現状非対応です。"},
                          ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, indent=2)
