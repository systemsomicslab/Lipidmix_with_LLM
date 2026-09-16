# ワークフロー: LC–MS メタボロミクス v2

検証済み profile（`lcms-profile.v1`）を前提に、注入ごとの測定証拠・feature binding・
内部標準比・固定母集団 QC・v2 統計までを回すための開発中の経路。
**2026-09-16時点では公開 `pipeline_plan` / `pipeline_run` のv2上流は未接続**で、
`PIPELINE_V2_UPSTREAM_UNAVAILABLE`で停止する。routineは独立した証明書実測hashの
受付も未接続のため`PROFILE_VALIDATION_INVALID`で停止する。
この文書の工程図は実装予定を含む。`dataset_statistic`は既にセッションに存在する
解析行列を対象にした単体ツールであり、現状の公開入口だけでその行列を作れるわけではない。
検証範囲と残タスクは[実装監査](../superpowers/plans/2026-09-16-validated-lcms-metabolomics-audit.md)を参照。

v1（`dataset_differential` ほか、[dataset_analysis.md](dataset_analysis.md)）とは
**数値契約が違う**。同じ「log2FC」という名前でも、v1 は `log2(x + pseudo_count)` 空間の
平均差、v2 は統計変換前の算術平均比の log2（`effect_size_definition =
log2_arithmetic_mean_ratio`）。v1 の既定値・契約は一切変更していない。

## dataset_statistic

名指しした解析行列（`analysis-matrix.v1`）に対して統計を1件実行する。

- **前提** — `session.dataset`（`dataset_load`）と、`ds.analysis_matrices` に登録済みの
  解析行列。行列は pipeline の `preprocess` / `qc_processed` 工程が作る。単体ツールで
  行列を組む入口は置いていない——recipe・binding・注入証拠が揃って初めて意味のある
  行列になるため。どちらが欠けても `missing_state` 封筒
  （`required_tools` に `dataset_load` / `pipeline_run`）を返す。
- **状態変更** — `session.dataset.results["stat_<statistic_id>"]` に結果全量を置く。
  戻り値には要約だけを載せる。
- **呼び出し連鎖**

```
1. lipidmix/tools/dataset_analysis_tools.py  dataset_statistic()
2. └─ lipidmix/analysis/dataset_service.py  statistic_dataset()
3.    └─ lipidmix/analysis/statistics_v2.py  run_statistic()
4.       └─ lipidmix/analysis/statistics_v2.py  transform_values()
5.       └─ lipidmix/analysis/sample_manifest.py  select_statistical_samples()
6.       └─ lipidmix/analysis/sample_manifest.py  validate_independent_samples()
7.       └─ lipidmix/analysis/differential.py  welch_t()
8.       └─ lipidmix/analysis/differential.py  bh_fdr()
9.       └─ lipidmix/analysis/multigroup.py  test_feature()
10.      └─ lipidmix/analysis/statistics_v2.py  arithmetic_log2fc()
11.      └─ lipidmix/analysis/pca.py  run_pca()
```

`matrix_result_id` が `ds.analysis_matrices` に無ければ `ANALYSIS_RESULT_NOT_FOUND` で
止まる。**近い行列で代用しない**——recipe 違いの行列が2本ある前提の設計で、
どちらの数字かを言えない結果を返すくらいなら止めるほうが安全。

## pipeline の v2 工程（参考）

公開受付接続後にv2 workerが回す予定の順序（handler の実体は
`lipidmix/pipeline/metabolomics_handlers.py`）:

```
prepare_inputs → execute_console → validate_outputs → load_dataset
  → resolve_metadata → load_assay_evidence → resolve_feature_bindings
  → qc_raw → preprocess → qc_processed → statistics:<id> → export:<id> → report
```

- `load_assay_evidence` — `.arf` の `AlignedPeakProperties` から注入ごとの RT/m/z を
  読む（`lipidmix/analysis/assay_evidence.py` `build_assay_evidence()`）。取得できない
  場合も理由 JSON を残して先へ進む。
- `resolve_feature_bindings` — profile の `feature_targets` をこのバッチの feature へ
  対応付ける（`lipidmix/analysis/feature_bindings.py` `bind_features()`）。0件・複数件は
  `FEATURE_BINDING_UNRESOLVED` で停止し、自動では選ばない。
- `qc_raw` — filter 前に QC 評価集合を固定する（`lipidmix/analysis/assay_qc.py`
  `evaluate_qc()`）。
- `preprocess` — recipe ごとに行列を作る（`lipidmix/analysis/matrix_state.py`
  `make_matrix()`）。補完はしない。
- `qc_processed` — 処理後 QC を評価してから補完する（`finalize_matrix()`）。QC で落ちた
  feature は eligibility にだけ反映し、QC は再集計しない。
- `report` — 実行・QC・解析の3軸を分けて書く（`lipidmix/pipeline/report.py`
  `analysis_status()`）。

工程ごとの停止理由・再開の規則は
[../superpowers/specs/2026-09-15-validated-lcms-metabolomics-design.md](../superpowers/specs/2026-09-15-validated-lcms-metabolomics-design.md)
（spec §6.2・§8〜§11）が正準。
