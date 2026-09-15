# pipeline-request.v2 — 検証済みLC–MSメタボロミクスのrequest契約

検証済みLC–MSメタボロミクス実行経路（spec
[2026-09-15-validated-lcms-metabolomics-design.md](../superpowers/specs/2026-09-15-validated-lcms-metabolomics-design.md)
§6, §6.2）が使うMCP request契約。実装は
[`lipidmix/pipeline/request_v2.py`](../../lipidmix/pipeline/request_v2.py)。既存の
`pipeline-request.v1`（[`lipidmix/pipeline/request.py`](../../lipidmix/pipeline/request.py)、
[`docs/schema/`にはv1専用の文書は無く、実装のdocstringが正準](../../lipidmix/pipeline/request.py)）
とは別契約であり、上流条件の出所が根本的に異なる: v1は`method_file`/`lbm_file`を
requestへ直接指定するが、v2は検証済み
[`lcms-profile.v1`](lcms-profile-v1.md)（`profile_file`）から上流条件を解決し、
`method_file`/`lbm_file`の直接指定を拒否する。

> このモジュールも`profile_file`が指す実体（profile本体）についてはprofile_schema
> 同様にファイルを読まない——読込・検証・hash計算は呼び出し側（後続タスクの
> `pipeline_plan`/`pipeline_run`）の責務で、ここでは既に検証済みのprofile dict
> （`profile_schema.validate_profile`の戻り値相当）を受け取って構造的に検証
> するだけ。ただし`analysis-request.json`（v1と同じファイル）だけは例外——
> v1が同じ場所（request層自身）でファイルを読むのに合わせ、
> `request_v2.read_request_file`がv2 shapeとして自ら読む。

## schema dispatch（v1との共存）

`lipidmix.pipeline.request.resolve_request(source_root, explicit, *, profile=None)`が
実効schema（下記）を判定し、`"pipeline-request.v2"`なら
`request_v2.read_request_file(source_root)`で`analysis-request.json`を読んだ上で
`request_v2.resolve(explicit, profile, from_file=...)`へ委譲する。同様に
`lipidmix.pipeline.request.merge_updates(request, updates, *, profile=None)`は
`request["schema"] == "pipeline-request.v2"`で`request_v2.merge_updates`へ委譲する
（resumeは`analysis-request.json`を読み直さない——§6.1が定める更新経路は
明示`updates`だけ）。

実効schemaは「`explicit["schema"]`（あれば）、無ければ`analysis-request.json`
自身が宣言する`schema`（あれば）、どちらにも無ければv1」の順で決まる
（`request.py`の`_peek_schema_declared_by_request_file`）。**explicit/file
双方がschemaを省略したときだけがv1**——v1のkey setで一度でも先に検証して
しまうとv2形状のファイルが「未知のキー」として誤って拒否されるため、
このpeekはschemaフィールドの値だけを見て、他のキーの検証は行わない
（不正JSON・非オブジェクトも例外にせず`None`を返し、エラー報告はschema確定後
の本読み込みに一本化する）。ファイル自身がv2を宣言している場合はそれ自体が
明示的な指定であり「省略」ではない——この不変条件はA01としてテストで
固定されている（`test_schema_omission_defaults_to_v1`は明示にもファイルにも
schemaが無い場合、`test_request_file_schema_v2_dispatches_without_explicit_schema_key`
はファイル側だけがv2を宣言する場合をそれぞれ固定する）。

## トップレベル

未知キーは拒否（`PIPELINE_REQUEST_INVALID`）。v1の`method_file` / `lbm_file` /
`polarity` / `measure` / `comparisons`はv2のキー集合に存在しない
——`method_file`/`lbm_file`を指定すると専用のエラーメッセージ
（"profile_fileから解決してください"）、`comparisons`を指定すると専用の
エラーメッセージ（"statisticsを指定してください"）で拒否される。

| フィールド | 型 | 既定 | null可否 | 内容 |
|---|---|---|---|---|
| `schema` | str | `"pipeline-request.v2"` | 不可 | 固定値 |
| `omics` | str | `"metabolomics"` | 不可 | 固定値（他omicsはlipidomics側adapterで解決し、この契約には乗らない） |
| `profile_file` | str | なし（必須） | 不可 | 検証済みprofileファイルへのパス。実解決は呼び出し側 |
| `execution_purpose` | str | `"routine"` | 不可 | `routine`（validated profile必須・上書き範囲は下記「preprocess」参照）／`validation`（draft profileでも実行可・preprocess上書き範囲の制限なし） |
| `target` | str | `"auto"` | 不可 | `auto` / `exploratory` / `differential`。`auto`は`statistics`から算出（下記） |
| `sample_manifest` | str | `null` | **可**（明示解除） | v1と同じ語彙——明示nullは「既定探索を解除し自動一覧生成へ切り替える」 |
| `standard_assays` | object | `{}` | 不可 | `target_id`（`profile.feature_targets`参照）→ 非空のsample_id配列 |
| `feature_bindings` | object | `null` | 不可 | dataset hash・target_idごとの選択。下記「feature_bindings」 |
| `preprocess` | object | `{}` | 可（省略と同義） | `recipe_id`（`profile.matrix_recipes`参照）→ 上書き部分dict。下記 |
| `statistics` | list | 下記フォールバック | 不可 | spec §6.2。下記 |
| `timeout_s` | int | `21600` | 不可（型検査で拒否） | v1と同じ |
| `save_project` | bool | `true` | 不可（型検査で拒否） | v1と同じ |
| `output_root` | str | `null` | 不可 | v1と同じ |
| `keep_extension` | str | `null` | 不可 | v1と同じ（wiff/wiff2共存時の採用形式選択） |

明示nullが許可されるのは`sample_manifest`だけ（v1と同じ語彙）。`preprocess`は
「全体をnull」にしても構わないが、それは「上書き無し」（省略と同義）であって
無効化ではない——他フィールドの明示nullリストには含まれない。

## 値の優先順位

spec §6「値の優先順位はMCP明示値 > analysis-request.json > profile既定値 >
v2既定値」。実際に働くのは次の段:

1. **MCP明示値**（`explicit`にキーがある）
2. **analysis-request.json**（`source_root`直下のファイル。`request_v2.read_request_file`
   がv2 shapeとして検証する——v1の`_TOP_LEVEL_KEYS`ではなくv2自身のキー集合・
   null規則を適用する）
3. **profile既定値**（`statistics`のみ。`profile.analysis_recipe.statistics`が
   非空ならそれを使う）
4. **v2既定値**（本文書の「既定」列、および`statistics`の大域既定PCA）

各実効値の出所は`value_sources`（トップレベルキーごとの辞書）に保存する。
値は`"explicit"` / `"request_file"` / `"default"`のいずれか（`statistics`だけは
`"explicit"` / `"request_file"` / `"profile_default"` / `"v2_default"`の4値）。
`resolve`の結果に`effective_target`（下記）も付与される。

`preprocess`（および他の全トップレベルキー）はv1の`_layer_preprocess`のような
子キー単位の重ね合わせをしない——`explicit`にキーがあればその値を**丸ごと**
採用し、無ければファイルの値を丸ごと採用する（`recipe_id`単位・
`normalize`/`drift_correct`等のフィールド単位のどちらの粒度でも部分マージは
しない）。`explicit`が`preprocess`を1つでも指定したら、ファイル側の
`preprocess`は（別の`recipe_id`を持っていても）丸ごと無視される。

## `statistics`（spec §6.2の discriminated schema）

非空の配列。共通5キー必須:

| フィールド | 型 | 内容 |
|---|---|---|
| `statistic_id` | str | 安全なslug（`^[A-Za-z0-9_-]+$`）。request内で一意（重複は`PIPELINE_REQUEST_INVALID`） |
| `kind` | str | `pca` / `welch` / `anova_tukey` |
| `matrix_recipe_id` | str | `profile.matrix_recipes`に実在するrecipe_id |
| `transform` | str | `none` / `log2` |
| `feature_scope` | object | `{"mode": "all_eligible"}` または `{"mode": "targets", "target_ids": [...]}`（`target_ids`は`profile.feature_targets`参照・非空・重複不可） |

`kind`別の追加キー（未知キーは拒否。省略時はここに示す既定値を埋める）:

| `kind` | 追加キー | 既定 |
|---|---|---|
| `pca` | `scaling`（`none`/`autoscale`）, `n_components`（正整数） | `scaling="autoscale"`, `n_components=2`。`groups`/`alpha`等の他kindキーは拒否 |
| `welch` | `reference_group`, `test_group`（異なる2群・必須）, `q_threshold`（0<x<1）, `log2fc_threshold`（非負有限） | `q_threshold=0.05`, `log2fc_threshold=1.0` |
| `anova_tukey` | `groups`（重複のない3群以上・必須）, `alpha`（0<x<1） | `alpha=0.05` |

### `statistics`省略時のフォールバック

`explicit`にも`analysis-request.json`にも`statistics`が無い場合:

1. `profile.analysis_recipe.statistics`が非空ならそれを検証して使う
   （`value_sources["statistics"] = "profile_default"`）。
2. profileにも無ければ、次の大域既定PCA単独を使う
   （`value_sources["statistics"] = "v2_default"`）:
   ```json
   {"statistic_id": "pca", "kind": "pca", "matrix_recipe_id": "default",
    "transform": "none", "feature_scope": {"mode": "all_eligible"},
    "scaling": "autoscale", "n_components": 2}
   ```
   （`profile.matrix_recipes`は`"default"`キーを必須で持つ——
   [`lcms-profile-v1.md`](lcms-profile-v1.md)参照——ためこの既定は常に解決できる）。

明示`statistics: null`および明示`statistics: []`はどちらも拒否する
（`PIPELINE_REQUEST_INVALID`）——「statisticsは非空の配列」という契約自体は
省略時のフォールバックの有無に関わらず常に成立する。

### `target`と`effective_target`

`target="auto"`の実効値は`statistics`から算出する: pca以外のkind（`welch`/
`anova_tukey`）が1件でもあれば`differential`、pcaだけなら`exploratory`。

- `target="exploratory"`で検定（welch/anova_tukey）を含む`statistics`は拒否。
- `target="differential"`で検定を1件も含まない`statistics`は拒否。

## `preprocess`（matrix recipe override）

`{recipe_id: {normalize?, drift_correct?, filter?, impute?}}`。`recipe_id`は
`profile.matrix_recipes`に実在する必要がある（存在しないrecipe参照は
`PIPELINE_REQUEST_INVALID`）。上書き部分dictは4キーの**部分集合**——
指定したキーだけが対象recipeの値を上書きする。

| フィールド | 型 | 内容 |
|---|---|---|
| `normalize` | str | `none` / `tic` / `median` / `pqn` |
| `drift_correct` | bool | |
| `filter` | object/null | `null`または`{"min_detection_rate": 0..1}` |
| `impute` | str | `none` / `half_min` / `knn` / `column_mean` |

`base`はrecipeの固定値であり上書き対象ではない。上書き後の実効`normalize`が
`base="internal_standard_ratio"`のrecipeで`"none"`以外になる場合は
（`profile_schema`の二重正規化禁止規則と同じ理由で、`execution_purpose`に
関わらず常に）`PIPELINE_REQUEST_INVALID`。

### `execution_purpose`によるroutine許容範囲は未実装（既知のgap）

spec §6・[`lcms-profile-v1.md`](lcms-profile-v1.md)「routine実行が上書き
できる範囲」は、routineの`preprocess`上書きが「証明書の明示許容集合」の
外に出れば`PROFILE_SCOPE_MISMATCH`にすると述べるが、その「明示許容集合」を
表す構造化フィールドは`lcms-profile-validation.v1`証明書schema
（`profile_schema._CERTIFICATE_KEYS`）にもprofile schemaにも存在しない——
Task 1自身がこの節を「将来指針」（未実装の設計メモ）と明記し、実装を
request v2（このモジュール）へ委譲していた。

存在しない契約を肩代わりする独自ルールを発明しない、というcontroller裁定に
従い、**`execution_purpose`による`preprocess`上書きの差別化は現状実装しない**
——`routine`/`validation`のどちらでも同じ構造検証だけを課す。`PROFILE_SCOPE_MISMATCH`
はこのモジュールからは送出されない。担当モジュール・具体的な集合の形は
`task-3-report.md`「Concern 2」としてcontrollerへ報告済み。

## `feature_bindings`（spec §6.2、構造検証のみ）

`{"dataset_hash": <sha256>, "selections": {<target_id>: {"feature_id": <str非空>, "reason": <str非空>}}}`。

| フィールド | 型 | 内容 |
|---|---|---|
| `dataset_hash` | str | 64桁小文字16進のSHA-256 |
| `selections` | object | 非空。`target_id`（`profile.feature_targets`参照）→ `{feature_id, reason}` |

ここで検証するのは**構造だけ**——選択された`feature_id`が実際にそのdatasetで
許容誤差・証拠条件（profileの`feature_targets[target_id].required_evidence`等）
を満たすかどうかの判定（spec §6.2「選択はprofileの許容規則内に限定し、
外れればPROFILE_SCOPE_MISMATCH」）は実データ（`assay-feature-evidence.v1`等）
へのアクセスを要する`resolve_feature_bindings`stage（後続task）の責務であり、
このモジュールは実装しない（`resolve`/`merge_updates`はprofileだけを受け取り、
datasetを受け取らないため判定しようがない）。

初回`resolve`とresumeの`merge_updates`は同じ`_validate_feature_bindings`を
通る——初回指定・resumeで検査の抜け道はない。

## resume（`merge_updates`）

`UPDATABLE = {target, sample_manifest, preprocess, statistics, standard_assays, feature_bindings}`。
これ以外のキー（`profile_file` / `execution_purpose` / `omics` / `schema` /
`timeout_s` / `save_project` / `output_root` / `keep_extension`）を変えようと
すると`NEW_PIPELINE_REQUIRED`——profile・実行目的・上流条件の変更は新しい
pipelineが要る（spec §6.1）。

更新後は`resolve`と同じ検証を通す（`statistics`の重複・target整合・
preprocessのrecipe存在・`feature_bindings`の構造を含め、初回指定とresumeで
検査の抜け道を作らない）。更新していないフィールドの`value_sources`は
元の出所を保持し、`updates`に挙げたキーだけ`"explicit_update"`へ格上げする。
resumeは`analysis-request.json`を読み直さない（更新経路は明示`updates`のみ）。
