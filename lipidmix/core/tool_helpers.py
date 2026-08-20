"""ツール本体が使う整形・同定・検証ヘルパ（PCA プロット／ローディング整形、
ARF 要約整形、identity 正規化、検証ドシエ、前処理行列ビルダ）。

依存: mcp_core / session_state / 各 reader / lipid_identity / knowledge_store /
peak_verification（いずれも下位レイヤ）。tools_* / server は import しない。
session への参照は session_state.session（動的）で行う。
"""
from pathlib import Path
import json

from lipidmix.msdial import lipid_identity
import knowledge_store
from lipidmix.msdial import peak_verification as pv
from lipidmix.core import session_state
from lipidmix.pai2.reader import get_signal_to_noise
from lipidmix.core import mcp_core


_IDENTITY_TABLES = None


def _identity_tables():
    """同梱の RefMet/LIPID MAPS 対応表を1回だけ読み込みキャッシュする。"""
    global _IDENTITY_TABLES
    if _IDENTITY_TABLES is None:
        _IDENTITY_TABLES = lipid_identity.load_reference_tables()
    return _IDENTITY_TABLES


def _build_verification_dossier(feat: dict, vocab: dict) -> dict:
    """1 feature の検証ドシエを組み立てる（決定的チェック + 生物学的妥当性の材料）。"""
    name = feat.get("name") or ""
    ontology = feat.get("ontology") or ""
    formula = feat.get("formula")
    adduct = feat.get("adduct")
    observed_mz = feat.get("m/z")
    ion_mode = feat.get("ion_mode")
    ion_mode_name = ion_mode.name if hasattr(ion_mode, "name") else str(ion_mode)
    rt = (feat.get("time") or {}).get("rt")
    sn = get_signal_to_noise(feat)

    mass_error = pv.mass_error_ppm(observed_mz, formula, adduct)
    adduct_check = pv.adduct_consistency(adduct, ion_mode_name, ontology)
    msms = pv.msms_evidence(feat)
    class_token = pv.extract_class_token(name, ontology)
    caveats = pv.ether_caveats(name, ontology)
    if msms["caveat"]:
        caveats = caveats + [msms["caveat"]]

    if name.strip():
        cov = knowledge_store.coverage([f"{name} {ontology}"], mcp_core.KNOWLEDGE_DIR, vocab)
        matches_info = next(iter(cov.values()))["matches"]
        candidate_slugs = [m["slug"] for m in matches_info]
        bio = {
            "class_token": class_token,
            "vocab_hits": pv.vocab_hits(class_token, vocab),
            "candidate_knowledge_slugs": candidate_slugs,
            "caveats": caveats,
        }
        instruction = (
            "candidate_knowledge_slugs を knowledge_expand で裏取りし、この試料系に"
            "この脂質種が生物学的に妥当か・表記の落とし穴に当たらないかを判断して"
            "総合判定せよ。"
        )
    else:
        bio = {
            "class_token": None,
            "vocab_hits": [],
            "candidate_knowledge_slugs": [],
            "caveats": ["アノテーション無しにつき生物学的妥当性は判定不可。"],
        }
        instruction = "アノテーションが無いため分析化学的事実のみで判断せよ。"

    identity_block = lipid_identity.build_identity_block(
        feat, _identity_tables(),
        mass_error_band=mass_error["band"],
        adduct_band=adduct_check["band"],
        msms_band=msms["band"],
    )

    return {
        "status": "success",
        "identity": {
            "id": feat.get("id"),
            "name": name or None,
            "ontology": ontology or None,
            "formula": formula,
            "adduct": adduct,
            "observed_mz": observed_mz,
            "rt": rt,
            "ion_mode": ion_mode_name,
            "signal_to_noise": sn,
        },
        "analytical_checks": {
            "mass_error": mass_error,
            "adduct_consistency": adduct_check,
            "msms": msms,
        },
        "biological_plausibility": bio,
        "identity_normalization": identity_block,
        "llm_decision": {
            "instruction": instruction,
            "deterministic_summary": (
                f"mass_error={mass_error['band']}, adduct={adduct_check['band']}, "
                f"msms={msms['band']}"
            ),
        },
    }


def _pca_scatter_arrays(plot: dict):
    """session_state.session.arf.last_pca_plot から散布図用の配列とラベルを取り出す（純ロジック）。"""
    points = plot.get("points", [])
    xs = [float(p["x"]) for p in points]
    ys = [float(p["y"]) for p in points]
    labels = [p.get("label") for p in points]
    return (
        xs, ys, labels,
        plot.get("x_label", "PC1"),
        plot.get("y_label", "PC2"),
        plot.get("title", "PCA"),
    )


def _remember_arf_pca_plot(
    pca_result: dict,
    sample_names: list[str],
    title: str,
    groups: dict[str, str | None] | None = None,
) -> None:
    """ARF系PCAのサンプル別スコアを session_state.session.arf.last_pca_plot に保存する。"""
    coords = pca_result.get("components", [])
    evr = pca_result["explained_variance_ratio"]
    groups = groups or {}
    points = []
    for i, name in enumerate(sample_names):
        if i < len(coords) and len(coords[i]) >= 2:
            point = {"x": float(coords[i][0]), "y": float(coords[i][1]), "label": name}
            if groups.get(name) is not None:
                point["group"] = groups[name]
            points.append(point)
    session_state.session.arf.last_pca_plot = {
        "title": title,
        "x_label": f"PC1 ({evr[0] * 100:.2f}%)",
        "y_label": f"PC2 ({evr[1] * 100:.2f}%)",
        "points": points,
    }


def _format_pca_plot_block(
    pca_result: dict,
    sample_names: list[str],
    title: str,
    intro: str,
    groups: dict[str, str | None] | None = None,
) -> str:
    """PCAスコアの要約ヘッダ＋群別サンプル数＋座標点列(JSON)を返す。

    LLM がこの座標から散布図を自描画できるよう per-sample の点列を ```json
    フェンスで同梱する。点列は session_state.session.arf.last_pca_plot にも別途保存
    され（_remember_arf_pca_plot）、save_pca_figure がユーザー要求時の PNG 化に
    使う。呼び出し側はこのブロックを loadings の後（payload 末尾）に置くこと
    （万一の下流截断で loadings ではなく座標点を失うようにするため）。
    """
    groups = groups or {}
    evr = pca_result["explained_variance_ratio"]
    coords = pca_result.get("components", [])
    lines = [
        intro.rstrip("\n"),
        f"- {title}",
        f"- PC1 ({evr[0] * 100:.2f}%) × PC2 ({evr[1] * 100:.2f}%)",
    ]
    labeled = [groups[n] for n in sample_names if groups.get(n) is not None]
    if labeled:
        from collections import Counter
        counts = ", ".join(f"{g}={c}" for g, c in sorted(Counter(labeled).items()))
        lines.append(f"- 群別サンプル数: {counts}")
    else:
        lines.append(f"- サンプル数: {len(sample_names)}")

    points = []
    for i, name in enumerate(sample_names):
        if i < len(coords) and len(coords[i]) >= 2:
            point = {
                "pc1": round(float(coords[i][0]), 4),
                "pc2": round(float(coords[i][1]), 4),
                "sample": name,
            }
            if groups.get(name) is not None:
                point["group"] = groups[name]
            points.append(point)
    plot_data = {
        "x_label": f"PC1 ({evr[0] * 100:.2f}%)",
        "y_label": f"PC2 ({evr[1] * 100:.2f}%)",
        "points": points,
    }
    lines.append(
        "- 上記座標から散布図を描画してください（group があれば群ごとに色分け・凡例付き）。"
        "PNG が必要なときのみ save_pca_figure を実行します。"
    )
    lines.append("```json")
    lines.append(json.dumps(plot_data, ensure_ascii=False))
    lines.append("```")
    return "\n".join(lines) + "\n"


def _format_pca_loadings_md(loading_features: list[dict], header: str) -> str:
    """arf_reader.get_pca_loading_features の構造化結果を Markdown 要約に整形する（共通）。"""
    text = header
    for pc in loading_features:
        text += f"\n##### 🔹 {pc['pc']} (説明分散比: {pc['var_ratio']:.2f}%)\n"
        for label, items in (("正", pc["positive"]), ("負", pc["negative"])):
            text += f"**【{label}の寄与 上位ピーク】**\n"
            for idx, item in enumerate(items, 1):
                ann = f" - *{item['annotation']}*" if item["annotation"] else " - *Unknown*"
                text += (f"  {idx}. ID: {item['id']} (Loading: `{item['value']:.6f}`){ann} "
                         f"[m/z: {item['m_z']:.4f}, RT: {item['rt']:.2f} min]\n")
    return text


def _format_arf_tag_summary(tag_index: dict | None) -> str:
    if not tag_index:
        return "- **MS-DIALタグ**: タグファイルは読み込まれていません。\n"
    summary = tag_index.get("summary", {})
    definitions = summary.get("definitions", [])
    tag_counts = ", ".join(
        f"{item['label']} (sample={item['sample_peaks']}, alignment={item['alignment_spots']})"
        for item in definitions
    ) or "定義なし"
    return (
        f"- **MS-DIALタグファイル**: サンプル用 {summary.get('sample_tag_files', 0)} 件 "
        f"(ARFとの一致 {summary.get('matched_arf_samples', 0)}/{summary.get('arf_samples', 0)}), "
        f"アラインメント用 {'あり' if summary.get('alignment_tag_file') else 'なし'}\n"
        f"- **タグ付きピーク数**: サンプル別 {summary.get('tagged_sample_peaks', 0)} 件, "
        f"アラインメントスポット {summary.get('tagged_alignment_spots', 0)} 件\n"
        f"- **タグファイル未対応サンプル**: {summary.get('unmatched_arf_samples', 0)} 件\n"
        f"- **利用可能タグ**: {tag_counts}\n"
    )


def _class_factors_by_position(class_ids) -> dict[str, list[str]]:
    """Class ID を `_` で分割し、位置(因子)ごとの値トークン語彙を集計する。

    例: {Cerebellum_gf_AIN, Hippocampus_spf_HFD, ...} →
        {"0": ["Cerebellum", "Hippocampus"], "1": ["gf", "spf"], "2": ["AIN", "HFD"]}
    部分指定（class_ids / group_levels）に使える有効トークンの発見を助ける。
    """
    by_position: dict[int, set[str]] = {}
    for class_id in class_ids:
        for position, token in enumerate(str(class_id).split("_")):
            if token:
                by_position.setdefault(position, set()).add(token)
    return {str(position): sorted(tokens) for position, tokens in sorted(by_position.items())}


def _format_arf_class_summary(class_index: dict | None) -> str:
    if not class_index:
        return "- **Class IDメタデータ**: `.mddata` は見つかりませんでした。\n"
    counts = class_index.get("class_counts", {})
    formatted = ", ".join(f"{class_id}={count}" for class_id, count in counts.items())
    return (
        f"- **Class IDメタデータ**: {Path(class_index['mddata_path']).name}\n"
        f"- **Class ID分布**: {formatted or 'クラスなし'}\n"
    )


def _format_arf_parse_summary(features: list[dict]) -> str:
    block_indices = {
        spot.get("SourceBlockIndex")
        for spot in features
        if isinstance(spot, dict) and spot.get("SourceBlockIndex") is not None
    }
    if not block_indices:
        return ""
    block_count = len(block_indices)
    mode = "multi-block stream" if block_count > 1 else "single block"
    return f"- **ARF parse mode**: {mode}; decoded blocks={block_count}\n"


def _format_arf_class_filter(stats: dict | None) -> str:
    if not stats or not stats.get("requested_class_ids"):
        return ""
    # matched_class_ids は「**選択されたサンプルが持つ** Class ID」であって「spec の
    # 展開先」ではない。統合トークン空間では spec が Class ID の一部だけを選ぶため
    # （class_ids=["6h"] → ILG/control の 6h サンプルのみ）、Class ID だけを見せると
    # 「ILG と control の全サンプルが入っている」と誤読され、選択範囲を過大に見せる。
    # 曖昧さを消せるのは実サンプル名なので、件数と名前を必ず併記する。
    matched = stats.get("matched_class_ids") or []
    selected = stats.get("matched_samples") or []
    matched_line = ""
    if selected:
        shown = ", ".join(selected[:10])
        more = f" ほか{len(selected) - 10}件" if len(selected) > 10 else ""
        class_note = f"（Class ID: {', '.join(matched)}）" if matched else ""
        matched_line = f"- **選択サンプル**: {len(selected)} 件{class_note}: {shown}{more}\n"
    # 統合トークン空間ではサンプル名経由で QC/blank を掴み得る。既定で落としたことを
    # 黙らせず、含める手段（include_roles）とセットで開示する。
    excluded = stats.get("excluded_by_role") or []
    excluded_line = ""
    if excluded:
        shown = ", ".join(excluded[:10])
        more = f" ほか{len(excluded) - 10}件" if len(excluded) > 10 else ""
        excluded_line = (
            f"- **role により除外**: {len(excluded)} 件（{shown}{more}）。"
            '含めるには include_roles=["sample", "qc"] を指定\n'
        )
    # 「未対応 N 件」は語彙全体の件数。そのうち実際に選択から外れた件数を併記して、
    # 「N 件が除外された」という読みが実態と食い違わないようにする。
    dropped_missing = stats.get("missing_samples_excluded") or []
    missing_note = f"（うち選択から除外 {len(dropped_missing)} 件）" if dropped_missing else ""
    return (
        f"- **Class IDフィルタ**: `{', '.join(stats['requested_class_ids'])}`\n"
        f"{matched_line}"
        f"{excluded_line}"
        f"- **Class IDフィルタ後**: スポット {stats['after_spots']}/{stats['before_spots']}, "
        f"サンプル別ピーク {stats['after_sample_peaks']}/{stats['before_sample_peaks']}, "
        f"メタデータ未対応サンプル {stats.get('missing_samples', 0)} 件{missing_note}\n"
    )


def _format_arf_tag_filter(stats: dict | None) -> str:
    if not stats or not stats.get("requested_tags"):
        return ""
    return (
        f"- **タグフィルタ**: scope=`{stats['scope']}`, mode=`{stats['mode']}`, "
        f"tags=`{', '.join(stats['requested_tags'])}`, "
        f"missing_sample_policy=`{stats.get('missing_sample_policy', 'error')}`\n"
        f"- **タグフィルタ後**: スポット {stats['after_spots']}/{stats['before_spots']}, "
        f"サンプル別ピーク {stats['after_sample_peaks']}/{stats['before_sample_peaks']}\n"
    )


def _pp_build_matrix(features, props):
    from lipidmix.arf.reader import build_pca_matrix
    return build_pca_matrix(features, use_properties=props)


def _pp_has_preprocessed() -> bool:
    return getattr(session_state.session.arf, "feature_matrix", None) is not None
