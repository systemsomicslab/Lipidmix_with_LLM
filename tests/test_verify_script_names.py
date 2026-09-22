"""検証 CLI（`scripts/verify_spectral_match.py`）の脂質名の正規化。

実データが要る CLI 本体は pytest に入れないが、**名前の同一視は純関数**なので
ここで縛る。この規則を落とすと `EtherLPE` の 5 件が「移植の誤り」に化けて見える
（HISTRY 2026-09-22(5)）。
"""
import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "verify_spectral_match.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_spectral_match", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify():
    return _load()


def test_the_original_name_is_always_a_variant(verify):
    assert "PC 18:0_22:6" in verify.ether_name_variants("PC 18:0_22:6")


def test_plasmanyl_and_plasmenyl_shorthand_are_the_same_species(verify):
    """`O-n:m` と `P-n:(m-1)` は同一化学種の別表記。MS-DIAL は `P-` のレコードを
    採点して `O-` 表記で書き出すので、名前だけで引くと別レコードに当たる。"""
    assert "LPE P-16:0" in verify.ether_name_variants("LPE O-16:1")
    assert "LPE P-18:2" in verify.ether_name_variants("LPE O-18:3")
    assert "LPE O-18:1" in verify.ether_name_variants("LPE P-18:0")


def test_only_the_ether_chain_is_rewritten(verify):
    """鎖が複数あるときは O-/P- が付いた鎖だけ書き換える。"""
    assert "PE P-16:1_18:1" in verify.ether_name_variants("PE O-16:2_18:1")


def test_a_zero_double_bond_alkyl_chain_has_no_plasmenyl_counterpart(verify):
    """`O-16:0` の相方は `P-16:-1` になってしまうので作らない。"""
    variants = verify.ether_name_variants("PE O-16:0_18:1")
    assert not any("-1" in v.split("_")[0] for v in variants if v.startswith("PE P-"))


def test_a_non_ether_name_yields_only_itself(verify):
    assert verify.ether_name_variants("PI 18:0_20:4") == {"PI 18:0_20:4"}
