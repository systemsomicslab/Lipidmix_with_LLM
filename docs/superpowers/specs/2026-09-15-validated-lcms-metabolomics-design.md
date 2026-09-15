# 検証済みLC–MS methodによるメタボロミクス自動処理の実装spec

- 作成日: 2026-09-15
- 状態: Draft / レビュー対象。実装済み・実データ検証済みを意味しない。
- 改訂: 2026-09-15、メタボロミクス実行経路の自己レビュー7件を反映。
- 対象リポジトリ: Lipidmix_with_LLM
- コード確認基準: `db41db0`。既存調査に加え、request、inputs、service、method参照、sample manifest、handoffを再確認した。
- 成果物: 本spec。実装plan、コード変更、実MS-DIAL実行は本作業に含めない。

## 1. 目的と完成条件

検証済みのLC–MS測定法・MS-DIAL処理method・同定ライブラリ・QC規則を固定したプロファイルを用い、ローカルの生データ投入からQC、統計、レポートまで、既存pipelineで自動実行する。

「検証済み」は論文に測定条件が掲載されていることだけでは成立しない。適用範囲、実行条件、検証入力、判定基準、実測結果が一致することを必要とする。測定法の妥当性と、ソフトウェアでの再処理の妥当性を別に記録する。

初期実装は、単一LC条件・単一極性・DDA・peak heightのLC–MSバッチを対象とする。最初の実データ検証候補はKiuchiらの甘草抽出物とアリルグリシン実験。汎用の全メタボロームカバレッジを、この限定データだけで保証しない。

完成を三つに分ける。

1. **ソフトウェア完成**: 合成fixtureで契約・数値・再開・互換性の受け入れ条件を満たし、§14.1の実Console接続試験に合格する。合成fixtureだけの成功は「単体・統合試験完了」とする。
2. **プロファイル検証完了**: 固定した実データで事前定義の基準を満たす。取得できていないmethod情報やQC資料が残れば未完了。
3. **個別バッチ完了**: 上流証跡と成果物が検証され、QC判定を付したレポートが生成される。QC不合格でも診断レポートは生成する。

## 2. 範囲

### 2.1 含むもの

- lipidomics固定をプロファイルに切り出し、metabolomicsを同じ実行経路へ接続する。
- method・MSP/TXT/LBM等の依存ファイルを解決し、ハッシュと出典を記録する。
- standard試料を明示し、化合物と内部標準の対応に基づく比率補正を追加する。
- pooled QC、blank、標準品、検出状況について、実施可能なQCと評価不能なQCを区別する。
- 二群Welch/BHの再利用、多群一元配置ANOVA/Tukeyの追加、変換条件の明示化。
- 未同定を含むfeature統計出力と、同定済み分子の既存pathway向け出力を分離する。
- 結果の依存関係、再開、MCPの公開契約、レポートを更新する。

### 2.2 含まないもの

プロテオミクス、CE-MS、DIA、新しいピーク検出エンジン、装置制御、測定法の自動最適化、POS/NEG統合、同位体追跡解析、絶対濃度・検量線フィッティング、共変量/交互作用/反復測定モデル、MS-DIALのArea制約解除、外部pathwayサービスの自動呼び出し、新GUIは対象外。

NASに対する書込み・移動・削除・解析実行は行わない。今回の検証は既にコピー済みのローカルデータを起点とする。研究データや非公開ライブラリをspec、fixture、commitへ含めない。質量データの調査・解析操作はms-data-parser MCPを入口とする。内部workerは既存のPythonサービスを利用し、MCPを自己呼び出ししない。

## 3. 論文に基づく初期プロファイルの根拠

出典: [Kiuchi et al., 2025, Methods](https://pmc.ncbi.nlm.nih.gov/articles/PMC12738721/)、[出版社ページ](https://www.nature.com/articles/s41540-025-00620-z)。詳細な確認記録はローカルの `analysis/metabolomics-extension/kanzo-20260914/paper-method/method-evidence.md`。

| 条件 | 甘草抽出物LC–MS | アリルグリシン実験 |
|---|---|---|
| 装置 | ExionLC AD / ZenoTOF 7600 | 同じ |
| 取得 | Positive DDA | 甘草抽出物methodを参照 |
| LC | ACQUITY UPLC BEH、1.7 µm、100 × 2.1 mm、30°C | 同methodを参照 |
| 移動相・流量 | A: 0.1%ギ酸水、B: メタノール、150 µL/min | 同methodを参照 |
| ソース・衝突 | 450°C、5500 V、CE 35 V、spread 15 V | 同methodを参照 |
| MS1/MS2 | m/z 50–1500 | m/z 50–750 |

論文本文の該当節にはLC時間プログラム、注入量、処理method一式の記載がない。補足PDFは前回取得できず内容未確認。脂質解析節のMS-DIAL版・閾値を、親水性LC–MSの値として流用しない。主たる親水性メタボロームデータはCE-MSであり、本specのLC–MS検証と分離する。

ローカル資料からは、GABA/GABA-d6比とANOVA/Tukeyの解析コードが確認されている。コードを実行して再現性を確認したわけではない。これを内部標準補正と多群統計の具体的な要件とする。

## 4. 採用構成と既存コードの合理化

独立したmetabolomics用pipelineを新設する案は状態管理を重複させる。既存コードへomics分岐を散在させる案は設定と数値処理を密結合にする。本specでは**共通実行機構＋版管理した測定プロファイル**を採用する。

| 現行の根拠 | 変更契約 |
|---|---|
| `pipeline/inputs.py::_OMICS`、`pipeline/service.py`のAnalysisJob生成 | 解決済みプロファイルのomicsを一貫して使用 |
| `pipeline/request.py`のv1、peak_height、conservative-v1 | v1を維持し、明示v2へ拡張 |
| `console/method_file.py::REFERENCE_KEYS`がLBMのみ | ソフトウェア版に対応した型付き依存一覧へ一般化 |
| `pipeline/service.py::_plan_fingerprint` | プロファイルと全上流依存を含める |
| `analysis/sample_manifest.py`の固定8列・4役割 | v1互換を維持し、v2でstandardを追加 |
| `analysis/preprocessing.py`、`dataset_analysis.py` | 既存数値関数を再利用し、内部標準比と変換契約を追加 |
| `analysis/dataset_export.py`のInChIKey必須 | 全feature出力を追加。既存pathway TSVは維持 |
| `pipeline/report.py`のTSVを含む必須成果物判定 | v2で全feature成果物と任意pathway成果物を分離 |

既存のConsole監視、lock、原子的保存、二重実行防止、入力変化検出、唯一の主mzTab選択、sample照合、結果ID・ハッシュ検査を再利用する。これらの証跡をQCラベルで代替しない。

## 5. プロファイル契約

新規JSON `lcms-profile.v1`。未知キー・型違反を拒否する。外部ファイルパスはプロファイルファイルの親を基準に解決し、ローカルsnapshotへコピーして実行する。ネットワークからの自動取得はしない。

| フィールド | 内容と制約 |
|---|---|
| `schema`, `profile_id`, `revision` | schemaは固定値、idは安全なslug、revisionは正整数。更新時は新revision |
| `omics` | 初期v1は`metabolomics`。既存lipidomicsは互換adapterで解決 |
| `acquisition` | `separation=lc`, `acquisition_type=dda`, polarity、装置、LC条件、MS範囲、試料マトリクス、適用範囲 |
| `software` | MS-DIAL版、実行ファイルpathとSHA-256、adapter版 |
| `processing` | method path/hash、`measure=peak_height`、依存ファイル一覧、実効設定snapshot |
| `analysis_recipe` | 前処理・変換・内部標準の対応付け規則の既定値。dataset固有feature IDは含めない |
| `feature_targets` | §8.1の論理target定義。target_idはprofile内で一意 |
| `matrix_recipes` | §8.2の行列生成規則。recipe_idはprofile内で一意でdefaultを含む |
| `qc_policy` | §9のmetricごとの対象、基準、必須性 |
| `evidence` | 条件ごとの出典URI/ローカル資料hash、所在、`paper_explicit / paper_cross_reference / supplied / locally_validated / proposed` |
| `validation` | `draft / validated`、適用範囲、検証証明書path/hash |

測定条件の未記載値は値nullと理由を持たせる。nullを装置既定値と解釈しない。draftは不足を表示してplanできる。validatedを名乗るには測定法資料と実行条件の特定、および検証証明書の検査が必要。論文にない条件は研究者資料等で補完できるが、出所を保持する。

証明書は`lcms-profile-validation.v1`とし、profile本体（validation部分を除く）のcanonical hash、全依存hash、固定入力hash、基準ファイルhash、検証出力hash、各判定、実施者、日時を必須とする。全必須基準がpassであることを機械検証する。評価結果を見た後の閾値変更は別revisionの再検証とする。署名認証基盤は作らない。

初期の論文由来プロファイルはdraft。ソフトウェアが動くことを理由に自動昇格しない。

### 5.1 methodと依存ファイル

依存要素は`dependency_id, kind, method_key, path, sha256, required`。kindは`msp / text_identification / rt_reference / lbm`。MS-DIAL版で実際にサポートするキーをadapterのallowlistで検証する。任意の文字列キーをパスと推測しない。

method原本と依存原本は変更しない。実行コピーだけを絶対パスへ書換え、原本hash、書換え後hash、差分を記録する。依存ファイルがない場合は起動前に停止する。metabolomicsでLBMを必須にしない。旧`lbm_file`はlipidomics互換入力として残す。

polarity、omics、DDA、measureがmethod/profile/request間で矛盾すれば`PROFILE_METHOD_CONFLICT`。実行に必須のキーが解決できなければ`PROFILE_INCOMPLETE`。同名化合物やファイル更新日時で候補を自動決定しない。

## 6. MCP・request・永続状態

既存`pipeline_plan/run/status/resume/cancel`の入口を維持する。追加の汎用workflowエンジンや別metabolomicsサーバーは作らない。

新しい`pipeline-request.v2`は既存のtarget、sample_manifest、output_root等を継承し、次を追加する。

- `omics`: `metabolomics`必須。
- `profile_file`: 必須。上流条件はこのファイルから解決し、v2でmethod_file/lbm_fileの直接指定は拒否する。
- `execution_purpose`: `routine`（既定）または`validation`。routineはvalidated profileが必須。validationはdraftで実行可能だが、実行に必要な値の欠落は許さない。
- `statistics`: §10の変換と検定定義。v2では旧comparisonsとの同時指定を拒否する。
- `standard_assays`: target_idから標準注入sample_id配列への対応。省略時は空。role=standardかつinclude=trueであることをmanifestと照合する。バッチ固有のためprofileには含めない。
- `preprocess`: profileの既定値を明示指定で上書き可能。ただしroutineで証明書の許容範囲外になる場合は`PROFILE_SCOPE_MISMATCH`。

schema省略は従来どおりv1。v2を暗黙に推測しない。値の優先順位はMCP明示値 > analysis-request.json > profile既定値 > v2既定値。各実効値に出所を保存し、nullと未指定を区別する。routineで許される比較群の変更等は証明書の適用範囲内に限定する。

v2の既定前処理は`normalize=none, impute=none, drift_correct=false`、filter閾値は無効。自動最適化を行わず、profileが定める手順を再現する。v1のconservative-v1既定値は変更しない。

metabolomicsは`pipeline-run.v2`、`analysis-job.v3`を書き、readerは旧版を継続して受ける。job v3はprofile snapshot/hashと全依存一覧を追加する。v1 pipelineは旧契約・旧schemaで新規作成・再開する。過去の記録をインプレース変換しない。

planは実効条件、採用raw一覧、不足/矛盾、各QCの実施可否、出力予定を返す。runは同じ解決処理を呼び、正当な入力なら追加確認なしで進行する。statusは§11の三軸を返す。

### 6.1 再開・指紋

上流指紋にraw全構成ファイルSHA-256、採用形式、profile内容hash、method原本hash、依存hash、実行環境manifest hash、adapter版、polarity、measureを含める。実行時の絶対パス書換え後method hashは実行証跡として別途保存し、出力先が変わるだけで計画の同一性が変わらないようにする。hashは計画時、実行直前、完了検証時に確認する。hash計算自体も進捗を記録する。

profile・method・library・raw・実行環境・極性・measureの変更は`NEW_PIPELINE_REQUIRED`。sample manifest・許可された前処理・統計定義・§8.1のバッチ固有対応付けの修正は新request revisionで再開する。対応付け結果はprofile本体を書き換えない。metadata修正は上流assay対応を再検証してから下流へ反映する。

下流結果IDは上流IDに加え、manifest内容hash、recipe、内部標準map、feature選択、変換、群・検定設定に依存する。変更後に古い図やTSVをcurrent扱いしない。古い成果物は上書きせず履歴として残す。

### 6.2 統計requestとstage契約

`statistics`は非空の配列。各要素は一意な安全slugの`statistic_id`、`kind`（`pca / welch / anova_tukey`）、`matrix_recipe_id`、`transform`（`none / log2`）、`feature_scope`を必須とする。`feature_scope`は`{mode: all_eligible}`または`{mode: targets, target_ids: [...]}`。target_idsはprofileで定義した論理IDで、バッチ固有feature IDではない。

- pca: `scaling`（none/autoscale）、`n_components`（正整数、既定2）。groups/alphaは受け付けない。
- welch: `reference_group`, `test_group`（異なる2群）、`q_threshold`（既定0.05）、`log2fc_threshold`（既定1.0）。
- anova_tukey: `groups`（重複のない3群以上）、`alpha`（既定0.05）。
- 確率閾値は0より大きく1未満、log2FC閾値は非負有限値とする。kindごとの未知キーを拒否する。
- statistics省略時はprofile指定の既定値、そこにもなければ全対象featureのPCAを生成する。既定PCAは`statistic_id=pca`, `matrix_recipe_id=default`, `transform=none`, `scaling=autoscale`, `n_components=2`。
- `target=auto`は検定を含めばdifferential、PCAだけならexploratory。exploratoryに検定を指定、またはdifferentialに検定がなければrequest不正とする。旧comparisonsはv2では受け付けない。

v2のstage列は `prepare_inputs → execute_console → validate_outputs → load_dataset → resolve_metadata → load_assay_evidence → resolve_feature_bindings → qc_raw → preprocess → qc_processed → [statistics:<id> → export:<id>] → report`。角括弧はstatistics順に展開し、既存v1 stage IDは変更しない。各stageは入力result ID/hashとrequest revisionを保存する。

`resolve_feature_bindings`の未解決はneeds_inputで中断する。`pipeline_resume`の`feature_bindings`更新で候補選択・理由を受け取り、同stageから再開する。更新payloadはdataset hash、target_idごとのfeature_id、選択理由。選択はprofileの許容規則内に限定し、外れればPROFILE_SCOPE_MISMATCH。上流Consoleは再実行しない。

metadata変更はresolve_metadata以降、bindingまたはstandard_assays変更はresolve_feature_bindings以降、recipe変更はpreprocess以降、統計定義変更は該当statistics/exportとreportを無効化する。元データQCの対象集合に影響するrecipe変更はqc_raw以降を無効化する。statistics、feature_bindings、standard_assaysはresumeの更新対象とし、元のrequestと同じ厳密検証を行う。統計の追加・削除でもstage集合を再構築し、削除済み統計の成果物を必須出力に残さない。

QC failと統計not_evaluableは結果を保存して次stageへ進む。対応付け不足や補正の前提不足はneeds_inputで止まり、再開に必要な情報を示す。reader障害・不正契約・破損成果物等の技術的失敗はfailedとし、診断レポートをbest effortで保存する。統計不能と技術的失敗を同一扱いしない。restart時のDatasetState再構築は、currentなmetadata/binding/matrix結果を検証して復元する。

## 7. raw受付と試料情報

1 runは単一LC条件・極性・質量範囲の測定群とする。甘草の`LCmethod 1`と`LCmethod 2`を一つのrunへ統合しない。method名だけで条件一致を断定せず、profile適用の根拠を試料情報に記録する。

wiff/wiff2が共存する場合、既存の形式選択で採用形式を明示し、片方を実行用コピーへ用意する。元フォルダ内の他形式は保持する。同じstemだけで両形式の内容同一性を主張しない。scan等の必要sidecarを検査し、primary＋sidecarを一測定として数える。

`sample-manifest.v2`はv1の8列順を保持し、末尾に`biological_sample_id`を追加する。roleは従来4値に`standard`を追加。sample_idは注入単位で一意、source_fileも一意。biological_sample_idは反復注入を識別するため重複可、空欄は生物学的独立性未確認とする。

- `include=false`は統計とQCの双方から除外し、raw対応一覧からは消さない。
- standardは統計群・pooled QC・PQN参照試料へ自動投入しない。内部標準はfeatureであり試料roleではない。
- qcはqc_poolを明示する。pool不明はpooled QCの必須判定を満たさない。
- 全rawとmanifestが一対一対応しない場合、既存のmissing/extraエラーで起動前停止する。名前から補正・群推測しない。
- 各検定ではbiological_sample_idの明示を必要とする。同一IDの複数注入が含まれる場合、初期版は`REPEATED_MEASURES_UNSUPPORTED`で停止し、自動平均しない。

## 8. 前処理と内部標準補正

元行列、検出mask、gap-fill証跡を保存する。処理順は、試料除外の確定 → 標準/対象feature対応付け → 元データQC評価集合の固定と評価 → 統計対象の検出filter → 内部標準比または全体正規化 → 任意のドリフト補正 → 処理後QC評価 → 統計対象のQC filter → 任意の補完 → 統計用変換。

解析対象featureのeligibility maskと、補正・QCに使うsupport feature集合を分離する。検出filterやblank filterで解析対象から除外された内部標準も、support行列からは削除しない。supportとして値を利用できるかは内部標準専用規則で判定する。元行列はどちらのfilterでも破壊しない。

検出率はgap-fill後の非ゼロ値で代用しない。maskが不明なら不明と報告する。QC-RSDを補完後行列で合格にしない。

内部標準の論理的対応はprofileに保持し、バッチ固有の`internal-standard-map.v1`は解析後に§8.1で生成する。mapの要素は対象feature_idと標準feature_idで、dataset hashとbinding結果IDを必須とする。別datasetのmapは入力として拒否し、同じprofile規則で新たに対応付ける。

- 補正値は同一注入の`target_height / standard_height`。単位は`internal_standard_ratio`。絶対濃度と表示しない。
- 一対象に複数標準、自己参照、循環、存在しないIDは`INTERNAL_STANDARD_MAP_INVALID`。
- 標準値が欠損・非有限・0以下、またはprofileが要求する検出条件を満たさなければ比は欠損。分母の補完・微小定数加算をしない。
- 分母不正で生じた欠損は後段補完も禁止し、sample/featureの理由を保存する。
- map未指定のfeatureは元の単位で保持する。補正済みと未補正を同じ統計行列へ混ぜず、各統計は§8.2のrecipeから生成した一意のmatrix result IDを参照する。
- 内部標準比viewへのTIC/median/PQNは初期版では拒否する。二重正規化を暗黙に実行しない。

### 8.1 profile規則からバッチ内featureへの対応付け

profileの`feature_targets`に、`target_id`、化合物の識別子（名前だけは不可）、adduct、charge、期待m/z、m/z許容誤差ppm、期待RT分、RT許容誤差分、必要な同定証拠を定義する。必要証拠は`mass_rt / library_match / authentic_standard_match`のいずれか。library_matchにはライブラリID/hashとスコア項目・閾値を要求する。authentic_standard_matchには照合条件を定義し、実際の標準注入IDはrequestのstandard_assaysをmanifest経由でassay IDへ解決する。証拠欠落を名前一致で補わない。

内部標準規則はtarget_idとstandard_target_idの対応。MS-DIAL処理後、許容範囲と証拠条件を全て満たす候補が一つなら自動確定、ゼロまたは複数なら`FEATURE_BINDING_UNRESOLVED`でneeds_inputとする。最高強度・先頭候補だけで選ばない。標準注入がprofileで必須の場合、manifestにその役割がないバッチも未解決となる。

結果`feature-bindings.v1`にはdataset hash、規則hash、全候補と不採用理由、採用feature ID、証拠参照、automatic/manual、手動選択理由を保存する。手動で候補を選択しても同定証拠の不足を解消したことにはしない。証拠条件を変更する必要があればprofileを改訂する。

### 8.2 行列契約

profileの`matrix_recipes`は一意なrecipe_idごとに`base`（`peak_height / internal_standard_ratio`）、normalize、drift_correct、filter、imputeを定義する。既定recipe_idはdefault。正規化・補正後もbaseを追跡し、単位は未補正height、normalized_height、internal_standard_ratioを区別する。

`analysis-matrix.v1`結果はmatrix result ID、dataset ID、recipe ID/hash、binding ID、assay/feature ID順序、値、単位、eligibility mask、検出mask、補完mask、欠損理由、補正履歴を持つ。前処理済み行列と統計変換後の行列は別resultとし、親IDで接続する。

statisticsのmatrix_recipe_idをcurrentな前処理matrix result IDへ解決し、統計結果に両方を保存する。該当resultがない・staleなら再構築し、rawへフォールバックしない。log2FCはこの前処理行列の統計変換前の値で計算する。補完を使った場合は群ごとの観測数と補完数も出力する。

## 9. QC契約

QC項目は`metric, scope, required, threshold, evidence_requirement`を持ち、結果は`pass / fail / not_evaluable`と理由・使用注入数を返す。閾値はprofileで明示する。RSD 30%等を本論文の検証済み値として埋め込まない。

| metric | 初期版の計算・前提 |
|---|---|
| `pooled_qc_rsd` | 同pool・同batchに有効QCが3以上。標本SD/平均×100、平均>0。元/処理後を併記 |
| `blank_fold` | 対象群の生物試料中央値 / 同batch blank中央値。各1以上。分母0で分子>0は+inf、両方0は評価不能 |
| `detection_rate` | 指定したincluded sample群で、検出mask真の数/群の全注入数。欠損・gap-filled-onlyは検出に加算しない |
| `standard_rt_error` | 指定標準featureの注入ごとの観測RT分と期待RT分の絶対差。§9.1の証拠必須 |
| `standard_mass_error_ppm` | 注入ごとの(観測m/z−期待m/z)/期待m/z×10^6。合否は絶対値。§9.1の証拠必須 |
| `internal_standard_valid_fraction` | 分母が有効な注入数/対象included注入数 |

閾値の演算子と集計単位（feature、batch、profile指定target集合）を必須とする。全feature QCではfail featureを除外する規則と、バッチ合否の`minimum_pass_fraction`を別に指定する。QC metricの評価集合はqc_raw stageでfilter前に固定し、ID一覧とhashを保存する。対象はprofile指定target集合または全raw feature集合。処理後QCでも同じ集合を使い、filterで減らさない。

各集合の要素をpass=P、fail=F、評価不能=U、総数=Nとする。N=0はnot_evaluable。pass率はP/Nで、評価不能も分母から除かない。minimum_pass_fraction=tの集合判定は、P/N≥tならpass、(P+U)/N<tならfail、それ以外はnot_evaluable。集計指定のない必須単一項目はその判定を直接使う。除外後featureだけのpass率は参考値として別名で表示し、バッチ合否には使用しない。

バッチQCは、必須項目のfailが一つでもあればfail、failがなく必須項目にnot_evaluableがあればnot_evaluable、それ以外はpass。必須項目がゼロならnot_evaluable。任意項目のfailは警告として残す。

ドリフト補正を明示した場合は既存アルゴリズムの前提（確認済みbatch/order/poolと必要QC数）をすべて満たす必要がある。不足時は`QC_PREREQUISITE_MISSING`で当該処理を停止し、別手法へ切替えない。標準品だけのデータにpooled QCの合格を付けない。

### 9.1 注入ごとの測定証拠

新規`assay-feature-evidence.v1`は`dataset_id, feature_id, assay_id, observed_rt_min, observed_mz, detection_status, source_artifact_hash, source_locator, reader_version`を持つ長形式の表。feature×assayは一意で、元のRT単位と分への変換をprovenanceに保存する。欠損値には理由を付ける。

取得元は、接続試験で対応が確認されたMS-DIAL alignment/peak成果物を読む既存parserの出力とする。adapterが取得元の形式・版・feature/assayへのキー対応を宣言する。新形式が必要ならそのreaderの実装・fixtureを本拡張に含める。名前だけの結合、SMFの代表RT/m/zを各注入へ複製する処理は禁止する。

対応成果物がない、または該当値を取得できない場合はmetricをnot_evaluableにする。外部から供給する証拠表も同じschema、dataset/assay整合性と元成果物hashを検証する。値を取得できることと同定が確定していることは区別する。

## 10. 統計

全検定はincluded role=sampleのみを対象とし、対象featureの選択規則、matrix recipe、群、biological_sample_id、変換を事前に固定する。解析後に解決したfeature集合とmatrix result IDを保存する。結果を見て群除外を自動変更しない。

共通変換は`none / log2`、PCA scalingは`none / autoscale`。v2既定は変換none、PCA autoscale。log2は有限正値のみ許可し、0以下は欠損化して理由を保存する。1へのclipや暗黙pseudocountは使わない。PCAは全採用試料で有限なfeatureのみを使い、除外数を表示する。比率が1未満でも情報を保持する。

- **PCA**: 探索用。standard/qc/blankは初期版の学習対象から除外。最小2試料・2非定数feature、成分数は行列rank以下。不足は評価不能成果物を返す。
- **Welch/BH**: 既存のWelch統計量・p値とBHの数値部品を再利用する。現行two_group_testの変換・効果量処理をそのままv2へ適用しない。各feature各群に有限値2以上を必要とする。BH母集団は同一statistic_idの検定可能feature全体。log2FCは前処理matrixの統計変換前のtest/reference算術平均比から計算し、非正平均ならNA。p値に用いた変換を別記する。
- **ANOVA/Tukey**: 3群以上の独立群。各feature各群に有限値2以上。通常の一元配置ANOVAとTukey HSDを同じ変換後行列で計算する。ANOVA p値には検定可能feature全体でBHを適用。Tukeyはfeature内全群対に対する補正であり、全feature横断FDRとは表示しない。ANOVA有意featureだけにTukeyを限定せず、検定可能feature全体で実施する。
- 定数列、残差分散0、試料不足はfeatureごとにNAと理由を返す。全featureが検定不能なら検定全体をnot_evaluableにする。NaN p値を0や有意に変換しない。

ANOVA出力はF、df、p、q、各群n。Tukey出力は群対、平均差、信頼区間、補正p、alpha。Welchのvolcano/15列TSVにANOVA結果を無理に格納しない。ANOVAには群別分布図と専用表を出す。

v2のlog変換・算術平均比にはpseudocountを加えない。v1のpseudo_count既定値とlog空間の効果量定義は変更しない。v2結果には`effect_size_definition=log2_arithmetic_mean_ratio`を保存し、15列TSVへの任意出力でも付属provenanceへ継承する。

## 11. 完了状態・出力・レポート

v2では次の独立項目を保持する。

- `execution_status`: 既存のpipeline実行状態。completedには上流終了証跡、入力不変、主mzTab検証、最新必須成果物hashの成立が必要。
- `qc_status`: pass/fail/not_evaluable。
- `analysis_status`: ready/limited/not_evaluable。全指定統計が算出可能ならready、一部のみ可能ならlimited、全て不可ならnot_evaluable。QCとは独立。

`validated_success`はexecution completed、routine実行、qc pass、analysis readyの全成立時だけtrue。QC failでも、統計が計算可能なら診断用として続行し、全結果とレポートにQC不合格を付記する。評価不能を含む明示的な結果JSONも成果物だが、有効な統計結果と数えない。

必須成果物: 解決済みprofile、実行条件/依存manifest、試料対応表、注入単位証拠表（取得不能なら理由JSON）、feature binding結果、前処理matrixと履歴、QC評価集合とQC JSON、全feature定量表、指定統計ごとの結果JSON/TSVと図または評価不能理由、最終Markdownレポート。内部標準未使用時はbinding/mapのnot_applicableを記録する。全てrun/result IDとhashを持つ。

全feature表のキーはfeature_id。annotation名、m/z、RT、InChIKey（空可）、同定証拠参照、定量view/単位、検出/gap-fill状態、除外理由を保持する。annotationのない行を捨てない。元SMEの複数候補を保持し、best hitを確定同定と呼ばない。

既存15列pathway TSVは契約を変えず、二群結果の同定済み対象だけを任意出力する。対象ゼロなら`not_applicable: NO_ANNOTATED_FEATURES`とする。全feature表があれば、この理由だけでpipelineを未完了にしない。

レポートには測定/処理条件と出典、profile検証範囲、試料除外、QCの前提と判定、内部標準、変換、検定と多重性、結果、制約を必ず含める。LLMによる考察は完成要件にしない。生成する場合も数値を再計算せず、参照result IDを示し、同定・QC・検定上の制約を引き継ぐ。

## 12. 実装境界

| モジュール案 | 責務 |
|---|---|
| 新規 `lipidmix/console/profiles.py` | profile読取、版/出典/検証証明書検査、snapshot |
| `console/method_file.py` | 依存キー解決、実行コピー書換え。profile読取と数値解析を混ぜない |
| `pipeline/request.py`, `inputs.py`, `service.py` | v2解決、指紋、共通実行接続。v1互換adapter |
| `pipeline/store.py`, `engine.py`と既存worker | v2 stage生成、schema dispatch、needs_input/再開、行列復元と依存無効化 |
| `handoff/schema.py`, `console/validation.py` | job v3、依存整合性、旧版読取 |
| `analysis/sample_manifest.py` | v2 roleと生物試料ID、厳密照合 |
| 新規 `analysis/feature_bindings.py` | profileの論理targetをバッチ内featureへ対応付け、候補と証拠を保存 |
| 新規 `analysis/internal_standards.py` | bindingからmap生成、比率計算と不正理由mask |
| 新規 `analysis/assay_evidence.py`と既存parser adapter | 注入単位のRT/m/z/検出証拠を読み、feature/assayへ厳密結合 |
| 新規 `analysis/assay_qc.py` | profile QC規則の評価。数値関数は既存から再利用 |
| `analysis/dataset_analysis.py`, `preprocessing.py`, `result_state.py` | view、変換、統計・結果依存の接続 |
| 新規 `analysis/multigroup.py` | ANOVA/Tukey結果契約と数値処理 |
| `analysis/dataset_export.py`, `pipeline/report.py` | 全feature/多群出力、三軸判定、レポート |
| `tools/pipeline_tools.py`, `tools/dataset_analysis_tools.py` | MCP入力検証と結果返却。計算処理は持たない |

ファイル名はplan作成時に既存モジュールとの重複を再確認する。責務と契約は本specを基準とする。全面改名・既存ARF経路の廃止・数値アルゴリズムの無関係な書換えは行わない。

## 13. 受け入れ条件

| ID | 検証可能な条件 |
|---|---|
| A01 | v1の省略値、既存lipidomics実行、保存済みrunの再開、15列出力契約が変わらない |
| A02 | v2でomicsがmethod選択からjob/handoff/reportまでmetabolomicsとして一致する |
| A03 | LBMなしのMSP/TXT profileを解決でき、依存欠落・hash不一致はConsole起動前に止まる |
| A04 | draftでroutineを拒否し、validationでも必須設定の不足を拒否する |
| A05 | validationラベルを手書きしても、証明書・hash・必須基準が不一致ならvalidatedとして使えない |
| A06 | 同じサイズ/mtimeに戻したraw変更もhashで検出する。method・library変更で旧runを再利用しない |
| A07 | primary/sidecarの欠落、manifest余剰/欠落、重複IDを起動前に検出し、NASへ書かない |
| A08 | standardとexclude試料が生物比較/PQN/pooled QCへ混入しない |
| A09 | target=10、standard=2の比が5となり、0/欠損分母は補完されず欠損理由が残る |
| A10 | 同名feature候補の曖昧性、別datasetのmap、循環を拒否する |
| A11 | 0.25と0.5のlog2値が−2と−1になり、1へのclipで消失しない |
| A12 | 検出率がgap-fillの非ゼロ値で増加せず、QC-RSDが補完値で改善されない |
| A13 | QC不足はnot_evaluable、必須QC failはfailとなり、どちらもpassに変換されない |
| A14 | Welch/BH、ANOVA/Tukeyを独立に固定した小規模数値fixtureと照合し、群方向、df、多重性の範囲が一致する |
| A15 | 同一生物試料の反復注入を独立nとして数えず停止する |
| A16 | 全件未同定でもfeature出力と診断レポートが完成し、pathway出力だけnot_applicableとなる |
| A17 | QC fail＋成果物正常はexecution completed/QC fail、統計不能はanalysis not_evaluableとして区別される |
| A18 | manifest・map・変換・群変更後に旧PCA/統計/図をcurrentとして返さない |
| A19 | 再起動・resume・同時run・cancel・timeoutでも既存の重複実行防止と証跡検証が維持される |
| A20 | source原本が不変で、出力からprofile・method・library・raw・統計条件へhashで追跡できる |
| A21 | 同じprofileを異なるfeature IDの2バッチへ適用し、規則からそれぞれ正しい内部標準mapを生成できる |
| A22 | 候補0/複数でneeds_inputとなり、許容範囲内のbinding更新後、Console起動回数を増やさず再開する |
| A23 | 同じfeatureの2標準注入で異なるRT/m/zを保持する。代表値しかないfixtureでは標準QCがnot_evaluableとなる |
| A24 | filter前100feature中P=60,F=40,t=0.8なら、不合格40件を除外してもバッチfail。P=60,F=10,U=30ならnot_evaluable |
| A25 | 統計対象filterで内部標準を除外してもsupport値が保持され、専用規則が有効なら比率を算出できる |
| A26 | 正規化で値が変わるfixtureで、統計がrawでなく指定matrix resultを参照し、再開後も同じ結果になる |
| A27 | reference=[1,9], test=[4,4]でv2 log2FCがlog2(4/5)となる。検定用log変換はpseudocountなし。v1既存fixtureは不変 |
| A28 | ANOVA/PCA/Welchの混在requestからstageが生成され、統計追加/削除・restart・部分評価不能でも出力とcurrent判定が一致する |
| A29 | §14.1の実Console接続試験で非脂質method、MSP/TXT、raw reader、mzTab/assay対応を確認し、接続報告を保存する |

新しい契約と数値処理は最小RED→GREENテストを先に作成する。対象テスト後に全suiteを実行する。Console fixtureのexit 0だけではA19/A20合格にしない。実データ検証はMCP経由で行い、科学的結果とソフトウェア試験結果を別々に報告する。

## 14. 初期データの適格性とリリース判定

コピー済み試料表にはrawと不一致があるため、そのまま自動実行の合格データにしない。根拠を伴うローカルmanifest訂正を記録し、元資料を変更しない。標準品をpooled QCへ読み替えない。既存資料で証明できないドリフト補正やQC-RSDの実データ検証は、未実施として残す。

初期検証では、(1)実行用methodと依存の確定、(2)試料対応の確定、(3)GABA/内部標準の対応の固定、(4)比較と変換の固定、(5)既存出力との数値照合、(6)QCとレポートの確認を行う。照合対象・許容誤差は実行前の検証基準ファイルで固定する。元表と異なる統計条件を使う場合は論文再現と呼ばない。

現状の資料不足はprofile検証の未充足事項であり、ソフトウェアspecの仮の既定値で埋めない。実装完了時にもprofileがdraftなら「ソフトウェア実装済み、実データprofile検証未完了」と報告する。

### 14.1 実Console接続試験

ソフトウェア完成には、固定したMS-DIAL Consoleとローカルの少数SCIEX rawを使い、MCP経由で次を確認する。生物学的仮説の再現やQC閾値の妥当性を証明する試験とは分離する。

1. Console版、exeおよびraw reader等の実行依存DLL/設定ファイルのpath/hashを実行環境manifestへ記録する。対象形式はwiffまたはwiff2のどちらかを明示し、試験していない形式を対応済みと表示しない。
2. metabolomics methodとMSP/TXTの実効キー・読込を確認する。既知標準を含む入力でライブラリ由来のannotationが出力へ反映されることを確認し、未知キーが黙って無視される状態を合格にしない。
3. raw読込、peak検出、alignment、height mzTab出力、予定した全assayとの一対一対応、SMF/SME参照を確認する。exit 0だけでは不十分とする。
4. 注入単位証拠の取得元形式とreaderを確認する。標準RT/m/z QCを対応機能としてリリースするには、少なくとも2注入で証拠表を検証する。取得不能なら当該QC機能は未対応と報告し、それを必須とするprofileはvalidatedにできない。
5. method・ライブラリ欠落、期待するannotation不在、raw reader不適合を、成功ではなく失敗/未対応として報告できることを確認する。

接続報告は`lcms-console-compatibility.v1`として、環境manifest hash、method/依存/input/output hash、対応形式、取得可能な証拠項目、各試験結果、日時を保存する。fixture成功、接続成功、科学的profile検証成功を別々に表示する。非公開rawやライブラリはcommitせず、接続報告のローカル保存先を記録する。

## 15. 既存specとの優先関係

[raw-folder統合設計](2026-09-05-raw-folder-pipeline-integrity-and-metadata-design.md)の監視・証跡・再開契約を継承する。本specはmetabolomics v2に限り、profile、standard、内部標準、多群統計、任意pathway exportを追加する。旧specの脂質固定・多群対象外・method生成対象外を、旧v1の利用者に遡及して変更しない。

このspecの承認は実装plan作成の基準となる。コード実装と実データ検証の完了は、それぞれ別の証拠で判断する。
