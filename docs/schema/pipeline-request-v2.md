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

> このモジュールもprofile_schema同様にファイルを読まない。`profile_file`が指す
> 実体（profile本体）の読込・検証・hash計算・`analysis-request.json`の読込は
> 呼び出し側（`lipidmix.pipeline.request.resolve_request`のschema dispatch、
> または後続タスクの`pipeline_plan`/`pipeline_run`）の責務で、ここでは
> 既に検証済みのprofile dict（`profile_schema.validate_profile`の戻り値相当）を
> 受け取って構造的に検証するだけ。

## schema dispatch（v1との共存）

`lipidmix.pipeline.request.resolve_request(source_root, explicit, *, profile=None)`が
`explicit["schema"] == "pipeline-request.v2"`を検出した時点で
`request_v2.resolve(explicit, profile)`へ丸ごと委譲する。同様に
`lipidmix.pipeline.request.merge_updates(request, updates, *, profile=None)`は
`request["schema"] == "pipeline-request.v2"`で`request_v2.merge_updates`へ委譲する。

**schemaを省略した入力は常にv1として解決する**（`explicit`に`schema`キーが
無ければv1のまま——v2を暗黙に推測しない。この不変条件はA01としてテストで
固定されている）。

**既知の限界（本モジュールの範囲外）**: このdispatchは`explicit`（MCPが直接
渡した値）だけを見る。`source_root`直下の`analysis-request.json`がv2形状を
宣言していても、v1の`read_request_file`はv1のキー集合しか知らないため
そちらの層でv2 schemaは検出されない——v2の`analysis-request.json`層
（spec §6の4段優先順位のうち「analysis-request.json」の段）は本タスクでは
未配線。実際に効くのは「MCP明示値 > profile既定値 > v2既定値」の3段。

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
v2既定値」。前述の既知の限界により、実際に働くのは次の3段:

1. **MCP明示値**（`explicit`にキーがある）
2. **profile既定値**（`statistics`のみ。`profile.analysis_recipe.statistics`が
   非空ならそれを使う）
3. **v2既定値**（本文書の「既定」列、および`statistics`の大域既定PCA）

各実効値の出所は`value_sources`（トップレベルキーごとの辞書）に保存する。
値は`"explicit"` / `"default"` のいずれか（`statistics`だけは
`"explicit"` / `"profile_default"` / `"v2_default"`の3値）。`resolve`の結果に
`effective_target`（下記）も付与される。

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
（`profile_schema`の二重正規化禁止規則と同じ理由で）`PIPELINE_REQUEST_INVALID`。

### `execution_purpose`によるroutine許容範囲

`execution_purpose="routine"`（既定）では、上書き後の各フィールド値は
**`profile`自身が持ついずれかの`matrix_recipes`エントリに既に現れる値**
でなければならない。そうでない場合は`PROFILE_SCOPE_MISMATCH`。
`execution_purpose="validation"`ではこの制限を外す（新しい条件を検証する
ための目的だから）。

> **この解釈はcontroller裁定を要する未確定事項として実装した。** spec §6・
> [`lcms-profile-v1.md`](lcms-profile-v1.md)「routine実行が上書きできる範囲」は
> 「証明書の明示許容集合」でroutineの許容範囲を判定すると述べるが、
> `lcms-profile-validation.v1`（証明書schema。`profile_schema._CERTIFICATE_KEYS`）
> にはそのような許容集合を表すフィールドが存在せず、Task 1もこの実装を
> 明示的に本タスク（request v2）へ委譲していた。ここでは「profile自身が
> 既に使っている値の外に出ない」ことを機械的な代替基準として採用した——
> 妥当性は`docs/task.md`/`docs/HISTRY.md`（コントローラの記録層）で再確認
> されたい。実際の証明書ベースの許容判定が別途必要になった場合、この関数
> （`request_v2._check_routine_scope`）を差し替える。

## resume（`merge_updates`）

`UPDATABLE = {target, sample_manifest, preprocess, statistics, standard_assays}`。
これ以外のキー（`profile_file` / `execution_purpose` / `omics` / `schema` /
`timeout_s` / `save_project` / `output_root` / `keep_extension`）を変えようと
すると`NEW_PIPELINE_REQUIRED`——profile・実行目的・上流条件の変更は新しい
pipelineが要る（spec §6.1）。

更新後は`resolve`と同じ検証を通す（`statistics`の重複・target整合・
preprocessのrecipe存在・routine許容範囲を含め、初回指定とresumeで
検査の抜け道を作らない）。更新していないフィールドの`value_sources`は
元の出所を保持し、`updates`に挙げたキーだけ`"explicit_update"`へ格上げする。

`feature_bindings`（`resolve_feature_bindings`stageの候補選択・理由。spec
§6.2）は`pipeline-request.v2`自体のフィールドではない——専用のresume payload
としてTask 8以降が扱う別の仕組みであり、本契約の対象外。
