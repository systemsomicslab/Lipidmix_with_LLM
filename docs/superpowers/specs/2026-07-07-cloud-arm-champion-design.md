# クラウド腕（Azure gpt-4o-mini）チャンピオン対決 設計

- 日付: 2026-07-07
- 背景: `docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md` のステップ5「ハイブリッドの分界点を実測で確定」。ローカル解釈担当は qwen3:14b(think ON) が最良（総合18-2）。Task①（差次スリム化）で差次はローカル救済され、クラウド送りの第一候補から外れた。残る候補は **PCA の生物解釈・同定の過剰主張**。クラウド腕を「実際にエスカレートする先」＝Azure OpenAI gpt-4o-mini で実測し、どのフェーズでクラウドがローカル最良を上回るか（＝分界点）を確定する。

## 目的

評価ハーネス `interp_eval` にクラウド生成腕を追加し、**Azure gpt-4o-mini vs qwen3:14b(think ON)** のチャンピオン対決を全10ケースで盲検比較（10ペア）する。クラウドがチャンピオンに勝つフェーズを分界点として特定する。

## スコープ

- 対象: `interp_eval.py`（`azure_generate` 追加）、`interp_eval_run.py`（生成ディスパッチ＋チャンピオン用シート）。
- 測定スコープ: **チャンピオン対決のみ**（Azure vs qwen3_on の2本、10ケース＝10ペア）。ローカル3本総当たり（60ペア）はローカル間が既測のため採らない（YAGNI）。qwen3_on の既存解釈（`interp_eval_out/interp/*__qwen3_on.txt`）を再利用し、Azure解釈10件のみ新規生成。
- 非対象: ライブ agent_core への chat_fn 切替（将来。今回は測定のみ）。差次・QC 等ツール側は不変。

## 認証情報ゲート

**現在 Azure 認証情報は未設定**（`AZURE_OPENAI_*` env なし）。本設計では**配管の実装＋TDD（httpxモック）まで**を行い、**実測ランは認証情報が入るまで保留**する。実行手順は下記に明記する。

## 設計

### クラウド腕 `azure_generate`
`ollama_generate(messages, model, think) → text` と同じ「messages→text」形で `interp_eval.py` に追加。新規SDK依存を入れず既存 `httpx` で Azure OpenAI Chat Completions REST を叩く。

```python
def azure_generate(messages, deployment=None, temperature=0.0, timeout=300,
                   endpoint=None, api_key=None, api_version=None):
    endpoint   = endpoint   or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key    = api_key    or os.environ.get("AZURE_OPENAI_API_KEY")
    deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    api_version= api_version or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not (endpoint and api_key and deployment):
        raise RuntimeError("Azure 認証情報が未設定です（AZURE_OPENAI_ENDPOINT / API_KEY / DEPLOYMENT）。")
    url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    resp = httpx.post(url, headers={"api-key": api_key},
                      json={"messages": messages, "temperature": temperature}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""
```

- `build_interp_messages` の system/user は role/content 形式で Azure もそのまま受理（変換不要）。
- `think` は Ollama 固有のため azure 腕は取らない。
- temperature=0 でローカルと条件を揃える。

### ハーネス統合
- `interp_eval_run.MODELS` に4本目 `("azure_4omini", "gpt-4o-mini", None)` を追加（第2要素はログ表示用の別名。実際の deployment は env）。
- `do_generate` で `key == "azure_4omini"` のとき `ie.azure_generate(msgs)` に、それ以外は従来 `ie.ollama_generate(msgs, model, think)` にディスパッチ。
- **チャンピオン用シート**: `do_sheet` に対戦モデル対を渡せるようにし、`model_keys=["qwen3_on", "azure_4omini"]` で10ペアを生成（`build_blind_sheet` は既に model_keys 引数を取る）。answer_key / aggregate は既存経路を再利用。
- 盲検・固定シードは踏襲。

## テスト（TDD・httpxモック）
`tests/test_interp_eval_cloud.py`（新規）:
1. **URL/ヘッダ/body 構築**: `httpx.post` をモックし、azure_generate が正しい URL（endpoint+deployment+api-version）・`api-key` ヘッダ・`{"messages","temperature":0}` body で呼ぶこと。
2. **応答パース**: モック応答 `{"choices":[{"message":{"content":"解釈X"}}]}` から `"解釈X"` を返すこと。
3. **認証未設定エラー**: env 未設定・引数なしで `RuntimeError`（実API を叩かない）。
4. **ディスパッチ**: `do_generate` 相当で azure キーが `azure_generate` を選ぶこと（`ollama_generate` を叩かない）。

実APIは一切叩かない。

## 実測ラン手順（認証情報が入ってから）
```
setx AZURE_OPENAI_ENDPOINT   https://<resource>.openai.azure.com
setx AZURE_OPENAI_API_KEY    <key>
setx AZURE_OPENAI_DEPLOYMENT <gpt-4o-mini deployment 名>
# 生成（azure 10件のみ）→ チャンピオン用シート（10ペア）→ 審判 → 集計
python interp_eval_run.py generate   # azure 腕を含む
python interp_eval_run.py sheet --champion
# blind_sheet を審判（Claude Code セッション）が採点 → verdicts.json
python interp_eval_run.py aggregate
```

## 非目標（YAGNI）
- OpenAI SDK / openai パッケージ導入はしない（httpx REST で足りる）。
- ローカル3本×Azure の総当たり（60ペア）はしない。
- ライブ agent_core の chat_fn 切替は今回やらない（測定で分界点が出てから）。
