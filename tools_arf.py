"""ARF（PeakProperties 等・サンプル別強度）ツール群。

arf_list_tags/classes/sample_roles, arf_preprocess, arf_pca_preprocessed,
arf_parser, arf_re_pca, arf_differential。deps: mcp_core / session_state /
path_resolvers / tool_helpers / msdial_* / preprocessing / differential /
arf_reader（arf_reader は関数内 import で、テストの patch.object(server.arf_reader)
が共有 module 経由で効くようにする）。

_filter_arf_spots / _pp_build_matrix はテストが差し替える対象なので、正準定義元を
module 修飾（path_resolvers.* / tool_helpers.*）で参照し patch が確実に効くようにする。
"""
import json
import math
from pathlib import Path

import differential
import path_resolvers
import preprocessing
import session_state
import tool_helpers
from mcp_core import mcp
from msdial_classes import assign_sample_groups, filter_arf_by_class_ids
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
    "arf_preprocess",
    "arf_pca_preprocessed",
    "arf_parser",
    "arf_re_pca",
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
    return json.dumps({"status": "success", "counts": counts, "samples": meta},
                      ensure_ascii=False, indent=2)


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
    matrix, sample_names, feature_names = tool_helpers._pp_build_matrix(session_state.session.filtered_features, props)
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
    session_state.session.feature_matrix = matrix2
    session_state.session.pp_sample_names = sample_names
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
) -> list:
    """arf_preprocess で用意した前処理後行列で PCA を実行する。

    通常の arf_parser 経路（生行列）とは独立で、既定挙動を変えない。
    """
    if not _pp_has_preprocessed():
        return ["前処理後の行列がありません。先に arf_preprocess を実行してください。"]
    from arf_reader import run_pca, get_pca_loading_features
    matrix = session_state.session.feature_matrix
    sample_names = session_state.session.pp_sample_names
    feature_names = session_state.session.pp_feature_names
    try:
        pca_result = run_pca(matrix, n_components=components, log_transform=log_transform)
    except Exception as exc:
        return [f"PCA 実行に失敗しました: {exc}"]

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
        f"{plot_block}{loadings_block}"
    )
    return [text]


@mcp.tool()
def arf_parser(
    file_path: str | None = None,
    props: list[str] = ["height"],
    components: int | None = None,
    top_features: int = 10,
    log_transform: bool = False,
    min_detection_rate: float = 0.0,
    tag_labels: list[str] | None = None,
    tag_mode: str = "any",
    tag_scope: str = "sample_peak",
    tag_directory: str | None = None,
    missing_sample_policy: str = "error",
    class_ids: list[str] | None = None,
    class_missing_sample_policy: str = "error",
    group_levels: list[str] | None = None,
) -> list:
    """
    .arf ファイルに対応する解析用関数
    指定されたARFファイルを読み込み、PCAを実行します。
    解析結果のテキスト要約（正負のLoading上位10件含む）と、PCAのスコアプロット画像を同時に返します。

    引数:
    - file_path: 解析する .arf ファイルのパス (省略時は自動検索)
    - props: PCAに使用するプロパティのリスト (デフォルト: ["height"])
    - components: 計算する主成分の数
    - top_features: 各主成分から抽出する正・負の寄与トップ件数 (デフォルト: 10)
    - log_transform: [任意] PCA前に log10 変換を適用する（強度の歪みを抑え条件分離が向上しやすい。既定 False）
    - min_detection_rate: [任意] 特徴量の実検出率(非ギャップフィル)による足切り 0.0-1.0（既定 0.0=無効）
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

    file_path = resolve_arf_file_path(file_path)
    if not file_path:
        return ["データディレクトリに .arf ファイルが見つかりませんでした。"]

    # 外部モジュールからのインポート
    from arf_reader import extract_peak_properties, build_pca_matrix, run_pca, get_pca_loading_features

    try:
        deserialized_and_formatted_data = session_state.session.load_data(file_path, tag_directory=tag_directory)
        if not isinstance(deserialized_and_formatted_data, list):
            return ["デシリアライズ結果がリストではありません。"]

        analysis_data, tag_filter_stats = filter_arf_by_tags(
            deserialized_and_formatted_data,
            session_state.session.arf_tag_index or {},
            tag_labels,
            mode=tag_mode,
            scope=tag_scope,
            missing_sample_policy=missing_sample_policy,
        )
        if not analysis_data:
            return ["指定されたMS-DIALタグ条件に一致するARFピークが見つかりませんでした。arf_list_tags で利用可能タグと件数を確認してください。"]

        analysis_data, class_filter_stats = filter_arf_by_class_ids(
            analysis_data,
            session_state.session.arf_class_index,
            class_ids,
            missing_sample_policy=class_missing_sample_policy,
        )
        if not analysis_data:
            return ["指定されたClass IDに一致するARFサンプルが見つかりませんでした。arf_list_classes で利用可能なClass IDと件数を確認してください。"]
        session_state.session.filtered_features = analysis_data

        peak_df = extract_peak_properties(analysis_data)
        avg_samples = 0
        if len(peak_df) > 0:
            avg_samples = len(peak_df) / len(analysis_data)

        # PCA行列構築（min_detection_rate は任意の検出率フィルタ）
        matrix, sample_names, feature_names = build_pca_matrix(
            analysis_data, use_properties=props,
            min_detection_rate=min_detection_rate,
        )

        if matrix.size == 0:
            return ["[ERROR] PCA 用データを構築できませんでした。"]

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

        # 基本的な要約テキストの作成
        output_text = (
            f"### 📈 ARF 多変量PCA解析完了: {Path(file_path).name}\n"
            f"- **読み込んだ総スポット数**: {len(deserialized_and_formatted_data)}\n"
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
            f"{plot_instruction_text}"  # ← ここにプロット用の指示とデータを追加
            f"{loadings_summary_text}"
        )
        
        # 戻り値の構築（画像オブジェクトを廃止し、テキストのみを返す）
        return [output_text]
        
    except Exception as e:
        import traceback
        return [f"ARF解析中にエラーが発生しました: {str(e)}\n{traceback.format_exc()}"]
    

@mcp.tool()
def arf_re_pca(
    min_intensity: float = 0.0,
    annotation_keyword: str | None = None,
    props: list[str] = ["height"],
    components: int | None = None,
    top_features: int = 10,  # ご要望通りデフォルトを10件に変更
    log_transform: bool = False,
    min_detection_rate: float = 0.0,
    tag_labels: list[str] | None = None,
    tag_mode: str = "any",
    tag_scope: str = "sample_peak",
    missing_sample_policy: str = "error",
    class_ids: list[str] | None = None,
    class_missing_sample_policy: str = "error",
    group_levels: list[str] | None = None,
) -> list:
    """
    ARFデータに対して、強度閾値(min_intensity)や特定のアノテーションキーワード（例: 'PC', 'TG' などの脂質クラス）
    によるフィルタリングを行い、PCAを再実行（やり直し）して解釈のためのデータを返します。
    先に arf_parser を実行してデータがセッションに読み込まれている必要があります。

    引数:
    - min_intensity: 抽出する平均強度の最小閾値 (例: 5000.0)
    - annotation_keyword: 抽出したい脂質クラスや化合物名のキーワード (例: "PC", "LPC", "TG")。部分一致でフィルタリングします。
    - props: PCAに使用するプロパティのリスト (デフォルト: ["height"])
    - components: 計算する主成分の数
    - top_features: 各主成分から抽出する正・負の寄与トップ件数 (デフォルト: 10)
    - log_transform: [任意] PCA前に log10 変換を適用する（既定 False）
    - min_detection_rate: [任意] 特徴量の実検出率(非ギャップフィル)による足切り 0.0-1.0（既定 0.0=無効）
    - tag_labels: [任意] MS-DIALタグ名またはタグIDのリスト
    - tag_mode: any/all/none/not_all のいずれか
    - tag_scope: sample_peak または alignment_spot
    - missing_sample_policy: タグファイル未対応サンプルの扱い。error/exclude/untagged（既定 error）
    - class_ids: [任意] Class ID の指定リスト。各要素は `_` 区切りの部分指定が可能で、
      指定した全トークンを含む Class ID に一致する（要素内AND・順不同）。要素間はOR。完全一致も可。
    - class_missing_sample_policy: Class IDメタデータ未対応サンプルの扱い。error/exclude（既定 error）
    - group_levels: [任意] PCA点の色分け因子の値トークン（例: `["gf","spf"]`）。未指定なら完全Class IDで色分け。
    """
    if session_state.session.features is None:
        return ["先に arf_parser を実行してデータを読み込んでください。"]
        
    # 外部モジュールからのインポート
    from arf_reader import extract_peak_properties, build_pca_matrix, run_pca, get_pca_loading_features

    try:
        # 1. セッションの全データから条件に合うスポットを抽出（共通ヘルパー _filter_arf_spots を利用）
        filtered_spots = path_resolvers._filter_arf_spots(session_state.session.features, min_intensity, annotation_keyword)

        filtered_spots, tag_filter_stats = filter_arf_by_tags(
            filtered_spots,
            session_state.session.arf_tag_index or {},
            tag_labels,
            mode=tag_mode,
            scope=tag_scope,
            missing_sample_policy=missing_sample_policy,
        )

        filtered_spots, class_filter_stats = filter_arf_by_class_ids(
            filtered_spots,
            session_state.session.arf_class_index,
            class_ids,
            missing_sample_policy=class_missing_sample_policy,
        )

        if not filtered_spots:
            tag_condition = (
                f", タグ: {tag_labels}, mode={tag_mode}, scope={tag_scope}"
                if tag_labels else ""
            )
            class_condition = f", Class ID: {class_ids}" if class_ids else ""
            return [
                f"指定された条件（強度 >= {min_intensity}, キーワード: '{annotation_keyword}'"
                f"{tag_condition}{class_condition}）に一致するARFピークが見つかりませんでした。"
            ]

        # フィルタリング後のデータをセッションの状態に反映
        session_state.session.filtered_features = filtered_spots
        
        # 統計情報の計算
        peak_df = extract_peak_properties(filtered_spots)
        avg_samples = len(peak_df) / len(filtered_spots) if len(filtered_spots) > 0 else 0
        
        # 2. 正確に使い回された関数による行列構築とPCAの実行
        matrix, sample_names, feature_names = build_pca_matrix(
            filtered_spots, use_properties=props, min_detection_rate=min_detection_rate,
        )
        if matrix.size == 0:
            return ["[ERROR] フィルタ後のデータから PCA 用行列を構築できませんでした。データ数が少なすぎる可能性があります。"]

        pca_result = run_pca(matrix, n_components=components, log_transform=log_transform)
        session_state.session.pca_result = pca_result

        # サンプル別の群ラベル（既定=完全Class ID、group_levels 指定時はその因子で統合）
        sample_groups = assign_sample_groups(
            sample_names, session_state.session.arf_class_index, group_levels,
        )

        # 3. スコアプロット用データ（共通ヘルパー）
        plot_instruction_text = _format_pca_plot_block(
            pca_result, sample_names,
            title=f"PCA Score Plot (Filtered - Intensity >= {min_intensity}, Keyword: '{annotation_keyword or 'None'}')",
            intro=(
                "\n#### 📊 PCA スコアプロット用データ (フィルタ再計算後)\n"
                "以下のJSONデータを用いて、見やすいインタラクティブな散布図（Scatter Plot）を構築してください。\n"
                "`group` フィールドがある場合は、群ごとに色分け（凡例付き）して群間比較が分かるようにしてください。\n"
            ),
            groups=sample_groups,
        )
        _remember_arf_pca_plot(
            pca_result, sample_names,
            title="PCA Score Plot (arf_re_pca)",
            groups=sample_groups,
        )

        # 4. Loadings 寄与上位（メタデータは大元の session_state.session.features から取得し index ずれを防止）
        loading_features = get_pca_loading_features(
            pca_result, session_state.session.features, feature_names, top_n=top_features,
        )
        loadings_summary_text = _format_pca_loadings_md(
            loading_features, header=f"#### 📊 PCA Loadings 寄与度分析 (各極値トップ {top_features} 件)\n",
        )

        pc1_var = pca_result['explained_variance_ratio'][0] * 100
        pc2_var = pca_result['explained_variance_ratio'][1] * 100

        # 5. レポート全体の結合
        file_name = Path(session_state.session.current_file_path).name if session_state.session.current_file_path else "Unknown"
        output_text = (
            f"### 🔄 ARF フィルタ適用・PCA再計算完了: {file_name}\n"
            f"- **適用フィルタ条件**: 強度最小値=`{min_intensity}`, アノテーションキーワード=`'{annotation_keyword or '指定なし'}'`\n"
            f"{_format_arf_class_filter(class_filter_stats)}"
            f"{_format_arf_tag_filter(tag_filter_stats)}"
            f"- **フィルタ後の有効スポット数**: `{len(filtered_spots)}` / {len(session_state.session.features)} (データ残存率: {len(filtered_spots)/len(session_state.session.features)*100:.1f}%)\n"
            f"- **抽出された総ピークレコード数**: {len(peak_df)}\n"
            f"- **平均サンプル数/スポット**: {avg_samples:.2f}\n"
            f"- **PCA入力行列の形状**: {matrix.shape} (サンプル数 x 特徴量数)\n"
            f"- **PC1 説明分散比**: {pc1_var:.2f}%\n"
            f"- **PC2 説明分散比**: {pc2_var:.2f}%\n"
            f"{plot_instruction_text}"
            f"{loadings_summary_text}"
        )
        
        return [output_text]

    except Exception as e:
        import traceback
        return [f"ARF再PCA実行中にエラーが発生しました: {str(e)}\n{traceback.format_exc()}"]


@mcp.tool()
def arf_differential(
    group_factor: str | None = None,
    group_a: str | None = None,
    group_b: str | None = None,
    q_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
    log_transform: bool = True,
) -> str:
    """前処理後行列で差次的解析を行う。group_a/group_b 指定時は2群 Welch、
    group_factor のみ指定時はその因子の全水準で一元配置 ANOVA。

    先に arf_preprocess を実行して session_state.session.feature_matrix を用意すること
    （未実行なら未正規化 caveat 付きで生行列にフォールバックする）。

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
    group_labels = [(meta.get(n) or {}).get("group") for n in sample_names]
    batch_labels = [(meta.get(n) or {}).get("batch") for n in sample_names]

    caveats: list[str] = []
    recipe = session_state.session.preprocessing_recipe or {}
    if recipe.get("normalize", "none") == "none":
        caveats.append("正規化が未適用のため log2FC は測定量差を含み得ます（arf_preprocess の normalize を検討）。")
    if log_transform:
        caveats.append("log2(x+1) 変換後に検定を実施（強度の歪みを補正）。log2FC は群平均の log2 差＝幾何平均比です。")

    conf = differential.check_confounding(group_labels, batch_labels)
    batch_source = next((m.get("batch_source") for m in meta.values() if m.get("batch_source")), None)
    src_note = "（バッチはファイル名の日付から推定。実バッチ設計と異なる場合あり）" \
        if batch_source == "filename_date" else ""
    if conf["confounded"]:
        caveats.append("交絡: " + conf["detail"] + src_note)
    elif not conf.get("assessable", True):
        caveats.append("交絡評価不可: " + conf["detail"] + src_note)

    if group_a is not None and group_b is not None:
        results = differential.two_group_test(matrix, feature_names, group_labels,
                                              group_a, group_b, log_transform=log_transform)
        results = differential.add_fdr(results)
        summary = differential.summarize_two_group(results, q_threshold, log2fc_threshold)
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
        payload = {"status": "success", "kind": "two_group",
                   "group_a": group_a, "group_b": group_b,
                   "summary": summary, "volcano": volcano, "caveats": caveats}
    elif group_factor is not None:
        results = differential.one_way_anova(matrix, feature_names, group_labels,
                                             log_transform=log_transform)
        results = differential.add_fdr(results)
        sig = [r for r in results if r.get("q") is not None and math.isfinite(r["q"]) and r["q"] <= q_threshold]
        n_tested = sum(1 for r in results if r["p"] is not None and math.isfinite(r["p"]))
        if n_tested == 0:
            caveats.append(
                "検定可能な特徴が0件（全特徴で p=NaN）。水準が空・分散0・または正規化で試料が"
                "NaN化した可能性があります。『有意0件』を『群間差なし』と解釈しないでください。")
        session_state.session.last_differential = {"kind": "anova", "results": results, "volcano": []}
        payload = {"status": "success", "kind": "anova",
                   "n_tested": n_tested,
                   "n_significant": len(sig),
                   "top": sorted(sig, key=lambda r: r["q"])[:15],
                   "caveats": caveats}
    else:
        return json.dumps({"status": "error",
                           "message": "group_a+group_b（2群）か group_factor（ANOVA）のいずれかを指定してください。"},
                          ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, indent=2)
