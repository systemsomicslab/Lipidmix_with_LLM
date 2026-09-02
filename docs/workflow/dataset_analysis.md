# DatasetState 解析ツール呼び出し連鎖

ARF 経路（`docs/workflow/arf.md`）と同じ純関数を共有している。同じ入力からは
同じ数字が出る。違いは入口（mzTab-M か .arf か）とセッションスロットだけ。

## dataset_preprocess

1. lipidmix/tools/dataset_analysis_tools.py  dataset_preprocess()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_preprocess()
3.    └─ lipidmix/analysis/dataset_analysis.py  build_dataset_pp_inputs()
4.       └─ lipidmix/analysis/preprocessing.py  detect_sample_roles()
5.    └─ lipidmix/analysis/preprocessing.py  preprocess()
6.    └─ lipidmix/analysis/preprocessing.py  drop_samples_by_role()
7.    └─ lipidmix/analysis/preprocessing.py  detect_qc_strata()

## dataset_pca

1. lipidmix/tools/dataset_analysis_tools.py  dataset_pca()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_pca()
3.    └─ lipidmix/analysis/pca.py  run_pca()

## dataset_differential

1. lipidmix/tools/dataset_analysis_tools.py  dataset_differential()
2. └─ lipidmix/analysis/dataset_analysis.py  run_dataset_differential()
3.    └─ lipidmix/analysis/differential.py  two_group_test()
4.    └─ lipidmix/analysis/differential.py  add_fdr()
5.    └─ lipidmix/analysis/differential.py  summarize_two_group()
6.    └─ lipidmix/analysis/differential.py  volcano_data()

## dataset_export_differential

1. lipidmix/tools/dataset_analysis_tools.py  dataset_export_differential()
2. └─ lipidmix/analysis/export_contract.py  build_meta()
3. └─ lipidmix/analysis/export_contract.py  format_row()
