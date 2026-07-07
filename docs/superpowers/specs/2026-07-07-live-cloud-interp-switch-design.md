# ライブ agent_core クラウド解釈切替 設計（A）

- 日付: 2026-07-07
- 位置づけ: ローカルLLM移行の残タスク分解のうち **A（ライブ切替）**。順序は C（構造化スリム化・完了）→ **A**。
- 先行実測: `docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md` ステップ6（チャンピオン対決）。**Azure gpt-5.4-mini が qwen3:14b(think ON) に 7-0**。分界点＝**PCA 生物解釈・QC 物語化・文献関連度統合はクラウドが明確に優**、**差次（slim後）と同定はローカルで互角**。
- 継ぎ目: 既存 `interp_eval.azure_generate`（messages→text）／`_openai_messages`（Ollama形式の tool ターンを OpenAI 有効形へ畳む）を**解釈専用の腕**としてライブへ差し込む。

## 目的

ライブの `agent_core.run_turn` を **2段ターン**にする。ローカル qwen3:14b が route＋ツールループを回し、**高価値ツールを実行したターンのみ**、蓄積したツール出力からクラウド（Azure）で最終解釈を別途生成して返す。非高価値・認証情報なし・クラウド失敗時はローカル最終解釈へ縮退する。実測(7-0)の分界点をライブ挙動として実装に落とす。

## 背景（2段構成の必然性）

Ollama チャットループでは「tool 呼び出し終了」の合図と「最終散文」が同一レスポンス（`tool_calls` 空＋`content`）に載る。よって**ローカル最終散文の生成自体は必ず発生する**（＝ループ終了シグナル）。高価値ターンではこれをクラウド出力で**上書き**し、同時に**クラウド失敗時のフォールバック**として再利用する。避けられない1回のローカル生成を保険に転用する設計。

## スコープ

- 対象:
  - `agent_core.py`: `Agent` に `interp_fn` を DI 追加、`run_turn` ループ終端の最小改修、純述語 `should_escalate` とツール tier 定数を追加。
  - `agent_repl.py`: `.env` から `AZURE_` 取り込み、creds/トグル ゲートで `interp_fn` を組み立て注入、腕の可視化。
  - `interp_eval.py`: 7-0 を出した解釈用 system プロンプト定数を共有可能な位置へ（両所から import）。
- 非対象:
  - ルーティング（`phase_router`）・ツール本体・`_truncate(8000)` backstop は不変。
  - `azure_generate`／`_openai_messages`／`ollama_generate` の実装は不変（そのまま再利用）。
  - 評価ハーネス（`interp_eval_run.py`）の測定経路は不変。
  - 人手校正（7-0 審判の妥当性検証）はコード外の別立て手動活動。
  - tiered payload（腕別の詳細度差）は扱わない（C 同様 YAGNI。クラウドは大文脈で現行 payload をそのまま食える）。

## 設計

### データフロー（2段ターン）

1. **1段目（不変）**: `route()` で1フェーズ固定 → 有界ツールループ。各ラウンドの `tool_calls` を実行し、出力を `_truncate(8000)` して `conversation` に蓄積。**このターンで実行したツール名を集合 `executed` に積む**（ターン毎に空から）。
2. **2段目（新規）**: `tool_calls` が空になった時点（＝最終解釈フェーズ到達）で:
   - ローカル最終散文 `local_content = msg["content"]` を手元に確保。
   - `interp_fn` が注入されており かつ `should_escalate(executed)` が真なら、**今ターンの証拠**をクラウドへ渡し最終解釈を生成。成功（非空）ならそれを会話に積んで返す（`last_arm="cloud"`）。
   - それ以外（非高価値／`interp_fn` 無し／クラウド例外／空応答）は `local_content` を積んで返す（`last_arm="local"`）。

### エスカレーション判定（純述語）

分界点(7-0)を**実行ツール集合**へ忠実に写像する。純関数で単体検証可能。

```python
# agent_core.py（モジュール定数）
CLOUD_TIER_TOOLS = {"arf_re_pca", "arf_pca_preprocessed", "arf_preprocess", "paper_search"}
LOCAL_VETO_TOOLS = {"arf_differential"}   # 差次=slim後ローカルで互角。arf_preprocessと同居するため veto

def should_escalate(executed: set[str]) -> bool:
    """このターンで実行したツール集合が高価値(クラウド送り)かを判定する純述語。"""
    return bool(executed & CLOUD_TIER_TOOLS) and not (executed & LOCAL_VETO_TOOLS)
```

判定表（実データ10ケースの各フェーズが正しく分岐する）:

| ケース | 実行ツール集合 | cloud∩ | veto∩ | 判定 |
|---|---|---|---|---|
| PCA | `{load_dataset, arf_re_pca}` | ✓ | — | **cloud** |
| QC | `{load_dataset, arf_preprocess}` | ✓(preprocess) | — | **cloud** |
| LITERATURE | `{paper_search}` | ✓ | — | **cloud** |
| DIFFERENTIAL | `{load_dataset, arf_preprocess, arf_differential}` | ✓(preprocess) | ✓ | **local** |
| IDENTITY | `{load_dataset, arf2_annotate_identities}` | — | — | **local** |

非対称の根拠を明示: **identity は cloud-tier に無いので自然にローカル**、**differential だけ cloud-tier の `arf_preprocess` と同居するため明示 veto** が要る。差次は slim 化(タスクC先行)でローカル救済済＝互角なので降格が正しい。

### クラウド継ぎ目（`run_turn` ループ終端）

```python
if not calls:
    local_content = msg.get("content") or ""
    if self.interp_fn is not None and should_escalate(executed):
        try:
            cloud = self.interp_fn([{"role": "system", "content": INTERP_SYSTEM}]
                                   + conversation[turn_start:])
            if cloud.strip():
                conversation.append({"role": "assistant", "content": cloud})
                state.last_arm = "cloud"
                return cloud
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
            pass  # クラウド失敗 → ローカル最終散文へフォールバック
    conversation.append({"role": "assistant", "content": local_content})
    state.last_arm = "local"
    return local_content
```

- **`INTERP_SYSTEM`**: 7-0 を出した `interp_eval_run.SYSTEM`（「直前のツール結果だけを根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を創作しないこと。」）を**逐語で共有**する。tool 指向の `DEFAULT_SYSTEM` は使わない（勝ち筋の再現）。定数は `interp_eval.py` に置き `agent_core`／`interp_eval_run` の両所から import する（重複定義を避ける）。
- **会話スライス `conversation[turn_start:]`**: マルチターン履歴の全部でなく**今ターンの証拠**（直近 user 以降の tool 出力群）のみをクラウドへ渡す。eval の `build_interp_messages`（単一ターン構造）に忠実で、トークンも節約。`turn_start` は `conversation.append({"role":"user",...})` 直後の `len(conversation)` を run_turn 冒頭で記録。
- **符号化**: `interp_fn` の実体は `azure_generate` を薄くラップするだけ。`azure_generate` が内部で `_openai_messages` を通し、Ollama 形式 assistant(tool_calls)/tool ターンを OpenAI 有効形（空 tool_call 指示ターンは畳み、tool 出力は `[ツール結果: name]` 付き user へ）に変換する（既存・検証済経路）。
- **フォールバックの安全性**: `local_content` はループ終端で必ず手元にあるため、クラウド例外・空応答時に無条件で退避できる。捕捉例外は transport/parse 系（`httpx.HTTPError, RuntimeError, KeyError, ValueError`）に限定し、フォールバック先が常に存在する。

### state の拡張（観測用）

`RouterState` に `last_arm: str | None = None`（`"local"` / `"cloud"`）を追加。route は書き換えないという不変条件は保つ（`last_arm` は run_turn が最終応答時に書く別フィールド）。REPL 表示 `[{phase}·{last_arm}]` でどちらの腕が答えたかを可視化する。

### 配線（`agent_repl.py`）— creds/トグルはここに閉じる

agent_core を純粋（Ollama/Azure 非依存・env 非依存）に保つため、認証情報とトグルの判定は REPL 側に置く。

```python
# .env からは AZURE_ のみ取り込む（interp_eval_run.py と同じ根拠＝LIPIDMIX_* は
# テンプレのダミーで server import を壊すため除外。既存 os.environ は上書きしない）。
def _build_interp_fn():
    if os.environ.get("LIPIDMIX_CLOUD_INTERP", "1") == "0":
        return None                       # 明示 kill-switch
    if not (os.environ.get("AZURE_OPENAI_ENDPOINT")
            and os.environ.get("AZURE_OPENAI_API_KEY")
            and os.environ.get("AZURE_OPENAI_DEPLOYMENT")):
        return None                       # creds なし → 完全ローカル縮退
    return lambda messages: interp_eval.azure_generate(messages)
```

- 既定挙動: `AZURE_OPENAI_*` が揃い かつ `LIPIDMIX_CLOUD_INTERP != "0"` → 高価値ターンを自動でクラウド送り。creds 無し → 従来通り全ローカル。
- `Agent(tool_schemas=..., chat_fn=ollama_chat, execute_fn=execute_tool, classify_fn=safe_classify, interp_fn=_build_interp_fn())`。

## テスト（TDD）

`tests/test_agent_core.py`（既存）を拡張。全て Ollama/Azure 非依存（フェイク注入）。

1. **`should_escalate` 判定表**: 上表5行＋端（PCA と differential 同居 `{arf_re_pca, arf_differential}` → veto で False、cloud-tier 空 `{load_dataset}` → False）を純関数で検証。
2. **クラウド送り**: フェイク `chat_fn` が「PCA ツール1回 → tool_calls 空＋ローカル散文」を返す会話を駆動。フェイク `interp_fn` が哨兵文字列を返す。`run_turn` が哨兵を返し、会話末尾に積み、`state.last_arm == "cloud"` を確認。`interp_fn` が受け取った messages 先頭が `INTERP_SYSTEM`・今ターンスライスのみ（前ターン混入なし）であることを確認。
3. **veto でローカル**: フェイク会話が `arf_preprocess` 後に `arf_differential` を実行 → `interp_fn` を**呼ばず**ローカル散文を返し `last_arm == "local"`。
4. **フォールバック**: `interp_fn` が `httpx.HTTPError` を送出 → ローカル散文へ退避、`last_arm == "local"`、会話にローカル散文が積まれる。
5. **空クラウド応答**: `interp_fn` が `""`（空）を返す → ローカル散文へ退避。
6. **既定＝純ローカル回帰**: `interp_fn=None`（既定）で既存挙動が不変（高価値ツールでもローカル、`last_arm=="local"`）。既存の run_turn テストが緑のまま。
7. **配線ゲート** `tests/test_agent_repl.py`（新規・軽量）: `_build_interp_fn` が (a) `LIPIDMIX_CLOUD_INTERP=0` で None、(b) creds 欠落で None、(c) creds 揃い＋トグル既定で callable を返す（`monkeypatch` で env 操作、実 API は叩かない）。

## 実装後の検証

- 全体スイート緑（既存 296 + 追加）。
- 任意（creds 投入後の実走）: `agent_repl.py` を起動し PCA/QC/文献クエリで `[PHASE·cloud]`、差次/同定クエリで `[PHASE·local]` が出ること、creds を外すと全 `local` になることを目視確認。実 API 呼び出しはこの手動確認のみ（自動テストは叩かない）。

## 非目標（YAGNI）

- ルーティング（フェーズ判定）のクラウド化はしない（route は状態ゲート＋キーワード＋ローカル分類で足りると既測）。
- tiered payload（ローカル/クラウドで詳細度を変える）は作らない。クラウドは大文脈で現行 payload を食える。
- ストリーミング／非同期化はしない（現行同期 REPL のまま）。
- クラウド最終解釈の再ツール実行（cloud にツールを持たせる）はしない。cloud は解釈専用（messages→text）。
- 複数クラウドプロバイダ抽象化はしない（Azure 1本＝`azure_generate`）。

## 付随更新

- `docs/HISTRY.md`（ローカル開発ログ・gitignore）へ A 実装ログを追記。
- メモリ `local-llm-architecture.md` を A 完了で更新（ライブ切替が接続済＝移行の主目的達成）。
