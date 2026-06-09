from html import parser
import json
import os
import sys
import time
from pathlib import Path
import io
from mcp.server.fastmcp import FastMCP, Image
import matplotlib.pyplot as plt
import numpy as np
import pprint
import sys
import base64
import csv
import math
import subprocess
import uuid
import threading
import pandas as pd

# --- LC-MS/MS viewer: reuse pure (non-GUI) logic from lcmsms_viewer.py ---
# Importing the module is safe: its main()/Tk() entry point is guarded by
# `if __name__ == "__main__"`, so nothing here launches the GUI.
from lcmsms_viewer import (
    LibraryEntry,
    InputFile,
    PeakSelection,
    MzMLReader,
    build_isotope_library_rows,
    write_library_rows,
    smooth_eic_points,
    auto_pick_peak,
    summarize_peak,
    safe_filename,
    normalize_grid_rows,
    flatten_grid_panels,
    render_grid_png,
    detect_msconvert,
    ensure_pyteomics_mzml,
    mzml_reader_requirement_message,
)

import test_arf
from test_arf2 import (
    deserialize,
    summarize_arf2_data,
    generate_text_summary,
)
from test_eic_aef import (
    parse_eic_aef_css1,
    summarize_eic_data,
    search_eic_by_mz_range,
    search_eic_by_rt_range,
    top_eic_spots_by_peak_top,
)
from test_pai2 import (
    perform_pca_summary,
    filter_features_by_params,
    inspect_metabolite_details,
    get_top_contributors,
)

mcp = FastMCP("ms-data-parser")

# 絶対パス指定
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"


# --- ステート保持クラス ---
class AnalysisSession:
    def __init__(self):
        self.current_file_path = None
        self.features = None      # デシリアライズ済みの全データ
        self.pca_result = None    # 直近のPCA計算結果
        self.filtered_features = None # フィルタリング後のデータ
        self.filter_params = {}   # 現在のフィルタ条件
        self.last_pca_summary = None
        self.current_aef_file_path = None
        self.eic_features = None

    def apply_filter(self, filter_params: dict | None = None):
        """現データに対して動的にフィルタを適用する。"""
        if filter_params is None:
            filter_params = {}
        self.filter_params = filter_params
        if self.features is None:
            self.filtered_features = None
            return None

        self.filtered_features = filter_features_by_params(self.features, filter_params)
        return self.filtered_features

    def run_pca(self, filter_params: dict | None = None):
        """フィルタリング条件を反映してPCAを再実行する。"""
        if self.features is None:
            raise ValueError("データが読み込まれていません。")
        if filter_params is not None:
            self.apply_filter(filter_params)
        if self.filtered_features is None:
            self.filtered_features = self.features

        summary, img_bytes, pca_result, pca_index, filtered_features = perform_pca_summary(
            self.filtered_features,
            filter_params=self.filter_params,
        )
        self.filtered_features = filtered_features
        self.pca_result = pca_result
        self.last_pca_summary = summary
        self.pca_index = pca_index
        return summary, img_bytes

    def load_data(self, file_path: str):
        """ファイルパスが前回と異なる場合のみデシリアライズを実行する"""
        if self.current_file_path == file_path and self.features is not None:
            print(f"DEBUG: Cache hit for {file_path}", file=sys.stderr)
            return self.features

        print(f"DEBUG: Loading/Deserializing {file_path}", file=sys.stderr)
        with open(file_path, 'rb') as f:
            # 【修正点】ファイルの拡張子を見て正しいパーサーを呼び分ける
            file_ext = str(file_path).lower()
            if file_ext.endswith('.arf'):
                self.features = test_arf.deserialize(io.BytesIO(f.read()))
            else:
                self.features = deserialize(io.BytesIO(f.read())) # 元からインポートされている test_arf2 用
                
            self.current_file_path = file_path
            # 新しいファイルを読み込んだら計算結果はリセット
            self.pca_result = None
            self.filtered_features = None

        return self.features

    def load_eic_data(self, file_path: str):
        """ファイルパスが前回と異なる場合のみEICデータを解析する"""
        if self.current_aef_file_path == file_path and self.eic_features is not None:
            print(f"DEBUG: Cache hit for EIC {file_path}", file=sys.stderr)
            return self.eic_features

        print(f"DEBUG: Loading/Parsing EIC {file_path}", file=sys.stderr)
        self.eic_features = parse_eic_aef_css1(file_path, include_chromatogram=False)
        self.current_aef_file_path = file_path
        return self.eic_features

# インスタンスを1つ作成（サーバー起動中に保持される）
session = AnalysisSession()


mscleanr_warmup_lock = threading.Lock()
mscleanr_warmup_thread: threading.Thread | None = None
mscleanr_warmup_state = {
    "status": "not_started",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}


def _mscleanr_state_snapshot() -> dict:
    with mscleanr_warmup_lock:
        return dict(mscleanr_warmup_state)


def _mscleanr_set_state(**updates) -> None:
    with mscleanr_warmup_lock:
        mscleanr_warmup_state.update(updates)


def _mscleanr_warmup_worker() -> None:
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        code = (
            "import json, mscleanr_bridge as b; "
            "print('MSCLNR_JSON:' + json.dumps(b.check_dependencies(force=True), ensure_ascii=False))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=600,
            check=False,
        )
        result = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith("MSCLNR_JSON:"):
                result = json.loads(line.removeprefix("MSCLNR_JSON:"))
        if result is None:
            result = {
                "ok": False,
                "message": "MS-CleanR warmup subprocess did not return dependency JSON.",
                "errors": ["MS-CleanR warmup subprocess did not return dependency JSON."],
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            }
        _mscleanr_set_state(
            status="ready" if result.get("ok") else "failed",
            finished_at=time.time(),
            result=result,
            error=None if result.get("ok") else result.get("message"),
        )
    except Exception as exc:
        import traceback
        _mscleanr_set_state(
            status="failed",
            finished_at=time.time(),
            result=None,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )


def _start_mscleanr_warmup(force: bool = False) -> dict:
    global mscleanr_warmup_thread
    with mscleanr_warmup_lock:
        status = mscleanr_warmup_state.get("status")
        if status == "running":
            return dict(mscleanr_warmup_state)
        if status == "ready" and not force:
            return dict(mscleanr_warmup_state)
        mscleanr_warmup_state.update({
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        })
        mscleanr_warmup_thread = threading.Thread(
            target=_mscleanr_warmup_worker,
            name="mscleanr-warmup",
            daemon=True,
        )
        mscleanr_warmup_thread.start()
        return dict(mscleanr_warmup_state)


def _format_mscleanr_warmup_state(state: dict) -> str:
    result = state.get("result") or {}
    lines = [
        "### MS-CleanR warmup status",
        f"- status: {state.get('status')}",
    ]
    if state.get("started_at"):
        lines.append(f"- started_at_unix: {state['started_at']:.3f}")
    if state.get("finished_at"):
        lines.append(f"- finished_at_unix: {state['finished_at']:.3f}")
    if result:
        lines.extend([
            f"- rpy2: {result.get('rpy2')}",
            f"- r_available: {result.get('r_available')}",
            f"- mscleanr_installed: {result.get('mscleanr_installed')}",
            f"- r_version: {result.get('r_version')}",
            f"- mscleanr_version: {result.get('mscleanr_version')}",
            f"- message: {result.get('message')}",
        ])
        bootstrap = result.get("bootstrap_env")
        if bootstrap:
            lines.append("\nBootstrap environment:")
            lines.append(f"```json\n{json.dumps(bootstrap, indent=2, ensure_ascii=False)}\n```")
    if state.get("error"):
        lines.append("\nError:")
        lines.append(f"```text\n{state['error']}\n```")
    return "\n".join(lines)


def _require_mscleanr_warmup_ready() -> str | None:
    state = _mscleanr_state_snapshot()
    if state.get("status") == "ready":
        return None
    if state.get("status") == "running":
        return (
            "MS-CleanR warmup is still running. "
            "Call mscleanr_warmup_status and retry after status becomes ready."
        )
    if state.get("status") == "failed":
        return (
            "MS-CleanR warmup failed. Run mscleanr_start_warmup(force=True) "
            "after fixing the reported environment issue.\n\n"
            + _format_mscleanr_warmup_state(state)
        )
    _start_mscleanr_warmup(force=False)
    return (
        "MS-CleanR warmup has been started in the background. "
        "Call mscleanr_warmup_status until status is ready, then retry this tool."
    )


def resolve_arf_file_path(file_path: str | None = None) -> str | None:
    """.arfファイルのパスを解決するヘルパー"""
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".arf")
    if not file_paths or not isinstance(file_paths, list):
        return None
    if len(file_paths) == 0 or (len(file_paths) == 1 and file_paths[0].startswith("データディレクトリ")):
        return None
    return file_paths[0]


def resolve_arf2_file_path(file_path: str | None = None) -> str | None:
    """.arf2ファイルのパスを解決するヘルパー"""
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".arf2")
    if not file_paths or not isinstance(file_paths, list):
        return None
    if len(file_paths) == 0 or (len(file_paths) == 1 and file_paths[0].startswith("データディレクトリ")):
        return None
    return file_paths[0]


def resolve_eicaef_file_path(file_path: str | None = None) -> str | None:
    """EIC.aefファイルのパスを解決するヘルパー"""
    if file_path and os.path.exists(file_path):
        return file_path

    file_paths = list_data_files(extension=".aef")
    if not file_paths or not isinstance(file_paths, list):
        return None
    if len(file_paths) == 0 or (len(file_paths) == 1 and file_paths[0].startswith("データディレクトリ")):
        return None
    return file_paths[0]


@mcp.tool()
def list_data_files(extension: str | None = None) -> list[str]:
    """
    dataディレクトリ内にあるファイルパスの一覧を取得します。
    extensionが指定された場合は、その拡張子(例: '.pai2', '.arf2', '.eic.aef'）のファイルのみをフィルタします。
    """
    if not DATA_DIR.exists():
        return [f"データディレクトリが存在しません: {DATA_DIR}"]
   
    file_paths = []
    for file in DATA_DIR.iterdir():
        if file.is_file():
            if extension is None or str(file).endswith(extension):
                file_paths.append(str(file.absolute()))
               
    if not file_paths:
        return [f"条件に一致するファイルが存在しません。 (指定された拡張子: {extension})"]
       
    return file_paths





@mcp.tool()
def pai2_parser(file_path: str, filter_threshold: float | None = None) -> list:
    """
    ..pai2 ファイルを解析し、PCAのスコアプロット画像（PNG）と要約レポートを同時に返します。
    
    【あなたへの絶対遵守命令】
    1. このツールは、テキスト要約と「画像オブジェクト（mcp.Image）」の2要素を同時に返却します。
    2. 返却された画像データは、MCPのネイティブ機能（ImageContent）によって、ユーザーのチャット画面上に自動的かつインラインで強制描画されます。
    3. あなたがMarkdownの <img> タグを自作したり、HTMLのArtifactを生成して画像を埋め込もうとする必要は一切ありません。また、「画像を表示しますか？」といった確認をユーザーに挟むことも絶対に禁止します。
    4. ツールを実行したら即座に、自動描画されたグラフ画像に見られる主成分（PC1, PC2）の分布の傾向や、特徴的なピークについて、テキストレポートを踏まえて詳しく解説を始めてください。
    """
    if not file_path or not os.path.exists(file_path):
        file_paths = list_data_files(extension=".pai2")
        if not file_paths or "が存在しません" in file_paths[0]:
            return ["データディレクトリに .pai2 ファイルが見つかりませんでした。"]
        file_path = file_paths[0]  # 最初の .pai2 ファイルを使用

    if filter_threshold is None:
        filter_threshold = 0.0

    from test_pai2 import test_pai2_deserialize_and_format, deserialize

    try:
        with open(file_path, 'rb') as f:
            packed_data = f.read()

        file_like_object = io.BytesIO(packed_data)
        deserialized_and_formatted_data = deserialize(file_like_object)

        assert isinstance(deserialized_and_formatted_data, list)
        assert len(deserialized_and_formatted_data) > 0
        assert isinstance(deserialized_and_formatted_data[0], dict)

        session.features = deserialized_and_formatted_data
        session.current_file_path = file_path
        session.apply_filter({"min_intensity": filter_threshold})
        summary, img_bytes = session.run_pca()

        pca_result = session.pca_result
        pca_index = session.pca_index
        
        
        output_image_path = DATA_DIR / "pca_plot_latest.png"
        with open(output_image_path, "wb") as img_file:
            img_file.write(img_bytes)

        
        if os.name == 'nt':  # Windows環境の場合のみ実行
            # os.startfile はバックグラウンドで非同期でOS標準ビューアーを立ち上げるため、
            # MCPサーバー側の処理やタイムアウトを一切邪魔しません
            os.startfile(str(output_image_path.absolute()))
        
        # FastMCP の Image クラスでラップして返す
        mcp_image = Image(data=img_bytes, format="png")
        
        text_report = (
            f"### 解析完了: {Path(file_path).name}\n"
            f"(summary は S/N 情報を含みます)\n"
            + json.dumps(summary, indent=2)
        )
        return [text_report, mcp_image]

    except Exception as e:
        return [f"エラーが発生しました: {str(e)}"]



@mcp.tool()
def pai2_get_top_metabolites(top_n: int = 10) -> str:
    """
    pai2ファイルの直近のPCA解析結果から、主成分に寄与している上位の代謝物リストを返します。
    """
    if session.pca_result is None:
        return "先に analyze_pai2_pca を実行してください。"
    
    from test_pai2 import get_top_contributors

    top_list = get_top_contributors(session.filtered_features, session.pca_result, top_n)
    return f"上位{top_n}件の代謝物:\n{json.dumps(top_list, indent=2, ensure_ascii=False)}"


@mcp.tool()
def pai2_inspect_metabolite_details(metabolite_id: str | None = None, metabolite_name: str | None = None) -> str:
    """特定の代謝物について、強度・S/N・MS/MS相当の情報を返す。

    返り値には signal_to_noise フィールドが含まれます。
    """
    if session.filtered_features is None:
        return "先に pai2_parser を実行してデータを読み込んでください。"

    details = inspect_metabolite_details(
        session.filtered_features,
        metabolite_id=metabolite_id,
        metabolite_name=metabolite_name,
    )
    return json.dumps(details, indent=2, ensure_ascii=False)


@mcp.tool()
def pai2_update_analysis_filter(min_intensity: float = 0.0, min_sn: float = 0.0) -> str:
    """min_intensity / min_sn を更新してPCAを再実行する。"""
    if session.features is None:
        return "先に pai2_parser を実行してデータを読み込んでください。"

    old_count = len(session.filtered_features or session.features)
    old_pc1 = None
    if session.last_pca_summary and session.last_pca_summary.get("explained_variance"):
        try:
            old_pc1 = float(session.last_pca_summary["explained_variance"]["PC1"].strip("%")) / 100.0
        except Exception:
            old_pc1 = None

    new_filter = {"min_intensity": min_intensity}
    if min_sn:
        new_filter["min_sn"] = min_sn

    session.apply_filter(new_filter)
    summary, img_bytes = session.run_pca()
    new_count = len(session.filtered_features or [])

    parts = [
        f"フィルタ更新: min_intensity={min_intensity}, min_sn={min_sn}",
        f"前件数: {old_count}",
        f"後件数: {new_count}",
    ]

    if old_count:
        reduction = 100.0 * (old_count - new_count) / old_count
        parts.append(f"データ損失率: {reduction:.1f}%")

    if summary.get("explained_variance"):
        parts.append(f"PC1 explained variance: {summary['explained_variance']['PC1']}")
        parts.append(f"PC2 explained variance: {summary['explained_variance']['PC2']}")

    if old_pc1 is not None and summary.get("explained_variance"):
        try:
            new_pc1 = float(summary["explained_variance"]["PC1"].strip("%")) / 100.0
            delta = new_pc1 - old_pc1
            parts.append(f"PC1 explained variance change: {delta:+.2%}")
        except Exception:
            pass

    parts.append("フィルタ後の上位寄与代謝物:")
    top_list = get_top_contributors(session.filtered_features, session.pca_result, top_n=5)
    parts.append(json.dumps(top_list, indent=2, ensure_ascii=False))

    return "\n".join(parts)


@mcp.tool()
def arf_parser(
    file_path: str | None = None,
    props: list[str] = ["height"],
    components: int | None = None,
    top_features: int = 10
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
    """
    
    file_path = resolve_arf_file_path(file_path)
    if not file_path:
        return ["データディレクトリに .arf ファイルが見つかりませんでした。"]
    
    # 外部モジュールからのインポート
    from test_arf import extract_peak_properties, build_pca_matrix, run_pca, plot_pca, extract_top_loading_features, _convert_to_times

    try:
        deserialized_and_formatted_data = session.load_data(file_path)
        if not isinstance(deserialized_and_formatted_data, list):
            return ["デシリアライズ結果がリストではありません。"]

        session.filtered_features = deserialized_and_formatted_data

        peak_df = extract_peak_properties(deserialized_and_formatted_data)
        avg_samples = 0
        if len(peak_df) > 0:
            avg_samples = len(peak_df) / len(deserialized_and_formatted_data)

        # PCA行列構築
        matrix, sample_names, feature_names = build_pca_matrix(deserialized_and_formatted_data, use_properties=props)

        if matrix.size == 0:
            return ["[ERROR] PCA 用データを構築できませんでした。"]
        
        # PCA実行
        pca_result = run_pca(matrix, n_components=components)
        
        # PCAスコアプロットの生成
        components = pca_result.get("components", [])
        plot_data_points = []
        
        # PC1とPC2の座標データをサンプル名と紐付ける
        if len(components) > 0 and len(components[0]) >= 2:
            for i, name in enumerate(sample_names):
                plot_data_points.append({
                    "sample": name,
                    "pc1": components[i][0],
                    "pc2": components[i][1]
                })

        # 軸ラベル用に寄与率を取得
        pc1_var = pca_result['explained_variance_ratio'][0] * 100
        pc2_var = pca_result['explained_variance_ratio'][1] * 100

        # LLMに渡すためのJSON構造
        plot_json_data = {
            "title": f"PCA Score Plot ({Path(file_path).name})",
            "x_axis": f"PC1 ({pc1_var:.2f}%)",
            "y_axis": f"PC2 ({pc2_var:.2f}%)",
            "data": plot_data_points
        }
        
        plot_instruction_text = (
            f"\n#### 📊 PCA スコアプロット用データ\n"
            f"以下のJSONデータを用いて、見やすい散布図（Scatter Plot）を描画してください。\n"
            f"各点には `sample` の名前をラベルとして表示するか、ホバー時に確認できるようにしてください。\n"
            f"```json\n{json.dumps(plot_json_data, indent=2, ensure_ascii=False)}\n```\n"
        )
        
        loadings_summary_text = "#### 📊 PCA Loadings 寄与度分析 (各極値トップ件数)\n"
        
        # PC1 と PC2 (存在する分だけ) の処理を実行
        for pc_idx in range(min(2, len(pca_result["loadings"]))):
            pc_loadings = pca_result["loadings"][pc_idx]
            pc_name = f"PC{pc_idx + 1}"
            var_ratio = pca_result['explained_variance_ratio'][pc_idx] * 100
            
            # 特徴量名、ロード値を紐付けたオブジェクトのリストを作成
            pc_features_list = []
            for feat_idx, loading_value in enumerate(pc_loadings):
                feat_name = feature_names[feat_idx]
                parts = feat_name.split("_")
                if len(parts) >= 3:
                    master_id = int(parts[1])
                    if master_id < len(deserialized_and_formatted_data):
                        spot = deserialized_and_formatted_data[master_id]
                        pc_features_list.append({
                            "id": master_id,
                            "value": loading_value,
                            "annotation": spot.get("Name", ""),
                            "m_z": spot.get("MassCenter"),
                            "rt": spot.get("RT")
                        })
            
            # 実数値の大きさで降順（大きい順）にソート
            pc_features_list.sort(key=lambda x: x["value"], reverse=True)
            
            # 正の寄与トップN（リストの先頭から）
            pos_top_n = pc_features_list[:top_features]
            # 負の寄与トップN（リストの末尾から取得し、負の方向に大きい順＝昇順にするため反転）
            neg_top_n = pc_features_list[-top_features:][::-1]
            
            loadings_summary_text += f"\n##### 🔹 {pc_name} (説明分散比: {var_ratio:.2f}%)\n"
            
            loadings_summary_text += "**【正の寄与 上位ピーク】**\n"
            for idx, item in enumerate(pos_top_n, 1):
                ann = f" - *{item['annotation']}*" if item['annotation'] else " - *Unknown*"
                loadings_summary_text += f"  {idx}. ID: {item['id']} (Loading: `{item['value']:.6f}`){ann} [m/z: {item['m_z']:.4f}, RT: {item['rt']:.2f} min]\n"
                
            loadings_summary_text += "**【負の寄与 上位ピーク】**\n"
            for idx, item in enumerate(neg_top_n, 1):
                ann = f" - *{item['annotation']}*" if item['annotation'] else " - *Unknown*"
                loadings_summary_text += f"  {idx}. ID: {item['id']} (Loading: `{item['value']:.6f}`){ann} [m/z: {item['m_z']:.4f}, RT: {item['rt']:.2f} min]\n"

        # 基本的な要約テキストの作成
        output_text = (
            f"### 📈 ARF 多変量PCA解析完了: {Path(file_path).name}\n"
            f"- **読み込んだ総スポット数**: {len(deserialized_and_formatted_data)}\n"
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
    top_features: int = 10  # ご要望通りデフォルトを10件に変更
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
    """
    if session.features is None:
        return ["先に arf_parser を実行してデータを読み込んでください。"]
        
    # 外部モジュールからのインポート
    from test_arf import extract_peak_properties, build_pca_matrix, run_pca
    import json
    from pathlib import Path

    try:
        # 1. セッションに保持されている全データ(session.features)から条件に合うものを抽出
        filtered_spots = []
        for spot in session.features:
            # 強度フィルター (HeightAverage)
            height = spot.get("HeightAverage")
            if height is not None and height < min_intensity:
                continue
                
            # アノテーション（脂質クラス）フィルター (部分一致)
            if annotation_keyword:
                name = spot.get("Name", "")
                if not name or annotation_keyword.lower() not in name.lower():
                    continue
                    
            filtered_spots.append(spot)
            
        if not filtered_spots:
            return [f"指定された条件（強度 >= {min_intensity}, キーワード: '{annotation_keyword}'）に一致する脂質/スポットが見つかりませんでした。"]

        # フィルタリング後のデータをセッションの状態に反映
        session.filtered_features = filtered_spots
        
        # 統計情報の計算
        peak_df = extract_peak_properties(filtered_spots)
        avg_samples = len(peak_df) / len(filtered_spots) if len(filtered_spots) > 0 else 0
        
        # 2. 正確に使い回された関数による行列構築とPCAの実行
        matrix, sample_names, feature_names = build_pca_matrix(filtered_spots, use_properties=props)
        if matrix.size == 0:
            return ["[ERROR] フィルタ後のデータから PCA 用行列を構築できませんでした。データ数が少なすぎる可能性があります。"]
            
        pca_result = run_pca(matrix, n_components=components)
        session.pca_result = pca_result
        
        # 3. LLM（Claude）自律描画用のスコアプロットデータをJSONとして抽出
        components_coords = pca_result.get("components", [])
        plot_data_points = []
        
        if len(components_coords) > 0 and len(components_coords[0]) >= 2:
            for i, name in enumerate(sample_names):
                plot_data_points.append({
                    "sample": name,
                    "pc1": components_coords[i][0],
                    "pc2": components_coords[i][1]
                })

        pc1_var = pca_result['explained_variance_ratio'][0] * 100
        pc2_var = pca_result['explained_variance_ratio'][1] * 100

        plot_json_data = {
            "title": f"PCA Score Plot (Filtered - Intensity >= {min_intensity}, Keyword: '{annotation_keyword or 'None'}')",
            "x_axis": f"PC1 ({pc1_var:.2f}%)",
            "y_axis": f"PC2 ({pc2_var:.2f}%)",
            "data": plot_data_points
        }
        
        plot_instruction_text = (
            f"\n#### 📊 PCA スコアプロット用データ (フィルタ再計算後)\n"
            f"以下のJSONデータを用いて、見やすいインタラクティブな散布図（Scatter Plot）を構築してください。\n"
            f"```json\n{json.dumps(plot_json_data, indent=2, ensure_ascii=False)}\n```\n"
        )
        
        # 4. Loadingsの正負トップ10件をテキスト要約に変換
        loadings_summary_text = f"#### 📊 PCA Loadings 寄与度分析 (各極値トップ {top_features} 件)\n"
        
        for pc_idx in range(min(2, len(pca_result["loadings"]))):
            pc_loadings = pca_result["loadings"][pc_idx]
            pc_name = f"PC{pc_idx + 1}"
            var_ratio = pca_result['explained_variance_ratio'][pc_idx] * 100
            
            pc_features_list = []
            for feat_idx, loading_value in enumerate(pc_loadings):
                feat_name = feature_names[feat_idx]
                parts = feat_name.split("_")
                if len(parts) >= 3:
                    master_id = int(parts[1])
                    # スポットのメタデータは、インデックスずれを防ぐため大元の session.features から確実に取得
                    if master_id < len(session.features):
                        spot = session.features[master_id]
                        pc_features_list.append({
                            "id": master_id,
                            "value": loading_value,
                            "annotation": spot.get("Name", ""),
                            "m_z": spot.get("MassCenter"),
                            "rt": spot.get("RT")
                        })
            
            # 実数値の大きさで降順（大きい順）にソート
            pc_features_list.sort(key=lambda x: x["value"], reverse=True)
            
            # 正の寄与トップN / 負の寄与トップN
            pos_top_n = pc_features_list[:top_features]
            neg_top_n = pc_features_list[-top_features:][::-1]
            
            loadings_summary_text += f"\n##### 🔹 {pc_name} (説明分散比: {var_ratio:.2f}%)\n"
            
            loadings_summary_text += "**【正の寄与 上位ピーク】**\n"
            for idx, item in enumerate(pos_top_n, 1):
                ann = f" - *{item['annotation']}*" if item['annotation'] else " - *Unknown*"
                loadings_summary_text += f"  {idx}. ID: {item['id']} (Loading: `{item['value']:.6f}`){ann} [m/z: {item['m_z']:.4f}, RT: {item['rt']:.2f} min]\n"
                
            loadings_summary_text += "**【負の寄与 上位ピーク】**\n"
            for idx, item in enumerate(neg_top_n, 1):
                ann = f" - *{item['annotation']}*" if item['annotation'] else " - *Unknown*"
                loadings_summary_text += f"  {idx}. ID: {item['id']} (Loading: `{item['value']:.6f}`){ann} [m/z: {item['m_z']:.4f}, RT: {item['rt']:.2f} min]\n"

        # 5. レポート全体の結合
        file_name = Path(session.current_file_path).name if session.current_file_path else "Unknown"
        output_text = (
            f"### 🔄 ARF フィルタ適用・PCA再計算完了: {file_name}\n"
            f"- **適用フィルタ条件**: 強度最小値=`{min_intensity}`, アノテーションキーワード=`'{annotation_keyword or '指定なし'}'`\n"
            f"- **フィルタ後の有効スポット数**: `{len(filtered_spots)}` / {len(session.features)} (データ残存率: {len(filtered_spots)/len(session.features)*100:.1f}%)\n"
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
def arf2_parser(file_path: str | None = None) -> list:
    """
    .arf2 ファイル（MS-DIALの全体カタログ）を解析し、データセットの全体像（メタデータ）を要約して返します。
    このファイルにはサンプル個別の強度データは含まれていないため、PCA等の多変量解析は実行できません。
    データ全体の品質や、アノテーション状況の概観を把握するために使用します。
    """
    file_path = resolve_arf2_file_path(file_path)
    if not file_path:
        return ["データディレクトリに .arf2 ファイルが見つかりませんでした。"]

    from test_arf2 import deserialize, generate_text_summary, summarize_arf2_data
    from pathlib import Path
    import json

    try:
        # ARF2データの読み込み
        with open(file_path, 'rb') as f:
            deserialized_data = deserialize(f)
            
        if not deserialized_data:
            return [".arf2 ファイルのパースに失敗したか、データが空です。"]

        # 要約テキストの生成
        text_summary = generate_text_summary(deserialized_data)
        
        # 将来の検索やフィルタリング用に、カタログデータをセッションに保持しておく
        session.current_file_path = file_path
        session.features = deserialized_data 

        output_text = (
            f"### 📂 ARF2 カタログデータのパース完了: {Path(file_path).name}\n"
            f"このファイルはデータセット全体の要約（平均値等）のみを含んでおり、サンプル別データを持たないためPCAは実行できません。\n\n"
            f"{text_summary}\n\n"
            f"※ 個別のサンプル比較やPCAを行いたい場合は、詳細データを持つ `.arf` (PeakProperties.arf など) を対象に `arf_parser` を使用してください。"
        )

        return [output_text]

    except Exception as e:
        import traceback
        return [f"ARF2解析中にエラーが発生しました: {str(e)}\n{traceback.format_exc()}"]



@mcp.tool()
def eicaef_parser(file_path: str | None = None) -> str:
    """
    .eic.aef ファイルに対応する解析用関数
    ファイルを解析し、テキスト要約を返します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session.load_eic_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        summary = summarize_eic_data(parsed)
        output_text = [
            f"EIC解析完了: {Path(file_path).name}",
            "== 基本要約 ==",
            json.dumps(summary, indent=2, ensure_ascii=False),
            "== コメント ==",
            "この結果をもとに、m/z範囲検索やRT範囲検索、上位PeakTop抽出を実行できます。",
        ]
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC解析中にエラーが発生しました: {str(e)}"


@mcp.tool()
def eicaef_top_peak_tops(file_path: str | None = None, top_n: int = 20) -> str:
    """
    EICデータのPeakTop値で上位スポットを返します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session.load_eic_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        top_spots = top_eic_spots_by_peak_top(parsed, top_n=top_n)
        output_text = [
            f"EIC上位PeakTopスポット: {Path(file_path).name}",
            f"上位{top_n}件:",
        ]
        for spot in top_spots:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} "
                f"max_peak_top={spot['max_peak_top']} num_samples={spot['num_samples']}"
            )
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC上位PeakTop抽出中にエラーが発生しました: {str(e)}"


@mcp.tool()
def eicaef_search_by_mz_range(file_path: str | None = None, min_mz: float = 0.0, max_mz: float = 1000.0, max_results: int = 20) -> str:
    """
    EICデータのm/z範囲でスポットを検索します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session.load_eic_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        matches = search_eic_by_mz_range(parsed, min_mz, max_mz)
        output_text = [
            f"EIC m/z範囲検索: {min_mz} - {max_mz}",
            f"一致件数: {len(matches)}",
            "上位結果:",
        ]
        for spot in matches[:max_results]:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} num_samples={spot['num_samples']}"
            )
        if len(matches) > max_results:
            output_text.append(f"(表示上限: {max_results} 件)")
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC m/z検索中にエラーが発生しました: {str(e)}"


@mcp.tool()
def eicaef_search_by_rt_range(file_path: str | None = None, min_rt: float = 0.0, max_rt: float = 20.0, max_results: int = 20) -> str:
    """
    EICデータのRT範囲でスポットを検索します。
    """
    file_path = resolve_eicaef_file_path(file_path)
    if not file_path:
        return "データディレクトリに .aef ファイルが見つかりませんでした。"

    try:
        parsed = session.load_eic_data(file_path)
        if not isinstance(parsed, list):
            return "EIC解析結果がリストではありません。"

        matches = search_eic_by_rt_range(parsed, min_rt, max_rt)
        output_text = [
            f"EIC RT範囲検索: {min_rt} - {max_rt}",
            f"一致件数: {len(matches)}",
            "上位結果:",
        ]
        for spot in matches[:max_results]:
            output_text.append(
                f"spot_id={spot['spot_id']} rt={spot['rt']} mz={spot['mz']} num_samples={spot['num_samples']}"
            )
        if len(matches) > max_results:
            output_text.append(f"(表示上限: {max_results} 件)")
        return "\n".join(output_text)
    except Exception as e:
        return f"EIC RT検索中にエラーが発生しました: {str(e)}"



def _filter_arf_spots(
    features: list[dict],
    min_intensity: float = 0.0,
    annotation_keyword: str | None = None,
) -> list[dict]:
    """Apply the same lightweight ARF filters used by arf_re_pca."""
    filtered_spots = []
    keyword = annotation_keyword.lower() if annotation_keyword else None
    for spot in features:
        height = spot.get("HeightAverage")
        if height is not None and height < min_intensity:
            continue

        if keyword:
            name = spot.get("Name", "")
            if not name or keyword not in name.lower():
                continue

        filtered_spots.append(spot)
    return filtered_spots


def _build_arf_pivot_table(features: list[dict] | None = None) -> "pd.DataFrame":
    """session.features (ARFデータ) を MS-CleanR ブリッジに渡す横持ちピボット表へ変換する。

    旧 MS_CleanP.process_cleanup に渡していたものと同一の表を構築する:
    1行=1アラインメント特徴、メタデータ列、サンプルごとの <sample>_Intensity 列。
    """
    from test_arf import extract_peak_properties

    source_features = features if features is not None else session.features
    peak_df = extract_peak_properties(source_features)
    if peak_df.empty:
        return peak_df

    df_pivot = peak_df.pivot_table(
        index=[
            'MasterAlignmentID',
            'CompoundName',
            'SpotMassCenter',
            'SpotRT',
            'IonMode',
        ],
        columns='FileName',
        values='PeakHeight',
        aggfunc='mean',
    ).reset_index()

    df_pivot = df_pivot.rename(columns={
        'CompoundName': 'Name',
        'SpotMassCenter': 'MassCenter',
        'SpotRT': 'RT',
    })

    metadata_cols = {'MasterAlignmentID', 'Name', 'MassCenter', 'RT', 'IonMode'}
    rename_dict = {
        col: f"{col}_Intensity"
        for col in df_pivot.columns
        if col not in metadata_cols
    }
    return df_pivot.rename(columns=rename_dict)


