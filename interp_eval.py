"""解釈品質評価の純粋ロジック（Ollama 非依存・unittest で検証）。

I/O オーケストレーションは interp_eval_run.py、ケース定義は interp_eval_cases.py。
"""
import httpx
import itertools
import os
import random
from collections import Counter
from dataclasses import dataclass, field

import phase_router

AXES = ["hallucination", "accuracy", "completeness", "utility", "language"]
PHASE_LABELS = ["PCA", "DIFFERENTIAL", "QC", "IDENTITY", "LITERATURE"]
MODES = ["NEG", "POS"]
MODEL_KEYS = ["qwen3_off", "qwen3_on", "qwen25_7b"]
INTERP_SYSTEM = (
    "あなたはMS-DIALリピドミクス解析アシスタントです。直前のツール結果だけを"
    "根拠に、日本語で簡潔に科学的解釈を述べてください。結果にない数値・主張を"
    "創作しないこと。")


@dataclass
class ToolStep:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class Case:
    id: str
    phase_label: str
    mode: str
    query: str
    pipeline: list  # list[ToolStep]（順に実行、最後の出力を解釈対象に凍結）


@dataclass
class FrozenCase:
    id: str
    phase_label: str
    mode: str
    query: str
    tool_name: str
    tool_args: dict
    tool_output: str


def validate_cases(cases, allowed_tools):
    """ケース集合の構造を検証し、エラー文字列のリストを返す（空＝妥当）。"""
    errors = []
    if len(cases) != 10:
        errors.append(f"ケース数は10であるべき（現在 {len(cases)}）。")
    ids = [c.id for c in cases]
    for dup in [i for i, n in Counter(ids).items() if n > 1]:
        errors.append(f"ID 重複: {dup}")
    present_phases = {c.phase_label for c in cases}
    for p in PHASE_LABELS:
        if p not in present_phases:
            errors.append(f"未カバーのフェーズ: {p}")
    for c in cases:
        if c.phase_label not in PHASE_LABELS:
            errors.append(f"未知の phase_label: {c.phase_label}（{c.id}）")
        if c.mode not in MODES:
            errors.append(f"未知の mode: {c.mode}（{c.id}）")
        if not c.pipeline:
            errors.append(f"pipeline が空: {c.id}")
        for step in c.pipeline:
            if step.name not in allowed_tools:
                errors.append(f"許可外ツール {step.name}（{c.id}）")
    return errors


def build_interp_messages(system_prompt, frozen):
    """凍結ケースから『ツール結果まで済んだ会話』を組み、続きに解釈だけ出させる。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": frozen.query},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": frozen.tool_name, "arguments": frozen.tool_args}}]},
        {"role": "tool", "content": frozen.tool_output, "tool_name": frozen.tool_name},
    ]


def make_pairs(model_keys):
    """全非順序ペアをソート済みタプルで返す。"""
    return [tuple(sorted(p)) for p in itertools.combinations(sorted(model_keys), 2)]


def build_blind_sheet(frozen_cases, interp, model_keys, seed):
    """ケース×モデルペアの盲検比較シートと復元用 answer_key を作る。"""
    rng = random.Random(seed)
    items = []
    answer_key = {}
    for fc in frozen_cases:
        for m1, m2 in make_pairs(model_keys):
            pair_key = f"{m1}__{m2}"
            if rng.random() < 0.5:
                a_model, b_model = m1, m2
            else:
                a_model, b_model = m2, m1
            items.append({
                "case_id": fc.id,
                "phase_label": fc.phase_label,
                "mode": fc.mode,
                "pair_key": pair_key,
                "a_text": interp[(fc.id, a_model)],
                "b_text": interp[(fc.id, b_model)],
            })
            answer_key[f"{fc.id}::{pair_key}"] = {"A": a_model, "B": b_model}
    return items, answer_key


@dataclass
class PairVerdict:
    case_id: str
    phase_label: str
    mode: str
    pair_key: str
    overall: str      # モデルキー or "tie"
    axes: dict        # axis -> モデルキー or "tie"


def _blank_counts(model_keys):
    return {m: {"win": 0, "loss": 0, "tie": 0} for m in model_keys}


def aggregate(verdicts, model_keys):
    """盲検採点（PairVerdict 群）を集計する。"""
    overall = _blank_counts(model_keys)
    by_axis = {ax: {m: 0 for m in model_keys} for ax in AXES}
    by_phase_axis = {}
    for v in verdicts:
        m1, m2 = v.pair_key.split("__")
        if v.overall == "tie":
            overall[m1]["tie"] += 1
            overall[m2]["tie"] += 1
        else:
            loser = m2 if v.overall == m1 else m1
            overall[v.overall]["win"] += 1
            overall[loser]["loss"] += 1
        for ax in AXES:
            winner = v.axes.get(ax, "tie")
            if winner != "tie":
                by_axis[ax][winner] += 1
                key = f"{v.phase_label}|{ax}"
                slot = by_phase_axis.setdefault(key, {m: 0 for m in model_keys})
                slot[winner] += 1
    return {"overall": overall, "by_axis": by_axis, "by_phase_axis": by_phase_axis}


def ollama_generate(messages, model, think, url=None, timeout=300):
    """解釈のみ（tools なし）で Ollama に問い合わせ、content を返す。"""
    url = url or phase_router.OLLAMA_URL
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {"temperature": 0},
    }
    resp = httpx.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["message"].get("content") or ""


def _openai_messages(messages):
    """Ollama 形式の会話を OpenAI Chat Completions で有効な role/content のみへ変換する。

    build_interp_messages は assistant.tool_calls（id/type なし）と tool ロールを使うが、
    OpenAI は tool_calls[].type / tool_call_id を必須とし拒否する（400）。同じ根拠を保った
    まま、tool 出力を user ターンへ畳み込む（tool_name も明記）。system/通常の user・
    assistant（content あり）はそのまま通す。ローカル腕とは会話符号化のみが異なり、
    モデルに与える証拠（クエリ＋ツール出力＋system）は同一。
    """
    out = []
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "assistant" and m.get("tool_calls") and not content:
            continue  # 空の tool_call 指示ターンは畳み込み先の tool 出力で表現する
        if role == "tool":
            name = m.get("tool_name") or m.get("name") or "tool"
            out.append({"role": "user",
                        "content": f"[ツール結果: {name}]\n{content}"})
        else:
            out.append({"role": role, "content": content})
    return out


def azure_generate(messages, deployment=None, temperature=0.0, timeout=300,
                   endpoint=None, api_key=None, api_version=None):
    """Azure OpenAI Chat Completions で解釈を生成する（ollama_generate と同形＝messages→text）。

    認証情報は引数優先・無ければ env（AZURE_OPENAI_ENDPOINT / API_KEY / DEPLOYMENT /
    API_VERSION）。新規SDK依存は入れず httpx で REST を叩く。build_interp_messages の
    system/user は role/content 形式で Azure もそのまま受理する。
    """
    endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
    deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    api_version = api_version or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not (endpoint and api_key and deployment):
        raise RuntimeError(
            "Azure 認証情報が未設定です（AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / "
            "AZURE_OPENAI_DEPLOYMENT を設定してください）。")
    base = endpoint.rstrip("/")
    body = {"messages": _openai_messages(messages), "temperature": temperature}
    if base.endswith("/openai/v1"):
        # Azure AI Foundry の OpenAI 互換 v1 サーフェス: deployment は body の model に載せ、
        # URL は {endpoint}/chat/completions（api-version クエリは不要）。
        url = f"{base}/chat/completions"
        body["model"] = deployment
    else:
        # 従来の Azure OpenAI: deployment を URL パスに、api-version をクエリに載せる。
        url = (f"{base}/openai/deployments/{deployment}"
               f"/chat/completions?api-version={api_version}")
    resp = httpx.post(url, headers={"api-key": api_key}, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"].get("content") or ""


def generate_interp(key, model, think, messages):
    """モデルキーに応じてクラウド/ローカルの生成腕へディスパッチする。

    azure キー（"azure_" 接頭）は azure_generate、それ以外は ollama_generate。
    orchestrator（interp_eval_run.do_generate）から使う継ぎ目をここに集約し単体検証可能にする。
    """
    if key.startswith("azure"):
        # deployment は env（AZURE_OPENAI_DEPLOYMENT）由来。MODELS の model 欄は表示用別名。
        return azure_generate(messages)
    return ollama_generate(messages, model=model, think=think)
