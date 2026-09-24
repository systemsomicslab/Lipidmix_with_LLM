"""全テスト共通の隔離。

参照ライブラリの環境変数（`MSDIAL_MSP_POS` / `MSDIAL_MSP_NEG`）は利用者が OS や
`.mcp.json` に常設する前提の設定なので、テストを走らせる機械にも設定されている。
残したままだと `resolve_library_path()` がテストの tmp ではなく実ライブラリを掴み、
結果が機械ごとに変わる（しかも実ライブラリは外部流出禁止の資産）。
環境変数を使うテストは monkeypatch.setenv で明示的に置く。
"""
import pytest

from lipidmix.core.path_resolvers import LIBRARY_ENV_VARS


@pytest.fixture(autouse=True)
def _isolate_library_env(monkeypatch):
    for name in LIBRARY_ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)
