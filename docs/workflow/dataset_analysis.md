# DatasetState 解析ツール呼び出し連鎖

ARF 経路（`docs/workflow/arf.md`）と同じ純関数を共有している。同じ入力からは
同じ数字が出る。違いは入口（mzTab-M か .arf か）とセッションスロットだけ。

## dataset_preprocess

1. lipidmix/tools/dataset_analysis_tools.py  dataset_preprocess()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_preprocess()
3.    └─ lipidmix/analysis/dataset_analysis.py  build_dataset_pp_inputs()
4.       └─ lipidmix/analysis/preprocessing.py  detect_sample_roles()
5.    └─ lipidmix/analysis/dataset_analysis.py  _apply_detection_filter()
6.       └─ lipidmix/analysis/preprocessing.py  detection_rates()
7.    └─ lipidmix/analysis/preprocessing.py  preprocess()
8.    └─ lipidmix/analysis/preprocessing.py  drop_samples_by_role()
9.    └─ lipidmix/analysis/preprocessing.py  detect_qc_strata()

手順 5 は前処理（手順 7）より**前**に来る。正規化・補完のあとでは gap-fill セルが
実測値と区別できなくなり、何を根拠に特徴を残したかが言えなくなるため。
`min_detection_rate > 0` なのに `ds.detected_mask` が無い場合は `bad_request` で
拒否する（検出状態が「無い」のと「全部未検出」は解釈が正反対で、0 扱いで通すと
gap-fill だけの特徴を実測として数えた行列が黙って下流に流れる）。

## dataset_pca

1. lipidmix/tools/dataset_analysis_tools.py  dataset_pca()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_pca()
3.    └─ lipidmix/analysis/pca.py  run_pca()

## dataset_differential

1. lipidmix/tools/dataset_analysis_tools.py  dataset_differential()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_differential()
3.    └─ lipidmix/analysis/differential.py  check_confounding()
4.    └─ lipidmix/analysis/differential.py  two_group_test()
5.    └─ lipidmix/analysis/differential.py  add_fdr()
6.    └─ lipidmix/analysis/differential.py  summarize_two_group()
7.    └─ lipidmix/analysis/differential.py  volcano_data()

## dataset_export_differential

1. lipidmix/tools/dataset_analysis_tools.py  dataset_export_differential()
2. └─ lipidmix/analysis/export_contract.py  is_significant()
3. └─ lipidmix/analysis/export_contract.py  build_meta()
4. └─ lipidmix/analysis/export_contract.py  format_row()
