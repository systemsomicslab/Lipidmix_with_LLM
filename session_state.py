"""解析セッションの状態: AnalysisSession とプロセス唯一の `session` シングルトン。

`session` は全ツールが共有する可変状態。ツールからの参照は必ず
`session_state.session`（module 修飾・動的）で行うこと。テストは
`session_state.session = AnalysisSession()` で丸ごと差し替えるため、
`from session_state import session` のようなスナップショット束縛を作ると
差し替えが見えなくなる。

依存: 各種 reader / preprocessing（いずれも leaf）のみ。tools_* / server は
import しない（循環回避）。`arf_reader` は module オブジェクトのまま参照するので、
テストの `patch.object(server.arf_reader, ...)` が共有 module 経由でここにも効く。
"""
import io
import re
import sys

import arf_reader
import preprocessing
from arf2_reader import deserialize
from eic_aef_reader import parse_eic_aef_css1
from pai2_reader import filter_features_by_params, perform_pca_summary
from msdial_classes import (
    assign_sample_groups,
    attach_class_ids_to_spots,
    discover_arf_class_index,
)
from msdial_tags import (
    attach_tags_to_spots,
    discover_arf_tag_index,
    normalize_sample_name,
)

_BATCH_DATE_RE = re.compile(r"(\d{8})")


def _build_sample_meta(sample_names, class_index):
    """サンプル名から role/group/run_order/batch を組み立てる。"""
    class_ids = {}
    orders = {}
    if class_index:
        by_name = {}
        for rec in class_index.get("records", []):
            key = normalize_sample_name(rec.get("file_name"), strip_processing_timestamp=False)
            if key:
                by_name[key] = rec
        for name in sample_names:
            rec = by_name.get(normalize_sample_name(name, strip_processing_timestamp=False))
            if rec:
                class_ids[name] = rec.get("class_id") or ""
                orders[name] = rec.get("analytical_order")
    roles = preprocessing.detect_sample_roles(sample_names, class_ids)
    groups = assign_sample_groups(sample_names, class_index, None)
    meta = {}
    for name in sample_names:
        m = _BATCH_DATE_RE.search(name)
        # バッチは MS-DIAL メタに専用項目が無いため、ファイル名中の8桁日付から推定する。
        # 出所を batch_source に明示し、交絡判定の脆さ（名前依存）を下流で開示できるようにする。
        meta[name] = {
            "role": roles.get(name, "sample"),
            "group": groups.get(name),
            "run_order": orders.get(name),
            "batch": m.group(1) if m else None,
            "batch_source": "filename_date" if m else None,
        }
    return meta


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
        self.arf_tag_index = None
        self.arf_class_index = None
        self.current_tag_directory = None
        self.last_pca_plot = None  # 直近PCAの描画用データ（save_pca_figure が参照）

        # --- P2a 前処理用の正準行列とサンプルメタ ---
        self.feature_matrix = None       # 前処理後のサンプル×特徴量行列
        self.pp_sample_names = None
        self.pp_feature_names = None
        self.sample_meta = {}            # {sample_name: {role, group, run_order, batch}}
        self.preprocessing_recipe = {}   # 直近適用した前処理レシピ（空=未適用）

        # --- 手動除外集合（PCA 外れサンプル / 特定ピークの可逆・非破壊除外） ---
        self.excluded_samples = set()   # 除外する file_name（サンプル）
        self.excluded_spots = set()     # 除外する MasterAlignmentID（スポット）

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
        ev = summary.get("explained_variance", {}) if isinstance(summary, dict) else {}
        coords = pca_result
        points = []
        try:
            if getattr(coords, "shape", (0, 0))[1] >= 2:
                xs = coords[:, 0].tolist()
                ys = coords[:, 1].tolist()
                points = [{"x": x, "y": y, "label": None} for x, y in zip(xs, ys)]
        except (IndexError, TypeError):
            points = []
        self.last_pca_plot = {
            "title": "PCA (pai2 peak-level)",
            "x_label": f"PC1 ({ev.get('PC1', '')})",
            "y_label": f"PC2 ({ev.get('PC2', '')})",
            "points": points,
        }
        self.pca_index = pca_index
        return summary, img_bytes

    def load_data(self, file_path: str, tag_directory: str | None = None):
        """ファイルパスが前回と異なる場合のみデシリアライズを実行する"""
        if (
            self.current_file_path == file_path
            and self.features is not None
            and self.current_tag_directory == tag_directory
        ):
            print(f"DEBUG: Cache hit for {file_path}", file=sys.stderr)
            if str(file_path).lower().endswith('.arf'):
                self.arf_tag_index = discover_arf_tag_index(
                    file_path, self.features, tag_directory=tag_directory,
                )
                attach_tags_to_spots(self.features, self.arf_tag_index)
                self.arf_class_index = discover_arf_class_index(file_path)
                attach_class_ids_to_spots(self.features, self.arf_class_index)
            return self.features

        # 新しいファイル読込時は手動除外を初期化
        self.excluded_samples = set()
        self.excluded_spots = set()

        print(f"DEBUG: Loading/Deserializing {file_path}", file=sys.stderr)
        with open(file_path, 'rb') as f:
            # 【修正点】ファイルの拡張子を見て正しいパーサーを呼び分ける
            file_ext = str(file_path).lower()
            if file_ext.endswith('.arf'):
                self.features = arf_reader.deserialize(io.BytesIO(f.read()))
            else:
                self.features = deserialize(io.BytesIO(f.read())) # 元からインポートされている arf2_reader 用

            self.current_file_path = file_path
            self.current_tag_directory = tag_directory
            # 新しいファイルを読み込んだら計算結果はリセット
            self.pca_result = None
            self.filtered_features = None

            # ARF ファイルの場合、タグとクラスを処理
            if file_ext.endswith('.arf'):
                self.arf_tag_index = discover_arf_tag_index(
                    file_path, self.features, tag_directory=tag_directory,
                )
                attach_tags_to_spots(self.features, self.arf_tag_index)
                self.arf_class_index = discover_arf_class_index(file_path)
                attach_class_ids_to_spots(self.features, self.arf_class_index)
            else:
                self.arf_tag_index = None
                self.arf_class_index = None

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
