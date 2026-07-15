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
import os
import re

import arf_reader
import preprocessing
from arf2_reader import deserialize
from eic_aef_reader import parse_eic_aef_css1
from pai2_reader import filter_features_by_params
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

# output-format リソースを LLM が pull していないときに、解釈直結のパーサー出力の
# 先頭へ最大1回だけ前置する意味論ダイジェスト（自己完結・~7行）。round-trip 不要で
# ローカルLLM でも意味が届く。全文定義は docs/output_format.md /
# lipidmix://docs/output-format。§2.1 は脂質名ショートハンド文法。
SEMANTICS_CAVEAT = (
    "[意味論] 解釈前に lipidmix://docs/output-format を参照。要点:\n"
    "- 粒度: ARF行=1スポット×1サンプル / ARF2行=全サンプル統合スポット。\n"
    "- IsGapFilled=true は補間値（実測でない）。\n"
    "- Nameの存在≠確定同定。空/Unknown/no MS2:/low score: を区別。\n"
    "- EIC peak_top は横軸座標(RT)で強度でない。強度はmax_intensity。\n"
    "- PAI2は単一サンプル→PCA不能。多変量比較はARF/ARF2。\n"
    "- 脂質名: 34:1(species)と 16:0/18:1(molecular)は別粒度。P-/O-は曖昧(§2.1)。"
)


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
        self.current_aef_file_path = None
        self.eic_features = None
        self.arf_tag_index = None
        self.arf_class_index = None
        self.current_tag_directory = None
        self.last_pca_plot = None  # 直近PCAの描画用データ（save_pca_figure が参照）
        self.last_eic_plot = None  # 直近EICプロット情報（明示的なPNG保存時のみ参照）
        self.last_differential = None  # 直近差次的解析（save_volcano_figure が参照）

        # --- P2a 前処理用の正準行列とサンプルメタ ---
        self.feature_matrix = None       # 前処理後のサンプル×特徴量行列
        self.pp_sample_names = None
        self.pp_feature_names = None
        self.sample_meta = {}            # {sample_name: {role, group, run_order, batch}}
        self.preprocessing_recipe = {}   # 直近適用した前処理レシピ（空=未適用）

        # --- 手動除外集合（PCA 外れサンプル / 特定ピークの可逆・非破壊除外） ---
        self.excluded_samples = set()   # 除外する file_name（サンプル）
        self.excluded_spots = set()     # 除外する MasterAlignmentID（スポット）

        # --- 意味論 caveat ガード（output-format 未 pull 時に1回だけ前置） ---
        # プロセス内で真に1回だけ発火させる。output-format リソースが読まれたら
        # output_format_seen=True になり以後は前置しない。reset_analysis_state /
        # load_data ではリセットしない（データ切替のたびに再注入しないため）。
        self.output_format_seen = False
        self.caveat_emitted = False

    def maybe_prepend_caveat(self, text: str) -> str:
        """解釈直結パーサー出力の先頭へ意味論ダイジェストを最大1回だけ前置する。

        output-format リソースが未 fetch（output_format_seen=False）かつ本プロセスで
        未注入（caveat_emitted=False）のときだけ SEMANTICS_CAVEAT を前置する。環境変数
        LIPIDMIX_CAVEAT_MODE=off で無効化（ローカルの操作ナビゲータ専用デプロイ向け。
        既定 digest）。エラー文字列など解釈材料でない出力には呼ばない。
        """
        mode = os.getenv("LIPIDMIX_CAVEAT_MODE", "digest").strip().lower()
        if mode == "off":
            return text
        if self.output_format_seen or self.caveat_emitted:
            return text
        self.caveat_emitted = True
        return f"{SEMANTICS_CAVEAT}\n\n{text}"

    def reset_analysis_state(self):
        """別データセットへ切り替える際に、前データ由来の解析成果を一括で破棄する。

        新ファイルの load 開始時に必ず呼ぶ。前回の feature_matrix / sample_meta /
        preprocessing_recipe / 差次的解析・PCA・EIC プロットなどが残ると、データ切替後に
        前回データの図や結果を「今のデータのもの」として保存・解釈してしまう。
        本メソッドは解析成果（派生状態）だけを消し、これから load される features や
        current_file_path、EIC キャッシュ（パスで自己検証する）は触らない。
        """
        # 直近解析の出力
        self.pca_result = None
        self.filtered_features = None
        self.filter_params = {}
        self.last_pca_plot = None
        self.last_differential = None
        self.last_eic_plot = None
        # 前処理由来の正準行列とサンプルメタ
        self.feature_matrix = None
        self.pp_sample_names = None
        self.pp_feature_names = None
        self.sample_meta = {}
        self.preprocessing_recipe = {}
        # 手動除外（別データに持ち越さない）
        self.excluded_samples = set()
        self.excluded_spots = set()

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

    def load_data(self, file_path: str, tag_directory: str | None = None):
        """ファイルパスが前回と異なる場合のみデシリアライズを実行する"""
        if (
            self.current_file_path == file_path
            and self.features is not None
            and self.current_tag_directory == tag_directory
        ):
            if str(file_path).lower().endswith('.arf'):
                self.arf_tag_index = discover_arf_tag_index(
                    file_path, self.features, tag_directory=tag_directory,
                )
                attach_tags_to_spots(self.features, self.arf_tag_index)
                self.arf_class_index = discover_arf_class_index(file_path)
                attach_class_ids_to_spots(self.features, self.arf_class_index)
            return self.features

        # 別データセットへ切り替えるので、前データ由来の解析成果（差次・PCA・前処理行列・
        # 手動除外・EICプロット等）を open 前に一括破棄する。前回の結果を新データのものとして
        # 保存・解釈する取り違えを防ぐ。
        self.reset_analysis_state()

        with open(file_path, 'rb') as f:
            # 【修正点】ファイルの拡張子を見て正しいパーサーを呼び分ける
            file_ext = str(file_path).lower()
            if file_ext.endswith('.arf'):
                self.features = arf_reader.deserialize(io.BytesIO(f.read()))
                self.arf_tag_index = discover_arf_tag_index(
                    file_path, self.features, tag_directory=tag_directory,
                )
                attach_tags_to_spots(self.features, self.arf_tag_index)
                self.arf_class_index = discover_arf_class_index(file_path)
                attach_class_ids_to_spots(self.features, self.arf_class_index)
            else:
                self.features = deserialize(io.BytesIO(f.read())) # 元からインポートされている arf2_reader 用
                self.arf_tag_index = None
                self.arf_class_index = None

            self.current_file_path = file_path
            self.current_tag_directory = tag_directory
            # 新しいファイルを読み込んだら計算結果はリセット
            self.pca_result = None
            self.filtered_features = None

        return self.features

    def load_eic_data(self, file_path: str):
        """ファイルパスが前回と異なる場合のみEICデータを解析する"""
        if self.current_aef_file_path == file_path and self.eic_features is not None:
            return self.eic_features

        self.eic_features = parse_eic_aef_css1(file_path, include_chromatogram=False)
        self.current_aef_file_path = file_path
        return self.eic_features


# インスタンスを1つ作成（サーバー起動中に保持される）
session = AnalysisSession()
