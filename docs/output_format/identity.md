# 同定信頼度・標準化

名前正規化（GOSLIN）、参照表マッピング、MSI レベル推定、`verify_peak_annotation` の材料。

> 先に `lipidmix://docs/output-format`（共通核）を読むこと。行・列の粒度、脂質名文法、必須注意事項はそちらで定義され、ここでは繰り返さない。節番号は分割前の通し番号。

## 12. 同定信頼度・標準化（P2c）

`lipid_identity.py`（MCP非依存の純ロジック層、**完全オフライン**）と `peak_verification.py` の拡張が、脂質同定名の標準化と信頼度レベルの推定を担う。外部識別子の取得はネットワークを一切使わず、`pygoslin`（同梱・純Python）と同梱 TSV 表のみで行う。既存ツール・既定挙動・`verify_peak_annotation` の既存キーは不変で、新データは新ブロックに追加する。

### 12.1 GOSLIN 名正規化（`normalize_lipid_name`）

`pygoslin` で脂質ショートハンド名を正規化し、`normalized`（正規化名）/ `level`（構造レベル: SPECIES/MOLECULAR_SPECIES など）/ `lipid_maps_category` / `parse_ok`（失敗時 False＋`error`）/ `stripped`（下記の前処理をしたか）を返す。`pygoslin` 未導入や解析不能でも例外を投げず `parse_ok=False` を返す（グレースフルデグレード）。

**MS-DIAL 限定子接頭辞の除去**: MS-DIAL は Name に信頼度の限定子（`"no MS2: "` / `"low score: "` / `"w/o MS2:"` 等）を前置し、複数候補を `|` で連結する。解析前にこれら接頭辞と `|` 以降を除去して単一の species 表記に整える（除去した場合 `stripped=True`）。この修正で正規化名の付与が増える。`"RIKEN N-VS1 ID-…"` 等の真の未同定名は接頭辞除去後も解析不能のまま（`parse_ok=False`）で正しい。

**プラズマローゲン注意**: pygoslin は species レベルで `PC P-34:0` を `PC O-34:1` に**同一化**する（P-/O- エーテルの曖昧性）。P- と O- を区別したい場合は正規化名ではなく `peak_verification.ether_caveats()` の caveat（[[pe-p-vs-pe-o-annotation]] / [[plasmalogen-oxidation]] に直結）に依拠すること。

### 12.2 同梱マッピング表（`load_reference_tables` / `map_to_reference`）

`reference/lipidmaps_classes.tsv`（クラス→LIPID MAPS カテゴリ/メインクラス）と `reference/refmet_map.tsv`（クラス→RefMet 名）は**キュレート済みの部分集合**（一般的な脂質クラスを網羅）。クラストークンで写像し、`matched`（bool）/ `lipid_maps_category` / `lipid_maps_main_class` / `refmet_name` / `caveat` を返す。表に無いクラスは `matched=False`＋caveat「同梱マッピング表に無いため ID 未付与」を返し、**推測はしない**。

### 12.3 MSI レベル推定（`msi_level`、ヒューリスティック）

決定論的シグナル（名称の有無・MS/MS取得・精密質量誤差バンド・アダクト整合バンド）を組み合わせて MSI 同定信頼度を推定する。`level`（2/3/4）/ `label` / `rationale` / `heuristic=True` を返す。

- **Level 2**（putative annotated compound）: 名称あり＋MS/MS取得＋精密質量整合（PASS）＋アダクト非FAIL。
- **Level 3**（putative class-level）: クラス（ontology）は判別できるが上記を満たさない。
- **Level 4**（unknown）: 名称・クラスとも無し。
- **Level 1（標準品照合）は決して主張しない**。返り値の `heuristic=True` が示すとおり、これは決定論的推定であって同定の確定ではない。

### 12.4 統合ツール

- `verify_peak_annotation` のドシエに `identity_normalization` ブロック（`goslin` / `reference` / `msi` / `class_token`）を追加。既存の `analytical_checks`（精密質量誤差・アダクト整合・**MS/MS 証拠**）から算出したバンドを MSI 推定に流用する（質量・アダクトロジックの二重化を回避）。
- **MS/MS 証拠 `analytical_checks.msms`**（`peak_verification.msms_evidence`）。`band` は3値で、実スペクトルと取得フラグを峻別する:

  | `band` | `source` | 意味 | MSI への効き方 |
  |---|---|---|---|
  | `PASS` | `spectrum` | 実スペクトルあり（`.dcl` 由来）。`top_fragments` に強度上位が入る | Level 2 の根拠として有効。rationale に「実 MS/MS スペクトル確認」と明記 |
  | `FLAG_ONLY` | `flag` | `has_msms` は立つがスペクトル本体が無い | Level 2 にはなるが **rationale で「根拠が弱い」と開示**し、`dcl_parser` / `dcl_find_msms` での確認を促す。caveat にも出る |
  | `ABSENT` | `null` | フラグもスペクトルも無い | Level 2 に到達しない |

  `n_peaks` は間引き前の元本数、`top_fragments` は強度降順の上位5本（`[[mz, intensity], ...]`）。`llm_decision.deterministic_summary` にも `msms=<band>` が出る。**`FLAG_ONLY` を `PASS` と同等に扱わないこと**——`.dcl` を読んでいないだけかもしれず、「MS/MS で裏が取れている」とは言えない。
- `arf2_annotate_identities(file_path=None, max_rows=50)`: ARF2 スポットカタログの注釈を一括で正規化・ID/レベル付与し、上位 `max_rows` 件を返す。**ARF2 には MS/MS 取得フラグ・精密質量誤差が無いため MSI は保守的にクラス上限で評価**（`has_msms=False`、バンド UNKNOWN）。より確度の高い MSI 評価は個別ピークの `verify_peak_annotation` を用いること。

### 12.5 アダクト/元素表の拡張（`peak_verification.py`）

`ADDUCT_SHIFTS` を `(sign, shift, charge, n_mol)` の4タプル化し、多量体 `[2M-H]-`・多価 `[M-2H]2-`・`[M+FA-H]-`（`[M+HCOO]-` の別名）を追加。`adduct_mz` は `m/z = (n_mol×neutral + shift) / charge` で多量体・多価に対応する（既存1価アダクトの数値挙動は不変）。元素表に D(²H)/F/Br/¹³C を追加（標識・ハロゲン対応）。CCS/RT 参照照合・同位体パターン照合は参照表未同梱のため v1 対象外。

## 13. 同定の出所: SME と SML（mzTab-M 経路）

mzTab-M では同定が 2 か所に出る。**意味が違うので混ぜてはいけない。**

| 出所 | 何か | このサーバでの置き場所 |
|---|---|---|
| SME 行（Small Molecule Evidence） | **スペクトル照合の証拠**。MS/MS が割り当てられた同定だけが載る | `feature_metadata` / `feature_candidates` |
| SML 行（Small Molecule） | 代表同定。MS1 だけの照合（Text DB）もここに載る | `feature_annotations` |

MS-DIAL は Text DB 由来の同定を **SME へ書かない**（`MztabFormatExport.cs` の
`ShouldWriteSmeLine` が `IsTextDbBasedRepresentative` を除外する）。したがって
Text DB 運用のメタボロミクスでは **SME が 0 行**になり、同定は SML にしか無い。
これは異常ではない。

特徴表（`.annotations.tsv`）の `identification_status` がこの区別を伝える。

| 値 | 意味 |
|---|---|
| `candidate` | SME 由来。スペクトル照合の候補（rank 1 でも確定同定は名乗らない） |
| `ms1_annotation` | **SML 由来。m/z 照合のみ。MS/MS の裏付けは無い** |
| `unidentified` | 同定情報が無い。複数の SML が 1 特徴に当たって決められない場合もここ |

**`ms1_annotation` を `candidate` と同一視しないこと。** MSI レベルで言えば
MS/MS 照合とは別水準で、v2 の `required_evidence` では `library_match` も
`authentic_standard_match` も満たさない。

`msi_level` は mzTab-M 経路では常に空欄。`.arf2` 由来の MSI ヒューリスティック
（§12.3）は Level 2 に MS/MS の取得を要件としており、別ルールの値を同じ列へ入れると
比較不能な 2 つの意味が同居するため。出所は `name_source`（`mztab_sme` / `mztab_sml`）
で伝える。

### 13.1 `feature_annotations` の項目

`DatasetState.feature_annotations` は `feature_id`（= `SMF_ID`）→ 注釈の辞書。
**証拠スロットではない**（`feature_bindings` はこれを読まない）。曖昧でない注釈は
次のキーを持つ。

| キー | 由来（SML 列） | 備考 |
|---|---|---|
| `sml_id` | `SML_ID` | どの SML 行から来たか |
| `ambiguous` | — | 常に `False`（曖昧な場合は下記の別形） |
| `name` | `chemical_name` | |
| `database_identifier` | `database_identifier` | MS-DIAL は必ず `<db>:<name>` 形式で書く |
| `chemical_formula` | `chemical_formula` | |
| `smiles` | `smiles` | |
| `adduct` | `adduct_ions` | |
| `reliability` | `reliability` | 例: `annotated by user-defined text library` |
| `confidence_measure` | `best_id_confidence_measure` | |
| `confidence_value` | `best_id_confidence_value` | float。**MS1 照合スコアであって MS/MS スコアではない** |
| `inchikey` | 導出 | `smiles` 経由でのみ到達しうる（下記） |
| `inchikey_source` | 導出 | `smiles_derived` / `none` |

**`inchi` キーは存在しない。** MS-DIAL は SML の `inchi` を常に `null` で書くため、
キーを置くと「取得していない」と「無い」の区別を偽ることになる。
`database_identifier` も必ず `<db>:<name>` 形式なので InChIKey にはならない。
したがって mzTab-M の SML から InChIKey に到達できるのは `smiles` 経由（RDKit）だけで、
テキスト DB が SMILES 列を持たなければ `inchikey` は `None` のままになる。

1 つの特徴に複数の SML 行が当たった場合は、どれが正しいか決められないので名前を
選ばない。その形は `{"ambiguous": True, "sml_ids": [...], "name": None}` で、
特徴表では `unidentified` として出る。

同定の出所内訳は `dataset_load` の要約と `inchikey_coverage["identified_by"]`
（`sme` / `sml_only` / `none`）で読める。
