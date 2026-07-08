# 肝臓リピドーム LLM解釈精度評価 設計（Opus正解基準・フルツール駆動）

- 日付: 2026-07-08
- 対象データ: `C:\Users\yuu18\datasets\20230824_liver\20230824_liver`（`NEG` / `POS`）
- 前提: 既存の解釈品質評価ハーネス（`interp_eval.py` / `interp_eval_cases.py` / `interp_eval_run.py`）と
  エージェントループ（`agent_core.py` の DI 構造）を土台にする。ルーティングは解決済み（`phase_router`）。
- 位置づけ: 2026-07-07 の解釈品質評価（frozen 単発・盲検ペア・ローカル3本＋Azure チャンピオン）の
  **後継テーマ**。今回は (1) 新データセット、(2) 正解基準を Opus 4.8 に据える、(3) 各モデルが
  **フルにツールを駆動**、(4) 採点を Opus 基準ルーブリック照合、へ枠組みを変える。

## 1. 目的と非目的

### 目的
同一の肝臓リピドームデータ（POS/NEG）を **5モデルがツールをフル駆動して解釈**した結果を、
**Opus 4.8 の解釈を正解基準**にルーブリック照合で採点し、下位モデルの**解釈精度**を測る。得たいもの:

1. 下位4モデル（ローカルハイブリッド / Azure gpt-5.4-mini / Sonnet 5 / Haiku 4.5）の
   Opus 正解に対する解釈精度ランキング。
2. フェーズ別の強弱（どのモデルがどのフェーズ・どの軸で正解から乖離するか）。

### 非目的
- ルーティング再評価（解決済み。`phase_router_eval.py` が担当）。
- モデルの推論速度・コストのベンチマーク（別テーマ）。
- 盲検ペア相対勝率の再測（今回は Opus 正解基準の絶対採点に置き換える）。

## 2. 出場モデルと実行経路

| モデル | 役割 | 実行経路 | 自動/手動 |
|---|---|---|---|
| **Opus 4.8**（この Claude Code セッション） | 正解基準 | 私が Python から `agent_core.execute_tool` をライブ駆動。フィルタ/群指定を変え**科学的分離が出るまで再PCA**し、正解解釈＋`rubric.md` を作成 | 自動（私） |
| ローカルハイブリッド | 被評価 | 既存 `agent_core.Agent`（`chat_fn`=qwen3:14b、`interp_fn`=Azure エスカレーション）を `run_turn` で各ケース実行 | 自動 |
| Azure gpt-5.4-mini | 被評価 | 新規 OpenAI ツール呼び出しアダプタを `chat_fn` に据えた `agent_core.Agent` を `run_turn` で実行 | 自動 |
| Sonnet 5 | 被評価 | Agent ツールで `model="sonnet"` のサブエージェントを起動し、`agent_core.execute_tool`（ヘルパ `liver2_drive.py`）でツールを駆動して解釈を生成→私が回収 | 自動（サブエージェント） |
| Haiku 4.5 | 被評価 | 同上（`model="haiku"`）。サブエージェントは常に起動可のため Desktop ピッカー問題は発生しない | 自動（サブエージェント） |

**サブエージェント経路の根拠**: この Claude Code セッションに ms-data-parser MCP は接続されていない
（接続は `claude-in-chrome` のみ）ため MCP プロトコル越しには呼べないが、サブエージェントは Bash/Python
で `agent_core.execute_tool` を叩ける＝gold（私）と同一のツール駆動が可能。Agent ツールの `model` 指定で
Sonnet 5 / Haiku 4.5 を選べるため、Anthropic API キーも Desktop 手動も不要で全自動化できる。

**駆動機構の非対称（限界として明記）**: サブエージェント（Sonnet/Haiku）は `execute_tool` 直呼びで全ツールが
可視、Azure/ハイブリッドは `run_turn` のフェーズ限定ツール。→ 各サブエージェントには**ケース別の同一ツール
指針**（そのフェーズの想定ツールと再PCA/フィルタ許可）をプロンプトで与えて範囲を揃え、残差はレポートの限界に記す。
サブエージェントは temp=0 制御ができないため、**各トランスクリプトの数値を `frozen/` の gold 証拠と突き合わせ、
実際にツールを走らせた（捏造でない）ことを検証**する。

**正解基準の妥当性**: 正解＝Opus 4.8 の解釈。Qwen 系（ローカル）・GPT 系（Azure）とは別系統。
Sonnet/Haiku は同じ Claude 系のため自己系統との比較になるが、採点は「正解一致」の絶対採点であり
相対選好ではないため、系統一致による有利化は限定的。人手校正（§5）で接地を担保する。

## 3. データセットと科学的枠組み

MS-DIAL アラインメント結果（`.arf`/`.arf2`/`.mddata`）が NEG/POS とも揃う。

- **要因計画**: `GF`/`SPF`（無菌 vs 通常腸内細菌叢）× `AIN`/`HFD`/`NC`（食餌3種）、各 n=4 の 2×3。
  加えて `QC`（n=5）と `Blank`（n=1）。NEG=30サンプル、POS も同一設計。
- **規模**: NEG 9,169スポット（アノテ56.3%）。両極性ともフル設計（前回 liver の POS n=3 退化とは異なる）。
- **生物学的問い**: 腸内細菌叢（GF vs SPF）と食餌（HFD 等）が肝リピドームをどう再構成するか。
  QC n=5 により実測 QC-RSD が算出でき、2×3 により PCA 分離・差次的解析が実データで成立する。

Class ID は `arf_list_classes()` で確認済み: position0 = {Blank, GF, QC, SPF}、position1 = {AIN, HFD, NC}。

## 4. ケース定義（5フェーズ × POS/NEG = 10ケース）

各ケースは 1 ユーザーターン（route でフェーズ固定→有界ツールループ）。全モデル同一クエリ・同一ツール可用面
（`phase_router` の allowlist）で駆動する。

| # | id | フェーズ | 極性 | pipeline（駆動ツール） | クエリ主旨 |
|---|---|---|---|---|---|
| 1 | pca_neg | PCA | NEG | load_dataset(NEG)→arf_re_pca（GF/SPF・食餌で色分け） | 群分離の主因と生物学的意味 |
| 2 | pca_pos | PCA | POS | load_dataset(POS)→arf_re_pca | 同上 |
| 3 | differential_microbiome | DIFFERENTIAL | NEG | load_dataset→arf_preprocess(median,half_min)→arf_differential(SPF vs GF) | 有意脂質と注意点 |
| 4 | differential_diet | DIFFERENTIAL | POS | load_dataset→arf_preprocess→arf_differential(HFD vs NC) | 同上 |
| 5 | qc_neg | QC | NEG | load_dataset→arf_preprocess(median,max_qc_rsd=30,half_min) | 品質問題と対処 |
| 6 | qc_pos | QC | POS | 同上 | 同上 |
| 7 | identity_neg | IDENTITY | NEG | load_dataset→arf2_annotate_identities(max_rows=30) | 確信度と過剰主張リスク |
| 8 | identity_pos | IDENTITY | POS | 同上 | 同上 |
| 9 | literature_neg | LITERATURE | NEG | paper_search（菌叢-肝リピドーム系クエリ） | 仮説を支持する知見 |
| 10 | literature_pos | LITERATURE | POS | paper_search（HFD-肝脂質系クエリ） | 同上 |

**確定事項（実装時に Opus ライブ解析で最終化）**:
- 差次のコントラスト（#3 の SPF vs GF、#4 の HFD vs NC）は暫定。Opus の再PCAで**主分離因子**を見極め、
  最も生物学的に読める2コントラストへ差し替える（例: 分離が食餌主導なら #3 も HFD vs NC 系へ寄せる）。
- `arf_differential` の群指定（`group_factor`/`group_a`/`group_b`）は Class ID トークン
  （GF/SPF/AIN/HFD/NC）で構成。ライブ解析で有効な指定を確定し `interp_eval_cases` に反映する。

## 5. 採点：Opus 基準ルーブリック照合（二層審判）

### rubric.md の作成
Opus ライブ解析から、各ケースの**必須所見リスト**を抽出して `rubric.md` に固定する。必須所見の例:
- PCA: 主分離因子（菌叢 or 食餌）・寄与率・分離方向・支配的脂質クラス。
- DIFFERENTIAL: 有意脂質数（n_significant）・上位有意脂質と方向・バッチ交絡や小 n の注意。
- QC: QC-RSD 水準・drift 補正要否・欠測補完の妥当性・blank 由来除去。
- IDENTITY: MSI レベルの留保・過剰主張の回避点。
- LITERATURE: 関連論文の弁別・無関係文献の格下げ・カバレッジ判断。

### 5軸ルーブリック（辞書式：①を最上位ゲート）
| 軸 | 定義 | 採点法 |
|---|---|---|
| ① ハルシネーション | 正解／ツール出力に無い数値・主張の捏造 | 最上位ゲート。致命的捏造があれば当該ケースは他軸に関わらず失格級の減点 |
| ② 正確性 | Opus 正解との一致（分離方向・脂質の意味づけ等） | 正解照合の一致度（0–2 等の順序尺度） |
| ③ 完全性 | 必須所見リストの被覆率 | 被覆した必須所見 / 全必須所見 |
| ④ 実用性 | 次の一手・注意点の適切さ | 順序尺度 |
| ⑤ 言語・形式 | 日本語で簡潔か、多言語ドリフト・冗長がないか | 順序尺度（破綻は減点） |

各ケース×モデルで5軸を採点し、モデル別に集計（軸別平均・フェーズ別・総合精度スコア）。
盲検ペアではなく**正解基準の絶対採点**とする（今回の枠組みの要）。

### 審判の二層
- **① バックボーン**: この Claude Code セッション（私）が Opus 正解と各出力を突き合わせ採点。追加API課金ゼロ。
- **② 校正**: ユーザーが十数項目を抜き取り採点し、①との一致を確認。ズレれば審判信頼性の限界として補正。

## 6. 成果物

出力先は `interp_eval_out/liver2/`（前回分の `interp_eval_out/` 直下と分離する）:

1. **Opus 解析ノート**（`gold/analysis.md`）＋ PCA/volcano 図。再PCA の反復過程と到達した科学的解釈。
2. **`rubric.md`**: フェーズ別・必須所見＋5軸採点基準（採点の単一の真実源）。
3. **gold 正準ツール出力**（`frozen/`）: 各ケースを **Opus の正準 pipeline で駆動した際のツール戻り値**。
   採点時に審判へ「根拠」として提示する参照証拠。フルツール駆動では各モデルが呼ぶツール・引数が
   異なりうるため単一の凍結入力は存在しない——各モデルが実際に見た証拠は当該モデルの**トランスクリプト**
   （下記4）内のツール呼び出し列に残す。`frozen/` はあくまで gold の正準証拠として採点の共通参照にする。
4. **各モデルのトランスクリプト**（`interp/<case>__<model>.txt`。ツール呼び出し列＋最終解釈を含む）＋
   **スコア表**（`scores.json`/`scores.md`）。model キー: `hybrid` / `azure` / `sonnet5` / `haiku45`。
5. **最終比較レポート**（`report.md`）: 精度ランキング・フェーズ別強弱・主要所見・限界。

## 7. 実装物（コード）

1. **OpenAI ツール呼び出しアダプタ**（`interp_eval.py`）: Ollama tool schema を OpenAI function-calling
   形式へ渡し、応答の `tool_calls` を `agent_core.Agent` が期待する message dict 形へ逆変換する `chat_fn`
   （`openai_chat`）。Azure gpt-5.4-mini を `run_turn` のツールドライバにする。
2. **新データセットのケース定義**: `interp_eval_cases_liver2.py` に本 spec §4 の10ケースを追加（前回ケースと
   隔離。DIR は `20230824_liver` の NEG/POS）。
3. **フルツール駆動ランナー**: 自動モデル（ローカルハイブリッド・Azure）について各ケースを `run_turn` で
   実行しトランスクリプトを保存する `interp_eval_liver2.py`（`run_auto`）。
4. **サブエージェント用ツール駆動ヘルパ**: `liver2_drive.py`。データをプライムし、指定ツール列を1プロセスで
   順に実行して出力を印字する。Sonnet/Haiku サブエージェントがこれを繰り返し呼んでツールを駆動する。
5. **採点集計**: 5軸スコアをモデル別/フェーズ別に集計する `score_aggregate`＋トランスクリプト整形
   `transcript_text`（`interp_eval.py` の純ロジック層。unittest で検証）。

## 8. 実行手順（オーケストレーション）

1. **gold**: Opus（私）が10ケースをライブ駆動→`gold/analysis.md`＋`rubric.md`＋`frozen/` を確定。
2. **差次コントラスト最終化**: 再PCAの主因に合わせ #3/#4 を確定し `interp_eval_cases_liver2.py` を更新。
3. **auto 実行**: ローカルハイブリッド・Azure を `run_turn` で全ケース実行→`interp/` 保存。
4. **サブエージェント実行**: `model="sonnet"` / `model="haiku"` のサブエージェントを起動し、`liver2_drive.py`
   でケース別にツールを駆動させ解釈を生成→`interp/<case>__{sonnet5,haiku45}.txt` に保存。各トランスクリプトの
   数値を `frozen/` と突き合わせ、実ツール実行（非捏造）を検証。
5. **採点**: 私が rubric 照合で全モデル×全ケースを5軸採点→`scores.json`。ユーザーが抜き取り校正。
6. **レポート**: `report.md` に精度ランキング・フェーズ別強弱・限界を記述。

## 9. 未解決・実装時に確定する事項

- 差次コントラストの最終選択（§4）と `arf_differential` の有効な群指定（ライブ解析で確定）。
- POS の QC/差次が NEG と同等に成立するか（POS も 2×3 だが実データの分離強度は未確認）。
- サブエージェントが実際にツールを走らせるか（捏造でないか）を `frozen/` 照合でどう機械的に確認するか
  （数値一致のスポットチェック手順）。
- 駆動機構の非対称（サブエージェント=全ツール直呼び vs Azure/ハイブリッド=フェーズ限定 `run_turn`）を
  ケース別ツール指針でどこまで揃えるか。残差はレポート限界に記載。
- rubric の順序尺度の刻み（本 spec は各軸 0–2 に確定）と「致命的ハルシネーション」の失格閾値の具体化。
