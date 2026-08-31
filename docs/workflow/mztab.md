# mzTab-M ツール呼び出し連鎖

## dataset_load

1. lipidmix/tools/mztab_tools.py dataset_load()
2. └─ lipidmix/mztab/reader.py parse_mztab()
3. └─ lipidmix/mztab/validator.py validate_mztab()
4. └─ lipidmix/mztab/validator.py detect_quantification_measure()
5. └─ lipidmix/mztab/dataset_state.py build_dataset_state()
6.    └─ lipidmix/mztab/reader.py extract_abundance_matrix()
7.    └─ lipidmix/mztab/identity.py derive_inchikey()

## dataset_status

1. lipidmix/tools/mztab_tools.py dataset_status()
