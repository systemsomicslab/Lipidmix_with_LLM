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

job_path 経路（analysis-job.json から自動選択）:
1. lipidmix/tools/mztab_tools.py dataset_load()
2. └─ lipidmix/tools/mztab_tools.py _load_from_job()
3.    └─ lipidmix/handoff/schema.py AnalysisJob.load()
4.    └─ lipidmix/mztab/reader.py parse_mztab()
5.    └─ lipidmix/mztab/validator.py validate_mztab()
6.    └─ lipidmix/mztab/validator.py detect_quantification_measure()
7.    └─ lipidmix/mztab/dataset_state.py build_dataset_state()
8.       └─ lipidmix/mztab/reader.py extract_abundance_matrix()
9.       └─ lipidmix/mztab/dataset_state.py _index_sme_rows()
10.      └─ lipidmix/mztab/dataset_state.py _best_evidence()
11.      └─ lipidmix/mztab/identity.py derive_inchikey()

## dataset_status

1. lipidmix/tools/mztab_tools.py dataset_status()
