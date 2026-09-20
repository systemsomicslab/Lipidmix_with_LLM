"""参照ライブラリ照合の既定許容幅（stdlib のみの leaf）。

`MsRefSearchParameterBase`（`docs/schema/molecule_ms_reference.md`）の既定値。
store の `search_params` が無い（`.msp` 由来など）ときのフォールバックとして、
`lipidmix.library.tools`（MCP 面）と `lipidmix.msdial.peak_verification`
（`verify_peak_annotation` の判断材料）の**両方**がここから読む。

以前は 2 箇所に同じ 3 つのリテラルを複写していた。上流の既定が変わったとき、
MCP に面していて目に付きやすい `library/tools.py` だけが更新され、
`peak_verification.py` 側がテストに気付かれず古くなる恐れがあったため、
値の置き場をここ 1 箇所に一本化した（Task 11 レビュー Important 1）。

`library.store`（sqlite3 / msgpack / reader 群に依存）には置かない——定数 3 つの
ためだけに `peak_verification.py`（下位レイヤ）へその依存を引き込むのは割に合わない。
"""
from __future__ import annotations

DEFAULT_MZ_TOL = 0.01
DEFAULT_MS2_TOL = 0.025
DEFAULT_RT_TOL = 0.2
