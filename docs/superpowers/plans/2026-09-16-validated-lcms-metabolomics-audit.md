# LC–MSメタボロミクス実装監査・残タスク（2026-09-16）

対象: `2026-09-15-validated-lcms-metabolomics.md` と対応spec。
開始HEAD: `4dcd22d`、branch: `feature/validated-lcms-metabolomics`。
既存の未追跡`docs/research/`は変更していない。今回の修正は未commit。

## 判定

**部分実装。ソフトウェア完成、実Console接続合格、profile科学的検証合格のいずれでもない。**
Task 1〜14のチェック印は実装作業の記録で、受け入れ条件A01〜A29を満たした証明ではない。
既存テストが通っていても、公開入口・実ファイル境界・評価母集団の欠落があった。

| Task | 確認できた実装 | 現在の不足 |
|---|---|---|
| 1–2 | profile/certificate schema、依存hash、adapter、snapshot部品 | 独立した証明書実測証拠の受付、snapshot依存固定と再検証 |
| 3–4 | v2 request、stage builder、run/job schema部品 | profile由来の公開入力計画、実job v3作成との接続 |
| 5–7 | standard役割、注入証拠、feature binding | 不完全証拠のmaskとQC対象scopeの経路確認 |
| 8–9 | 内部標準比、行列保存、QC前後処理 | 群別filter、補完母集団、QC対象scopeの完成。複数batch/poolドリフトは拒否 |
| 10 | Welch/ANOVA/Tukey/PCAの数値部品 | 変換matrixの別result、非正値理由、群別観測/補完件数 |
| 11 | 全feature/統計出力、状態三軸 | PCA専用表、仕様§11の条件・出典・除外・制約を含むレポート |
| 12–13 | handlerとMCP登録、dataset_statistic | 公開上流、再開時profile/raw/依存検証、セッションへの行列受渡し |
| 14 | run_engine中心の合成E2E、既存回帰 | 上流4工程を置換しないfake Console E2E、v2独立プロセス試験 |
| 15–16 | 計画のみ | compatibility実装と実Console接続、科学的検証、証明書発行 |

## 今回修正した事項

1. requestファイルの`execution_purpose`をprofile読込にも適用。明示値を優先し、不正なprofile値はファイルへfallbackせず拒否。
2. requestのprofile相対パスをdataset rootから絶対解決。profile内の依存パスはprofile親、rawは別の`raw_root`から解決。
3. execution manifestの`raw`がnullになるキー不一致を修正し、実際のraw hash一覧を保存。
4. routineは`validate_certificate`を実際に呼ぶ。独立した`observed_hashes`がなければ拒否。証明書の宣言hashを観測値として流用しない。
5. 公開v2受付がv1のmethod/LBM/実行体選択へ流れないよう、`PIPELINE_V2_UPSTREAM_UNAVAILABLE`で停止。
6. blank_foldは同batchのblankを参照。QC不足batchと、生物試料があるQC不在batchを評価不能として維持。
7. 正規化参照と検出filterからstandard/blank/include=falseの混入を除去。全assay軸は保持し、正規化単位を`normalized_height`に変更。
8. ドリフト補正はincluded QC4本以上、確認済み単一batch/pool、一意な注入順を要求。下位関数がskippedなら成功にしない。
9. 異なるtarget IDから同一featureに複数内部標準を割り当てる場合を拒否。
10. TSVの小数6桁丸めを除去。小さいp/qや定量値を0へ変換しない。
11. PCA成分数を中心化・指定スケーリング後のrankで制限。
12. 計画書とworkflowの「公開入口で実行可能」「Task 1〜14完了」と読める記述を訂正。

## 優先順位付き残タスク

### R1 / P1: 公開上流と実プロセスを接続（Task 2/4/12/13/14）

- profileのexe/method/依存/raw形式を唯一の情報源として入力配置計画を作る。`_prepare_run`の停止を外す前にv1へのfallbackを禁止したテストを置く。
- `_handle_upstream`の固定lipidomics/job v2前提をschema dispatchし、実効profile methodとjob v3のmanifestを実際にConsoleへ渡す。
- raw全構成hash、依存・環境・profileを起動前/終了後/再開時に再検証。`recovery._console_supervision_state`の`upstream`固定をv2の`execute_console`に対応させる。
- profile内容を原本から再読込して古いresultへ混ぜない。snapshotから復元し、変更は明示的新runへ誘導する。
- 完了条件: 公開入口からfake Consoleが実mzTab/証拠を生成し、上流4工程も実handlerで通過。binding訂正で再起動なし、取消/timeout/親終了後完走、hash改変拒否を確認。

### R2 / P1: 証明書の実測証拠を受付へ接続（Task 1/2/15/16）

- 現証明書のfixed/reference/outputキーはopaque IDでpathではない。ID→ローカル実ファイルの対応を明示したmanifestを定義し、全ファイルを独立hash計算する。
- `load_profile(..., observed_hashes=...)`へ実測値を渡し、scope/criteria/profile/依存/検証出力の一致を確認する。
- 完了条件: 正当な証明書でroutine受付成功、各構成要素の一要素改変で拒否。現在routineが停止するのはこの未接続を隠さないため。

### R3 / P1: 証拠・QC・前処理の残契約（Task 6/8/9）

- `matrix_state._detected_mask`は部分的なevidenceで未提示セルをTrue初期化する。unknownを表現し、未知を検出済み扱いせず、内部標準分母とfilter/QCまで意味を維持する。
- `assay_qc._fix_population`のscope/target_idsと実際のbinding済み対象集合を接続。現在entryを使わず全featureを採る箇所がある。
- 検出率の生物群別規則、標準QC対象のinclude/role、補完に利用する試料・feature母集団をspecと照合した追加回帰が必要。
- 複数batch/poolのドリフトは現在明示拒否。対応する場合はbatch/pool別fitと全試料のQC区間被覆、feature別補正不能理由を定義・試験する。現在の下位補正は一部区間外を警告のみで扱う。
- 完了条件: 部分証拠、対象外feature、QC不在batch、標準品/除外試料を加えても誤ったpassや解析値が作られない。

### R4 / P2: 統計成果物とレポート（Task 8/10/11/13）

- log変換を親matrix IDと非正値理由maskを持つ別matrix resultとして保存。群ごとの観測数/補完数を出す。
- PCAのscores/loadings/寄与率を専用表で出力。現在の統計exportはWelch/ANOVA中心。
- レポートへprofile/入力の出典、処理条件、単位、除外理由、変換、多重比較、QC評価不能、証拠制約を載せる。
- standalone dataset_statisticが永続matrixを正しくcurrent判定して利用できる公開経路を完成する。
- 完了条件: 第三者が成果物だけで母集団・変換・条件・制約・result親子関係を追跡できる。

### R5 / 実入力が必要: Task 15/16

- `console/compatibility.py`と合否検査を合成入力で実装し、その後、出典確認済みmethod/ライブラリと標準品2注入以上を固定する。
- 実分析はms-data-parser MCPを入口に行う。NAS操作・実Console起動・実データ解析は今回実施していない。
- raw/試料表不一致、LCmethod 1/2、比較群、内部標準、許容差、参照結果を実行前に確定する。資料不足はPROFILE_INCOMPLETE、profileはdraftのまま保持。
- compatibility合格と科学的profile合格を別記録にする。既存の合成試験で代用しない。

## 検証記録

- 修正前のfresh process: `C:/Python314/python.exe -m pytest tests -q` → **2105 passed、1074 subtests passed、1 warning**。
- 追加回帰でprofile境界6失敗、未接続上流/manifest境界3失敗、QC3失敗、数値端点4失敗を修正前に確認。
- 修正後の対象群: profile/request 133 passed、QC等172 passed、数値端点82 passed、profile schema/input79 passed、matrix/QC/profile/engine/E2E147 passed（重複あり、合計しない）。
- 修正後のfresh process全suite: `C:/Python314/python.exe -m pytest tests -q` → **2138 passed、1074 subtests passed、1 warning、150.37秒**。
- `git diff --check` → exit 0。既存warningは`test_preprocessing.py::TestImpute::test_all_nan_column_fills_zero`の`Mean of empty slice`で、修正前後とも同じ。
- 第三者レビュー用エージェントは使用上限で最終レビュー中断。以後の差分点検と全体検証は主担当で実施。独立レビュー完了とは扱わない。
