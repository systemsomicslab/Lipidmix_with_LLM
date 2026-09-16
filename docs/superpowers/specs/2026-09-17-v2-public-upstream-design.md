# v2 公開入口と上流接続 設計（S3 / 旧 R1）

対象: `pipeline_plan` / `pipeline_run` へ v2 要求（`pipeline-request.v2`）を渡したときに、
profile だけを情報源として入力を固定し、Console を起こし、全工程を完走させる経路。

前提文書: [検証済みLC–MSメタボロミクス設計](2026-09-15-validated-lcms-metabolomics-design.md)（§4〜6, §14）、
[実装監査と残タスク](../plans/2026-09-16-validated-lcms-metabolomics-audit.md)（R1）。
方針転換の経緯（対話利用の優先、S1→S2→S3 の順）は `docs/HISTRY.md` 2026-09-16 の項。

## 1. 目的と完成条件

公開入口から v2 要求を渡して、**draft profile + `execution_purpose="validation"`** で
全工程（`prepare_inputs` → `execute_console` → … → `report`）が完走し、その結果を
`dataset_load(pipeline_path=...)` で対話セッションへ載せられる状態にする。

完成条件は次の 5 つがすべて成り立つこと。

1. 公開入口 `pipeline_run` に v2 要求を渡すと、profile 由来の入力配置計画で run が起動する。
2. profile を伴わない v2 要求は、v1 の経路へ落ちずに停止する。
3. 起こした Console ジョブが `analysis-job.v3` になり、`profile_snapshot` を持つ。
4. binding 訂正による再開で Console を起こし直さない。
5. `dataset_load(pipeline_path=...)` が、その run の dataset・試料対応表・binding・解析行列を載せる。

**実 Console 接続と科学的 profile 検証は本設計の対象外**（R5 = 旧 Task 15/16）。
method 一式が未確定のため、本設計の検証はすべて fake Console と合成入力で行う。
合成入力での完走を「ソフトウェア完成」「実 Console 合格」と表記しない。

## 2. 範囲

### 2.1 含むもの

- `lipidmix/pipeline/inputs.py` に profile 由来の入力配置計画を作る関数を追加する。
- `lipidmix/pipeline/service.py` の受付分岐（`PIPELINE_V2_UPSTREAM_UNAVAILABLE` の解除）。
- `_handle_upstream` の schema 分岐（omics・polarity・measure・method・profile snapshot）。
- `lipidmix/pipeline/recovery.py` の `_console_supervision_state` の stage ID 直書きを直す。
- 公開入口から fake Console で回す E2E 試験。

### 2.2 含まないもの

実 Console の起動、`console/compatibility.py`、証明書の実測証拠受付（R2）、
`_detected_mask` の unknown 表現（R3）、統計成果物とレポートの残契約（R4）、
複数 batch/pool のドリフト対応、raw 形式の新規対応。

`execution_purpose="routine"` は引き続き `PROFILE_VALIDATION_INVALID` で停止する
（R2 未接続。本設計は `validation` 経路だけを開ける）。

## 3. 現状と欠けているもの

| 箇所 | 現状 |
|---|---|
| `_handle_prepare_inputs_v2` | profile snapshot の固定は実装済み。raw の実配置は `inputs` に `raw_stat` があるときだけ実行する（＝受付が v1 形式の計画を作ったときだけ） |
| `_prepare_run` | v2 要求を `PIPELINE_V2_UPSTREAM_UNAVAILABLE` で停止させている |
| `_handle_upstream` | `omics="lipidomics"` 固定。polarity・measure・method を v1 の `inputs` snapshot から取る。`profile_snapshot` を job に載せない |
| `recovery._console_supervision_state` | `record["stages"]["upstream"]` を直書き。v2 record では常に `verified=False` |
| `recovery._upstream_stage_id` | v1/v2 の振り分けは実装済み（`prepare_resume` は使っている） |
| `AnalysisJob` | `profile_snapshot` を持つジョブは自動で `analysis-job.v3` として書かれる |

## 4. 入力配置計画（`plan_from_profile`）

### 4.1 契約

```python
def plan_from_profile(source_root: Path, request: dict, profile: dict) -> dict
```

`inspect_inputs` と**同じ形の plan dict** を返す。下流（`stage_inputs`・`_plan_fingerprint`・
`_handle_prepare_inputs_v2`）は変更しない。

解決と hash 照合をここでやり直さない。`profiles.resolve_profile_inputs(profile,
profile_dir, raw_root=source_root)` が method・依存・実行体・raw の実在と hash 一致を
既に検証しているので、本関数はそれを呼んで**形を移す**だけにする。

| plan のキー | v2 での出どころ |
|---|---|
| `source_root` | 引数の `source_root`（絶対化） |
| `selected_format` / `entries` / `raw_stat` / `companions` | v1 と同じ raw 列挙部品を共有する。raw 形式は profile が宣言しないので、フォルダから解決してよい（**データ由来**であって method 由来ではない） |
| `method.source_path` / `method.sha256` | `profile.processing.method_path` / `method_sha256`（解決済みの絶対パス） |
| `method.effective_relative_path` / `effective_sha256` | `None`（`stage_inputs` が実効メソッドを書いた後に埋める既存の流れに従う） |
| `method.overrides` | profile の各依存を `{method_key: 解決済み絶対パス}` として載せる。v1 が LBM 1 件に使っていた任意キー dict をそのまま一般化する |
| `lbm` | profile に LBM 依存があればその 1 件、無ければ `{"path": None, "sha256": None}` |
| `exe` | `profile.software.executable_path` / `executable_sha256` |
| `polarity` | `{"value": profile.acquisition.polarity, "source": "profile"}` |
| `unverified` | raw から極性を検証していないことを理由として残す |

### 4.2 禁止事項

- **環境設定の実行体へフォールバックしない。** `_resolve_exe_path()`（`console.runner.get_exe_path`）は
  v1 経路だけが呼ぶ。profile が実行体を宣言していなければ `PROFILE_INCOMPLETE` で止める。
- **method をフォルダから推定しない。** `_resolve_method` / `_resolve_lbm_pinned` /
  `_resolve_polarity` は v2 経路から呼ばない。
- **LBM を必須にしない。** 必要な依存の種類は profile adapter の allowlist が決める。

## 5. 受付の分岐

`_prepare_run` は要求の schema で計画の作り方を選ぶ。

```
resolve_request → is_v2_request?
  ├─ yes → profile 必須 → plan_from_profile(source_root, request, profile)
  └─ no  → _resolve_exe_path() → inspect_inputs(source_root, request, exe_path=...)
→ _plan_fingerprint（共通） → create_run
```

- v2 要求で profile が解決できない場合は `PIPELINE_REQUEST_INVALID` で止める。
  **v1 経路へ落とさない**——落とすと profile 以外の method・LBM・実行体を黙って採用する。
  これは `PIPELINE_V2_UPSTREAM_UNAVAILABLE` を置いた理由そのものなので、停止を外すのと
  同じ変更で「落ちないこと」を固定する試験を置く。
- `_plan_fingerprint` は plan の形が同じなので共通のまま使う。

## 6. Console 起動（`_handle_upstream`）

schema で分ける値は 4 つだけで、supervise の呼び方・attempt ディレクトリの規約・
`write_supervision_inputs` の内容は共通のまま。

| job のフィールド | v1 | v2 |
|---|---|---|
| `omics` | `"lipidomics"` 固定 | 要求の `omics`（現状 `"metabolomics"`） |
| `polarity` | `inputs.polarity.value` | 同左（v2 の plan では profile 由来の値が入っている） |
| `measure` | `request["measure"]` | `profile.processing.measure` |
| `method_file` | `inputs.method` の実効パス | 同左 |
| `profile_snapshot` | `None` | 保存済み `profile` 成果物の `data["snapshot"]` |

`profile_snapshot` を載せると `AnalysisJob.save` が `analysis-job.v3` を選ぶ。新しい版管理は不要。

snapshot は `context["runtime"]` からではなく**保存済みの `profile` 成果物から読む**。
再開で `prepare_inputs` が skip されると `runtime` は空で、`runtime` を信じると
再開後の job だけ snapshot を失う。読み取りは `store.read_result_data` を使う。

## 7. 再開の判定（既存バグ）

`recovery._console_supervision_state` は `verified` を `record["stages"]["upstream"]` から
読んでいる。v2 の record に `upstream` stage は無いので常に `verified=False` になり、
`execute_console` が succeeded として永続化されていても、終了証跡が読めない場合に
`EXECUTION_UNRESOLVED` を投げる。

```python
verified = record["stages"].get(_upstream_stage_id(record), {}).get("status") == "succeeded"
```

同モジュール内の `prepare_resume` は既に `_upstream_stage_id(record)` を使っており、
この 1 箇所だけが取り残されている。再現試験を先に置いてから直す。

## 8. 誤り時の停止条件

| 条件 | コード | 出どころ |
|---|---|---|
| v2 要求に profile が無い／解決できない | `PIPELINE_REQUEST_INVALID` | 本設計で追加 |
| 必須依存の実ファイルが無い | `PROFILE_INCOMPLETE` | `profiles._resolve_dependency`（既存） |
| 宣言した hash と実ファイルが一致しない | `INPUT_CHANGED` | `profiles._verify_pinned_file`（既存） |
| `execution_purpose="routine"` | `PROFILE_VALIDATION_INVALID` | 既存。R2 未接続のため本設計では変えない |

実行体を profile が宣言していない場合は `validate_profile` が
`software.executable_path` を必須として扱うため、profile 読込の時点で
`PROFILE_INVALID` になる。受付で改めて検査を足さない（同じ規則を 2 箇所に書かない）。

いずれも起動前に止める。Console を起こしてから気付く形にしない。

## 9. 検証

### 9.1 受け入れ条件

| ID | 条件 |
|---|---|
| S3-01 | 公開入口から v2 要求で run が起動し、fake Console で全 10 工程 + 統計 + report が完走する |
| S3-02 | profile を伴わない v2 要求は `PIPELINE_REQUEST_INVALID` で止まり、`inspect_inputs` を呼ばない |
| S3-03 | v2 経路は環境設定の実行体（`console.runner.get_exe_path`）を参照せず、profile が宣言した実行体を使う。環境側に別の実行体があっても採用しない |
| S3-04 | 起動した job が `analysis-job.v3` で、`profile_snapshot` を持つ |
| S3-05 | 再開で `prepare_inputs` が skip されても、job の `profile_snapshot` が失われない |
| S3-06 | binding 訂正の再開で Console を起こし直さない（起動回数で直接確認する） |
| S3-07 | v2 record で `execute_console` が succeeded なら、終了証跡が読めなくても `EXECUTION_UNRESOLVED` にしない |
| S3-08 | raw または依存の hash を改変すると、Console 起動前に止まる |
| S3-09 | 完走した run を `dataset_load(pipeline_path=...)` で対話セッションへ載せられる |
| S3-10 | v1（lipidomics）の経路の挙動が変わらない（既存試験が緑のまま） |

### 9.2 試験の作り方

- fake Console は `tests/pipeline_fixtures.py` の `fake_console_command` / `use_fake_console`
  を流用する（v1 の実プロセス試験が既に使っている）。新しい偽 Console を作らない。
- 既存の `MetabolomicsHarness` は `run_engine` 直叩きなので置き換えない。**受付から起こす**
  経路を別に用意し、両方を残す（工程単体の試験と、入口から通す試験は別の目的）。
- exit 0 だけを成功と読まない。生成物の存在と hash、job の schema、stage の状態を見る。

## 10. 未解決・持ち越し

- profile が raw 形式を宣言しないため、v2 でも raw 形式はフォルダから解決する。
  profile に宣言を足すかは R5（実データで形式が確定してから）で判断する。
- `measure` の出どころが v1（要求）と v2（profile）で違う。v2 の要求に `measure` を
  書けないことは `request_v2` の `_TOP_LEVEL_KEYS` が既に固定している。
- 実 Console 接続（R5）で、本設計の plan が実際の MS-DIAL 引数として通るかを再確認する。
  合成入力での完走はその保証にならない。
