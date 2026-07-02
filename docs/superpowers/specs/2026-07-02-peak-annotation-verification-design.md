# ピークアノテーション生化学的検証ツール 設計

- 日付: 2026-07-02
- 対象: MS-DIAL 解析 MCP サーバ（`server.py`）への新ツール追加
- ステータス: 設計確定（実装計画へ移行予定）

## 1. 背景と目的

PCA のスコアプロットで着目したピークや、loadings で寄与の大きいピークについて、
その **アノテーションが生化学的に本当に正しいか** を検証したい。「正しさ」は
2 軸で捉える:

1. **分析化学的な同定確度**: 測定事実（m/z・アダクト・分子式・極性）がその脂質
   構造を支持するか。
2. **生物学的妥当性**: その脂質が試料系（生物種/組織/実験系）に存在してよいか、
   既知の内因性種か、P-/O- 表記などの落とし穴に当たらないか。

本ツールはこの 2 軸を **統合** し、決定的に計算できる部分（質量誤差・アダクト整合）は
ツールがスコア帯付きで確定し、生物学的妥当性の最終判定は関連 knowledge ノートを
添えて LLM に委ねる（**ハイブリッド**）。既存の設計思想「サーバ=機械的な事実集約 +
knowledge ポインタ / LLM=解釈・判断」（`knowledge_store.py` 冒頭コメント）に整合する。

## 2. スコープ

### v1 に含める
- 新ツール `verify_peak_annotation(metabolite_id | metabolite_name)`。
- 入口は **ID/名称による単一ピック検証**（`inspect_metabolite_details` と同じ照合）。
- 対象は現在ロード中の **pai2 データ**（`session.filtered_features`）。pai2 の feature は
  formula / adduct / ontology / ion_mode / m-z を既に保持する。
- 分析化学チェック: **精密質量誤差 (ppm)** と **アダクト/イオンモード整合**。
- 生物学的妥当性の **判断材料の組み立て**（判定は LLM）。

### v1 に含めない（follow-up）
- **ARF 経路対応**: ARF の spot 辞書（`test_arf.py: _convert_arf_peak_group`）は現状
  `RT/MassCenter/IonMode/Name/HeightAverage` のみで Adduct/Formula/Ontology を持たない。
  対応には representative 行から Adduct/Formula/Ontology を surface する抽出拡張
  （msgpack スキーマ索引の特定）が必要。別タスク。
- **MS/MS 断片チェック (.dcl)**: クラス別断片ルールライブラリ + `.dcl` パース。別タスク。
  （MS/MS 実断片は `.pai2` 単体には無く別ファイル `.dcl` に格納される。）
- **分子式↔アノテーション整合**: 脂質ショートハンド名（鎖長:二重結合数）からの元素組成
  予測と分子式の突合。別タスク。
- **外部 DB 照合**（LIPID MAPS 等）。将来候補（ネットワーク依存のため v1 では避ける）。

## 3. アーキテクチャ / モジュール境界

### `peak_verification.py`（新規・純ロジック層、MCP 非依存）
`knowledge_store.py` と同じく MCP に依存しない純関数群。単体テスト可能。

- `parse_formula(formula: str) -> dict[str, int]`
  - `"C42H82NO8P"` 形式を元素→個数へ。2 桁以上の数、単一原子（数省略=1）を扱う。
- `monoisotopic_mass(counts: dict[str, int]) -> float`
  - 元素モノアイソトピック質量表（C, H, N, O, P, S, Na, Cl, K 等）を用いて中性質量を算出。
- `adduct_mz(neutral_mass: float, adduct: str) -> float | None`
  - 主要脂質アダクト表を適用。未知アダクトは `None`。
  - 正: `[M+H]+`, `[M+NH4]+`, `[M+Na]+`, `[M-H2O+H]+`
  - 負: `[M-H]-`, `[M+HCOO]-`, `[M+CH3COO]-`, `[M+Cl]-`
  - プロトン質量 1.007276 を用い電子質量を織り込む。
- `mass_error_ppm(observed_mz, formula, adduct) -> dict`
  - `{ "theoretical_mz", "ppm", "band" }`。
  - 帯: `|ppm| <= 5 → PASS` / `<= 10 → BORDERLINE` / `> 10 → FAIL`（定数で調整可）。
  - formula 欠落/`"Unknown"` or 未知アダクトのとき `band="UNKNOWN"`（計算スキップ、失敗しない）。
- `adduct_consistency(adduct, ion_mode, ontology=None) -> dict`
  - `{ "polarity_ok": bool, "band": "PASS|FAIL|UNKNOWN", "class_typical": bool|None, "advisory": str }`。
  - **決定的**: アダクトの電荷符号と実測極性(pos/neg)の一致を PASS/FAIL 判定。
  - **助言のみ**: ontology 由来クラスに対する典型アダクトかの soft advisory（軽量表、
    例 PC→`[M+H]+/[M+HCOO]-`、TG→`[M+NH4]+/[M+Na]+`）。判定はしない。

### `server.py` の新ツール `verify_peak_annotation`
1. `session.filtered_features` から id/name で対象 feature を引き当て
   （`inspect_metabolite_details` の照合ロジックを再利用）。
2. name / ontology / formula / adduct / m-z / rt / ion_mode / S/N を抽出。
3. `peak_verification` の決定的チェックを実行。
4. `knowledge_store`（`load_vocab` の同義語展開 + `coverage` 類似）で関連 knowledge
   slug 候補と vocab ヒットを収集。P-/O-・プラスマローゲン酸化等の落とし穴ノートを該当時に付与。
5. ドシエ JSON を組み立てて返す。**総合判定フィールドは持たず** LLM に委ねる。

## 4. ドシエ・スキーマ（返り値）

`json.dumps(..., ensure_ascii=False, indent=2)`:

```json
{
  "status": "success",
  "identity": {
    "id": 123, "name": "PC 34:1", "ontology": "PC",
    "formula": "C42H82NO8P", "adduct": "[M+H]+",
    "observed_mz": 760.5851, "rt": 12.34,
    "ion_mode": "Positive", "signal_to_noise": 42.0
  },
  "analytical_checks": {
    "mass_error": { "theoretical_mz": 760.5856, "ppm": -0.7, "band": "PASS" },
    "adduct_consistency": { "polarity_ok": true, "band": "PASS",
      "class_typical": true, "advisory": "PC は正で [M+H]+ が典型" }
  },
  "biological_plausibility": {
    "class_token": "pc",
    "vocab_hits": ["phosphatidylcholine"],
    "candidate_knowledge_slugs": ["pe-p-vs-pe-o-annotation"],
    "caveats": ["エーテル脂質の P-/O- 表記混同に注意（該当時）"]
  },
  "llm_decision": {
    "instruction": "candidate_knowledge_slugs を knowledge_expand で裏取りし、この試料系にこの脂質種が生物学的に妥当か・表記の落とし穴に当たらないかを判断して総合判定せよ。",
    "deterministic_summary": "mass_error=PASS, adduct=PASS"
  }
}
```

決定的サブスコアは `band`（`PASS` / `BORDERLINE` / `FAIL` / `UNKNOWN`）で表現する。

## 5. エラー処理・エッジケース

- データ未ロード（`session.filtered_features is None`）→ `{"status":"error", "message":"先に pai2_parser を実行してデータを読み込んでください。"}`。
- id/name 不一致 → `{"status":"not_found", ...}`（`inspect_metabolite_details` と同形）。
- 複数一致 → 全件を配列で返す（inspect と同挙動）。
- formula 欠落/`"Unknown"`、adduct `"Unknown"`/未知 → `mass_error.band="UNKNOWN"`、
  `adduct_consistency` は極性のみ判定 or `UNKNOWN`。ツールは失敗せず「計算不能」を明示。
- name 空（未アノテーション）→ analytical は可能な範囲で実行、biological は
  「アノテーション無しにつき判定不可」を返す。

## 6. テスト

新設 `tests/test_peak_verification.py`（既存 pytest 様式に合わせる）:

- **純ロジック**:
  - `parse_formula`: 複数元素・2 桁数・単一原子。
  - `monoisotopic_mass`: 既知脂質（例 PC 34:1）の理論質量を許容誤差内で検証。
  - `adduct_mz`: 各アダクトのシフト値。
  - `mass_error_ppm`: PASS / BORDERLINE / FAIL / UNKNOWN の境界。
  - `adduct_consistency`: 極性一致 / 不一致 / 未知。
- **ツール統合**（ダミー `session.filtered_features`）:
  - success / not_found / error。
  - formula 欠落時の `UNKNOWN` 降格。
  - `candidate_knowledge_slugs` の収集。

## 7. 既存コードへの影響

- `server.py`: `@mcp.tool()` を 1 つ追加（`inspect_metabolite_details` 近傍）。照合ロジックは
  既存関数を再利用し重複を避ける。
- `peak_verification.py`: 新規ファイル。他モジュールへの影響なし。
- knowledge/playbook: 既存ノート（`pe-p-vs-pe-o-annotation`, `plasmalogen-oxidation`,
  `lipid-class-search-vocabulary`）を参照。新規ノートは必須ではない。
- ドキュメント: 必要に応じ `docs/output_format.md` にツール出力の追記を検討（任意）。
