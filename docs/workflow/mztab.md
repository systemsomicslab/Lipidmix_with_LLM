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
11. └─ lipidmix/mztab/evidence.py attach_to_dataset()
12.    └─ lipidmix/mztab/evidence.py arf_candidates()
13.    └─ lipidmix/mztab/evidence.py load_arf_evidence()
14.       └─ lipidmix/arf/reader.py deserialize()
15.       └─ lipidmix/mztab/evidence.py normalize_arf_spots()
16.       └─ lipidmix/mztab/evidence.py build_evidence()
17.    └─ lipidmix/mztab/evidence.py apply_evidence()

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
14. └─ lipidmix/mztab/evidence.py attach_to_dataset()

`_select_primary_entry()` は候補が一意に決まらなければ読まずに停止する
（`QUANTIFICATION_CONFLICT` / `POLARITY_MISMATCH` / `AMBIGUOUS_PRIMARY_MZTAB`）。
どのファイルを読むかは解析結果そのものを変えるため、辞書順にも LLM にも決めさせない。

job_path 経路では手順 14 が `artifact_paths` を入れ終えた**後**に来る。handoff が
記録した `peak_matrix_source` を `.arf` 候補の先頭に使えるのはその時点以降だけ。

### evidence sidecar（検出状態）の取り込み

mzTab-M の `abundance_assay[N]` は非ゼロでも実測ピークか gap-fill 補間かを区別しない。
実データ（60 サンプル × 714 特徴）では**セルの 70.0% が gap-fill** だったので、
非ゼロを検出と数えると検出率を 3 倍以上に過大評価する。隣接する `.arf` は
(スポット × サンプル) の粒度で `MasterPeakID < 0` = gap-fill を持つので、そこから補う。

接合は**名前ではなく数値で検証する**。`.arf` のスポット順が mzTab の `SMF_ID` と
一致することを、スポット数の一致と全特徴の m/z 差（既定 0.01 Da 以内）で確かめる。
実データでは位置一致で最大 6.7 mDa、1 つずらすと 87 Da に爆発するので偶然は起きない。
確認できなければ取り込まず、`feature_qc` に理由を残して warning を出す
——誤接合は検出/未検出を特徴間で入れ替えたまま静かに嘘をつくため。

結果は `ds.detected_mask`（(特徴 × サンプル) の bool 行列）と `ds.feature_qc`（要約）。
TSV は書かない（消費者のいない `feature-qc.tsv` を 2026-09-03 に廃止した経緯）。

## dataset_status

1. lipidmix/tools/mztab_tools.py dataset_status()
2. └─ lipidmix/tools/mztab_tools.py _samples_tsv()
3.    └─ lipidmix/analysis/preprocessing.py detect_sample_roles()
4. └─ lipidmix/tools/mztab_tools.py _detection_summary()
