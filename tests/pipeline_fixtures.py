"""pipeline関連テストの合成fixture置き場。

実rawや既存成果物をfixtureにしない（CLAUDE.md方針）。ここは純粋なdictビルダーの集合で、
テストごとに独自の形を作らせないための唯一の正準とする。Task 3・6・9・13・19がここへ
helperを足していく前提なので、既存helperの必須フィールドは減らさない。
"""
from __future__ import annotations

import sys
from pathlib import Path


def make_source(root: Path) -> dict:
    """Task 13 (入力隔離・メソッド選択) 用の合成生データフォルダを作る。

    root直下にS0..S7.wiff・GUI自動保存形式のlab_param_202609050001.txt
    （Ion mode/Target omics/Lbm file path）・fake.lbm2・fake.exeを作り、
    それぞれのPathを返す。fake.exeは実行しない（中身は placeholder テキスト）
    ——単体テストではlipidmix.console.runner.is_console_exeを差し替えて使う。
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (root / f"S{i}.wiff").write_text(f"raw-{i}", encoding="ascii")

    lbm = root / "fake.lbm2"
    lbm.write_bytes(b"fake-lbm-library")

    method = root / "lab_param_202609050001.txt"
    method.write_text(
        "Ion mode: negative\n"
        "Target omics: Lipidomics\n"
        "Lbm file path: fake.lbm2\n",
        encoding="ascii", newline="\n")

    exe = root / "fake.exe"
    exe.write_text("fake console executable placeholder", encoding="ascii")

    return {"root": root, "method": method, "lbm": lbm, "exe": exe}


def execution_record(**overrides):
    """console-execution.v1 の妥当な最小レコードを返す。

    overridesで一部フィールドを壊し、validate_execution_recordの拒否条件を
    テストするために使う。
    """
    record = {
        "schema": "console-execution.v1", "execution_id": "exec-1", "job_id": "job-1",
        "started_at": "2026-09-05T00:00:00Z", "ended_at": "2026-09-05T00:00:01Z",
        "pid": 1234, "process_identity": {"pid": 1234, "creation_time": 100},
        "command_sha256": "a" * 64, "method_sha256": "b" * 64, "exe_sha256": "c" * 64,
        "exit_code": 0, "termination": "exited", "timeout_s": 21600,
        "collection": {"status": "succeeded"}, "validation": {"status": "pending"},
    }
    record.update(overrides)
    return record


# ---------- write_mztab ----------

# 少なくとも3特徴、識別済み(with_inchikey時)・InChIKeyを想定した合成値。
# 実データの脂質名文法に寄せる必要はない（構造検証のための合成値のため）。
_FEATURES = (
    ("PC 34:1", "IPCSVZSSVZVIGE-UHFFFAOYSA-N", 760.5851, 620.0),
    ("PE 36:2", "XKMRRTOUMJRJIA-UHFFFAOYSA-N", 742.5395, 590.0),
    ("TG 52:3", "DGGXCMYPQAOAJC-UHFFFAOYSA-N", 878.7789, 910.0),
)


def write_mztab(path: Path, sources: list[Path], *, with_inchikey: bool = True) -> Path:
    """実際のparse_mztab/validate_mztabに通る合成mzTab-M 2.0ファイルを書く。

    sources の各要素を ms_run[N] として file URI 化し、assay[N]-ms_run_ref で
    結ぶ（1 source = 1 assay）。tests/test_mztab_tools.py の SFH 構文（SFH ヘッダ
    行 + SMF データ行）を再利用し、少なくとも3特徴・SML・SMEを含む。文字列
    "x" のような偽物ではなく、実物の構造検証・定量行列抽出が通る内容にする。

    Parameters
    ----------
    with_inchikey:
        False なら database_identifier を全行 "null"（未識別）にする。
    """
    path = Path(path)
    n = len(sources)
    if n < 1:
        raise ValueError("sources には少なくとも1件のPathが必要です")

    lines = [
        "MTD\tmzTab-version\t2.0.0-M",
        "MTD\tmzTab-mode\tComplete",
        "MTD\tmzTab-type\tQuantification",
        "MTD\tsoftware[1]\t[MS, MS:1003082, MS-DIAL, Msdial console 5.5.241113]",
        "MTD\tquantification_method\t[MS, MS:1001829, label-free raw feature quantitation, ]",
        "MTD\tsmall_molecule-quantification_unit\t[PRIDE, PRIDE:0000429, Abundance, ]",
    ]
    for i, src in enumerate(sources, start=1):
        uri = Path(src).resolve().as_uri()
        lines.append(f"MTD\tms_run[{i}]-location\t{uri}")
        lines.append(f"MTD\tassay[{i}]-ms_run_ref\tms_run[{i}]")
        lines.append(f"MTD\tassay[{i}]\tS{i}")

    abundance_cols = "\t".join(f"abundance_assay[{i}]" for i in range(1, n + 1))
    lines.append(
        "SFH\tSMF_ID\tSML_ID_REFS\tdatabase_identifier\tchemical_name\tsmiles\tinchi\t"
        f"exp_mass_to_charge\tretention_time_in_seconds\t{abundance_cols}"
    )
    for idx, (name, inchikey, mz, rt) in enumerate(_FEATURES, start=1):
        identifier = inchikey if with_inchikey else "null"
        abundances = "\t".join(f"{1000.0 * idx + 10.0 * j:.1f}" for j in range(1, n + 1))
        lines.append(
            f"SMF\t{idx}\tSML:{idx}\t{identifier}\t{name}\tnull\tnull\t{mz}\t{rt}\t{abundances}"
        )

    # アダクトは正極性1・負極性1・不明1件にして多数決を意図的に引き分け(None)に
    # する。極性を断定すると、write_mztab を負極性jobで使うテストと衝突する
    # （validate_outputsの極性多数決チェックが致命的不一致として弾いてしまう）。
    _sml_adducts = ("[M+H]1+", "[M-H]1-", "null")
    lines.append("SMH\tSML_ID\tSMF_ID_REFS\tdatabase_identifier\tadduct_ions")
    for idx, adduct in enumerate(_sml_adducts[: len(_FEATURES)], start=1):
        lines.append(f"SML\t{idx}\t{idx}\tnull\t{adduct}")

    lines.append("SEH\tSME_ID\tSMF_ID_REFS\tdatabase_identifier")
    for idx in range(1, len(_FEATURES) + 1):
        lines.append(f"SME\t{idx}\tSMF:{idx}\tnull")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------- 実プロセスとして走る偽 Console ----------
# supervise は「本当に起動したプロセスが本当にどう終わったか」を証跡にする。
# subprocess をモックで置き換えると、監視・停止・収集のどれが壊れても緑のままに
# なるため、実行系のテストは実際に子プロセスを起こす。tests/fixtures/fake_console.py
# は決まったシナリオ用（import せずスクリプトパスとして渡す）で、こちらは
# 「任意のファイルを書いて任意の終了コードで終わる」だけの汎用版。

def fake_console_command(files: dict | None = None, *, exit_code: int = 0,
                         sleep_s: float = 0.0) -> list[str]:
    """指定のファイルを書き、必要なら待ってから、指定の終了コードで終わるコマンド。"""
    payload = [(str(path), text) for path, text in
               sorted((files or {}).items(), key=lambda kv: str(kv[0]))]
    script = (
        "import pathlib, sys, time\n"
        f"for path, text in {payload!r}:\n"
        "    p = pathlib.Path(path)\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_text(text, encoding='utf-8')\n"
        f"time.sleep({sleep_s!r})\n"
        f"sys.exit({exit_code!r})\n"
    )
    return [sys.executable, "-c", script]


def use_fake_console(monkeypatch, command: list[str]) -> None:
    """Console のコマンドライン組み立てだけを偽 Console へ差し替える。

    差し替えるのは「何を起動するか」だけで、起動・監視・収集・完了判定は本物を
    通る。`supervise` の `command` 注入は MCP の公開引数にできない（任意コマンドの
    実行口になる）ので、テストからは唯一の組み立て場所である build_msdial_cmd を
    差し替える。
    """
    monkeypatch.setattr("lipidmix.console.runner.build_msdial_cmd",
                        lambda *args, **kwargs: command)


def mztab_text(tmp_path: Path, sources) -> str:
    """構造検証と定量抽出を通る合成 mzTab-M の中身を返す。

    完了ゲートは「拡張子が .mzTab である」ことではなく、主 mzTab が一意に選べ・
    構造が妥当で・定量行列に有限値があり・予定した入力が全て assay に対応して
    いることを要求する。偽 Console にはこの中身を書かせる。
    """
    scratch = Path(tmp_path) / "_mztab_template"
    text = write_mztab(scratch, list(sources)).read_text(encoding="utf-8")
    scratch.unlink()
    return text


# ---------- DatasetState ----------

def make_dataset():
    """解析サービスのテスト用に、8 検体 × 6 特徴の DatasetState を作る。

    実 mzTab を読まずに済ませる（実データ・既存成果物を fixture にしない方針）。
    値は固定 seed の一様乱数で、前処理・PCA・2 群比較が実際に走る規模にしてある。
    """
    import numpy as np

    from lipidmix.mztab.dataset_state import DatasetState
    ds = DatasetState()
    ds.feature_matrix = np.random.default_rng(7).uniform(100, 200, (6, 8))
    ds.sample_names = [f"S{i}" for i in range(8)]
    ds.sample_assay_ids = [f"assay[{i + 1}]" for i in range(8)]
    ds.feature_ids = [f"F{i}" for i in range(6)]
    ds.quantification_measure = "peak_height"
    ds.validation_result = {"ok": True, "errors": [], "warnings": []}
    ds.feature_metadata = {f"F{i}": {"name": f"Lipid {i}", "mz": 500 + i,
                                     "rt": 2.0,
                                     "inchikey": "IPCSVZSSVZVIGE-UHFFFAOYSA-N",
                                     "inchikey_source": "database_identifier"}
                           for i in range(6)}
    return ds


# ---------- sample-manifest.v1 の合成行（Task 9 apply_metadata の契約） ----------

def metadata_rows(ds, *, n_qc=4, confirmed=True):
    """make_dataset()の8検体へ、resolve_metadata出力と同じ形の行を合成する。

    n_qcで健全なQC数（0〜4件）を振り、conservative-v1のQC閾値（Task 11）を
    跨いだ挙動をテストできるようにする。confirmed=Falseはbatch/injection_order/
    qc_poolだけをmzTab由来unverifiedへ落とし、他タスクが「出所で挙動が変わる」
    経路を確認するための契約。
    """
    assert len(ds.sample_names) == 8 and 0 <= n_qc <= 4
    qc_orders = [1, 3, 6, 8][:n_qc]
    orders = qc_orders + [i for i in range(1, 9) if i not in qc_orders]
    rows = []
    for i, name in enumerate(ds.sample_names):
        row = dict(sample_id=name, source_file=f"{name}.wiff",
                   role="qc" if i < n_qc else "sample", group=None,
                   batch="B1", injection_order=orders[i],
                   qc_pool="pool1" if i < n_qc else None, include=True)
        row["provenance"] = {
            key: {"value": value, "source": "user_manifest", "confidence": "confirmed"}
            for key, value in row.items()
        }
        if not confirmed:
            for key in ("batch", "injection_order", "qc_pool"):
                row["provenance"][key].update(source="mztab", confidence="unverified")
        row["conflicts"] = []
        rows.append(row)
    return rows
