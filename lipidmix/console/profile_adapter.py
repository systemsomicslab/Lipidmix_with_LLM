"""MS-DIALバージョンごとのadapter能力宣言（spec §5.1, §6.1）。

このモジュールは依存グラフのleaf寄り（`lipidmix.console.method_file` と
`lipidmix.console.job_manager` の定数だけを再利用する）。`lipidmix.console.profiles`
（profile読込・依存解決・実行環境manifest構築）がここから import する側であり、
逆向きの依存（このモジュールが`profiles`をimportする）は循環になるので作らない。

## `adapter_capabilities(adapter_id)` の役割

`lcms-profile.v1`の`processing.dependencies[].kind`（`msp` / `text_identification` /
`rt_reference` / `lbm`）が、実際にどのMS-DIAL Console方法ファイルキー
（`method_key`）へ対応するかは、**MS-DIALのバージョンごとに変わりうる**。
このモジュールは「このadapter_idで実際に確認済みのキー」だけを allowlist
として返す——確認できていないキーを推測で登録しない（spec §5.1
「任意の文字列キーをパスと推測しない」「キー名を推測してMSPをLBM欄へ入れない」）。

登録が無い（未知の）`adapter_id`は`DomainError("PROFILE_ADAPTER_UNSUPPORTED", ...)`。
これは「このMS-DIALバージョン向けの対応が実装/検証されていない」ことを示す
機械可読な計画不足であり、キー名を当て推量して処理を通すことより安全。

## `adapter_id`の決め方

`lipidmix.console.profiles`が`profile["software"]["msdial_version"]`（例:
`"5.x"` `"5.5.241113"`）の先頭の整数（メジャーバージョン）から
`f"msdial{major}"`（例: `"msdial5"`）を作って渡す。パースできない
バージョン文字列は、どのみちここで未登録として`PROFILE_ADAPTER_UNSUPPORTED`
になる。

## `msdial5`（MS-DIAL 5.x Console）のキー証拠

ローカルclone `C:\\Users\\yuu18\\source\\repos\\MsdialWorkbench`
（コミット `afd5f9522fa2f11990e1ad51f88b22eef00a087c`、2026-09-08、
`origin/master`と同一）で確認した。実行体は`MSDIALCUI.exe`
（`tests/MSDIAL5/MsdialCoreTestApp`の成果物。`C:\\Users\\yuu18\\.claude\\kb\\
refs\\msdial-console-exe-build-paths.md`参照。`src/MSDIAL4/`の同名exeはMS-DIAL
**4**系の別物なので対象外）。

| kind | method_key | 証拠 |
|---|---|---|
| `msp` | `Msp file path` | `ParameterBase.MspFilePath`（`src/MSDIAL5/MsdialCore/Parameter/ParameterBase.cs:98,533`）。`ConfigParser.cs:742` `case "msp file path": param.MspFilePath = value;`。`Process/CommonProcess.cs:126-128`が`LibraryHandler.ReadMsLibrary(param.MspFilePath, ...)`で読み`DataBaseSource.Msp`として同定に使う。 |
| `lbm` | `Lbm file path` | `ParameterBase.LbmFilePath`（同ファイル:100,534）。`ConfigParser.cs:743` `case "lbm file path": param.LbmFilePath = value;`。`CommonProcess.cs:133-135`が`DataBaseSource.Lbm`として読む。既存`lipidmix/console/method_file.py`の`LBM_KEY`と同一（`docs/HISTRY.md` 2026-09-04(2)にGUI/Console差異の経緯あり）。 |
| `text_identification` | `Text db file path` | `ParameterBase.TextDBFilePath`（同ファイル:102,535）。`ConfigParser.cs:745` `case "text db file path": param.TextDBFilePath = value;`。`CommonProcess.cs:141-143`が`DataBaseSource.Text`として読む——msp/lbmと並ぶ「テキスト形式の同定用データベース」で、`kind="text_identification"`の実体として最も忠実な対応（`IsotopeTextDBFilePath`はアイソトープ追跡専用で同定用途ではないため不採用。`CompoundListInTargetModePath`＝"Compounds library file path for target detection"はターゲットモード**検出**専用で同定ではないため不採用）。 |
| `rt_reference` | `Compounds library file path for RT correction` | `ParameterBase.CompoundListForRtCorrectionPath`（同ファイル:108,538）。`ConfigParser.cs:748-749`。`Process/RetentionTimeCorrectionProcess.cs:120-127`が「RT correction anchor library」として読み、`Execute RT correction`が真なのに未設定/ファイル無しならConsole自身が`InvalidOperationException`/`FileNotFoundException`で停止する——RT補正の基準化合物リストという役割が`kind="rt_reference"`に直接対応する。 |

未対応のまま残したキー（推測で割り当てなかったもの）: `Isotope text DB file path`
（`IsotopeTextDBFilePath`。アイソトープ追跡専用）。
"""
from __future__ import annotations

import copy

from lipidmix.console import method_file as method_file_mod
from lipidmix.console.job_manager import _RAW_EXTENSIONS as _MSDIAL_RAW_EXTENSIONS
from lipidmix.core.atomic_io import DomainError

__all__ = ["adapter_capabilities"]

_UNSUPPORTED_CODE = "PROFILE_ADAPTER_UNSUPPORTED"

#: Task 6（per-injection evidence読込）向け: このadapterでは、注入ごとの
#: ピーク証拠が`.pai2`（個別測定。`lipidmix/pai2/`が既に読む）に載る
#: （CLAUDE.mdの対象形式表・`lipidmix/pai2/reader.py`参照）。値は
#: 「どの既存パーサ層を使うべきか」を指す識別子であり、まだ存在しない
#: 関数を指すcallableは作らない。
_LCMS_EVIDENCE_READER = "pai2"

_REGISTRY: dict[str, dict] = {
    "msdial5": {
        "adapter_id": "msdial5",
        "supported_software": (
            "MS-DIAL 5.x Console (MSDIALCUI.exe, tests/MSDIAL5/MsdialCoreTestApp)",
        ),
        "dependency_keys": {
            "msp": method_file_mod.MSP_KEY,
            "lbm": method_file_mod.LBM_KEY,
            "text_identification": method_file_mod.TEXT_DB_KEY,
            "rt_reference": method_file_mod.RT_REFERENCE_KEY,
        },
        # MS-DIALの`SupportMsRawDataExtension`と同じ集合（既存
        # `lipidmix.console.job_manager._RAW_EXTENSIONS`を再利用。二重管理しない）。
        "raw_formats": tuple(sorted(_MSDIAL_RAW_EXTENSIONS)),
        "evidence_reader": _LCMS_EVIDENCE_READER,
    },
}


def adapter_capabilities(adapter_id: str) -> dict:
    """`adapter_id`のadapter能力を返す（副作用なし・毎回新しいdictを返す）。

    未登録の`adapter_id`は`DomainError("PROFILE_ADAPTER_UNSUPPORTED", ...)`。
    確認できていないMS-DIALバージョンやキーを推測で通さないための、
    唯一のゲート（spec §5.1）。
    """
    entry = _REGISTRY.get(adapter_id)
    if entry is None:
        raise DomainError(
            _UNSUPPORTED_CODE,
            f"未登録のadapter_idです（対応未確認のMS-DIALバージョンの可能性があります）: "
            f"{adapter_id!r}",
            {"adapter_id": adapter_id, "known_adapter_ids": sorted(_REGISTRY)},
        )
    return copy.deepcopy(entry)
