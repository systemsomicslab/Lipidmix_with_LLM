# mzTab-M ツール呼び出し連鎖

## dataset_load

mztab_path 経路（直接指定）:
1. lipidmix/tools/mztab_tools.py dataset_load()
2. └─ lipidmix/mztab/reader.py parse_mztab()
3. └─ lipidmix/mztab/validator.py validate_mztab()
4. └─ lipidmix/mztab/validator.py detect_quantification_measure()
5. └─ lipidmix/mztab/dataset_state.py build_dataset_state()
6.    └─ lipidmix/mztab/reader.py extract_abundance_matrix()
7.    └─ lipidmix/mztab/dataset_state.py _index_sme_rows()
8.    └─ lipidmix/mztab/dataset_state.py _best_evidence()
9.    └─ lipidmix/mztab/identity.py derive_inchikey()
10.   └─ lipidmix/mztab/dataset_state.py _resolve_sample_names()

job_path 経路（analysis-job.json の宣言 polarity + measure で正準を選ぶ）:
1. lipidmix/tools/mztab_tools.py dataset_load()
2. └─ lipidmix/tools/mztab_tools.py _load_from_job()
3.    └─ lipidmix/handoff/schema.py AnalysisJob.load()
4.    └─ lipidmix/tools/mztab_tools.py _select_primary_entry()
5.    └─ lipidmix/mztab/reader.py parse_mztab()
6.    └─ lipidmix/mztab/validator.py validate_mztab()
7.    └─ lipidmix/mztab/validator.py detect_quantification_measure()
8.    └─ lipidmix/mztab/dataset_state.py build_dataset_state()
9.       └─ lipidmix/mztab/reader.py extract_abundance_matrix()
10.      └─ lipidmix/mztab/dataset_state.py _index_sme_rows()
11.      └─ lipidmix/mztab/dataset_state.py _best_evidence()
12.      └─ lipidmix/mztab/identity.py derive_inchikey()
13.      └─ lipidmix/mztab/dataset_state.py _resolve_sample_names()

`_select_primary_entry()` は候補が一意に決まらなければ読まずに停止する
（`QUANTIFICATION_CONFLICT` / `POLARITY_MISMATCH` / `AMBIGUOUS_PRIMARY_MZTAB`）。
どのファイルを読むかは解析結果そのものを変えるため、辞書順にも LLM にも決めさせない。

## dataset_status

1. lipidmix/tools/mztab_tools.py dataset_status()
2. └─ lipidmix/tools/mztab_tools.py _samples_tsv()
3.    └─ lipidmix/analysis/preprocessing.py detect_sample_roles()
