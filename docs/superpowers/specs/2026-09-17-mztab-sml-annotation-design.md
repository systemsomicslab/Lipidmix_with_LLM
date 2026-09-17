# mzTab-M の SML 注釈を同定ラベルとして扱う 設計

- 日付: 2026-09-17
- 発端: 実 MS-DIAL Console 出力（`docs/HISTRY.md` 2026-09-17(2)）で、Text DB 同定が
  SML にしか現れず、`DatasetState` が同定を 1 件も拾えなかった。
- 関連: [v2 公開入口と上流接続](2026-09-17-v2-public-upstream-design.md)、
  [実装監査](../plans/2026-09-16-validated-lcms-metabolomics-audit.md)

## 1. 目的と完成条件

mzTab-M の **SML セクションに載る同定を「ラベル」として回収する**。証拠
（`required_evidence` を満たす材料）としては**一切使わない**。

完成条件:

1. Text DB 注釈だけの実 mzTab-M（SME 0 行）から、feature ごとの化合物名・adduct・
   スコアが取れる。
2. `lipidmix/analysis/feature_bindings.py` に**差分が無い**。証拠の意味論が
   動いていないことをファイル差分の不在で示す。
3. MS1 注釈と MS/MS 裏付けの同定が、出力のどの経路でも**見分けられる**。
4. 差次的エクスポートの InChIKey ゲートは変わらない。

## 2. 背景と根拠（実測）

実行: `MSDIALCUI lcms` 5.5.241113 / SCIEX `.wiff` 11 検体 /
`Text DB file path` に GABA 4 化合物、`Msp file path` と `Lbm file path` は空。
出力 `AlignResult-2026917123.mzTab`。

| 観測 | 値 |
|---|---|
| SML / SMF / SME 行数 | 3050 / 3050 / **0** |
| SML↔SMF の参照 | **厳密に 1:1**（各 1 個） |
| 注釈付き SML | 3 行（GABA 0.999982 / Glutamate 0.6837 / Glutamate 0.999998） |
| 注釈付き SML の `smiles` `inchi` `chemical_formula` | すべて `null` |
| 注釈付き SML の `database_identifier` | `TextDB:GABA` 形式（InChIKey ではない） |
| 対応する SMF の `SME_ID_REFS` | `None` |

上流の規則（`C:\Users\yuu18\source\repos\MsdialWorkbench`）:

- `src/MSDIAL5/MsdialCore/Export/MztabFormatExport.cs` `ShouldWriteSmeLine`:
  `IsMsmsAssigned != true` と `MatchResults.IsTextDbBasedRepresentative == true` を
  除外する。**Text DB 同定は SME に出ない（設計）**。
- 同 `:393` `var inchi = "null";` — SML の `inchi` は**常に null**。
- 同 `:407` `databaseIdentifier = <databaseID> + ":" + <name>` — **InChIKey にならない**。
- 同 `:417-418` `chemicalFormula = metadata["Formula"]` / `smiles = metadata["SMILES"]` —
  照合した参照レコード由来。

したがって `derive_inchikey`（`database_identifier` 正規表現 → `inchi` → `smiles`）の
3 経路のうち、MS-DIAL の SML で到達しうるのは **`smiles` → RDKit のみ**。

## 3. 範囲

### 3.1 含むもの

- `DatasetState` への `feature_annotations` 追加と構築。
- `feature_export`（`.annotations.tsv`）への状態 `ms1_annotation` 追加。
- `dataset_load` 要約への出所内訳の表示。
- `dataset_export`（差次的エクスポート）でのマージと `name_source` の訂正。

### 3.2 含まないもの

- `feature_bindings.py` の変更（**禁止**。§5 の不変条件）。
- `feature_metadata` / `feature_candidates` の中身の変更。
- 差次的エクスポートの InChIKey ゲートの変更。
- `msi_level` への値の投入（§7）。
- `EXPORT_COLUMNS` の増減・改名・並べ替え（`CONTRACT_VERSION` は 1 のまま）。

## 4. データモデル

`DatasetState` に 1 スロットを足す。`feature_id`（= `SMF_ID`）→ 注釈。

```python
ds.feature_annotations: dict[str, dict]
# 値（曖昧でない場合）
{
    "sml_id": str,
    "name": str | None,                 # SML.chemical_name
    "database_identifier": str | None,  # MS-DIAL は必ず "<db>:<name>"
    "chemical_formula": str | None,
    "smiles": str | None,
    "inchikey": str | None,             # smiles から RDKit 経由でのみ到達しうる
    "inchikey_source": str,             # 既存語彙: database_identifier / inchi_derived
                                        # / smiles_derived / none
    "adduct": str | None,               # SML.adduct_ions
    "reliability": str | None,          # MS-DIAL の自由文字列
    "confidence_measure": str | None,   # SML.best_id_confidence_measure
    "confidence_value": float | None,   # SML.best_id_confidence_value
    "ambiguous": False,
}
# 値（曖昧な場合。§6 の「複数 SML → 同一 SMF」）
{
    "ambiguous": True,
    "sml_ids": list[str],
    "name": None,          # 選ばない
}
```

曖昧な場合の辞書は上の全項目を持たない。`ambiguous` を先に見て分岐すること
（`name` が `None` であることだけを頼りに分岐しない——曖昧でない注釈でも
`chemical_name` が無く `database_identifier` だけの行がありうる）。

規約:

- **`inchi` のキーを持たない。** 上流が常に null を書くので、キーを置くと
  「取得していない」と「無い」の区別を偽る。
- SML 行が同定情報（`chemical_name` か `database_identifier` のいずれか）を持つときだけ
  登録する。**登録されていること自体が「注釈あり」の意味**。
- **SME の有無で分岐しない。** 分岐を足すほうがコードが増え、ファイルが言っている
  ことをそのまま記録するほうが正直。優先順位は consumer 側で決める（§6）。

## 5. 不変条件（最重要）

**`lipidmix/analysis/feature_bindings.py` を変更しない。`feature_metadata` と
`feature_candidates` の中身も変えない。**

理由: `_check_identity`（`feature_bindings.py:126-170`）は候補ゼロのとき
`feature_metadata["inchikey"]` を見て、一致すれば `matched` を返す（`:136,140-141`）。
ここに SML 由来の識別子が流れ込むと `identity_status` が `not_evaluable` →
`matched` に変わり、`_evaluate_feature:269` の分岐が動く。影響:

| 証拠水準 | 現状 | 識別子が漏れた場合 |
|---|---|---|
| `mass_rt` | 成立（同定不要） | 変わらず |
| `library_match` | `identification_required` でブロック | `identification_missing` でブロック（結論は同じ） |
| `authentic_standard_match` | `identification_required` で**停止** | `_check_standard_match` まで進み**合格しうる** |

最後の 1 行が証拠水準の実質的な緩みにあたる。これを避けるため、SML 由来を
**別スロットに隔離する**（本設計の案 A）。`feature_metadata` にタグを足して
binding 側にガードを入れる案 B は、ガードの書き漏らしが「証拠が緩む」方向に倒れるため
採らない。案 A では consumer がマージを書き忘れても「ラベルが出ない」だけで済む。

## 6. SML → feature の対応付け

`SML.SMF_ID_REFS` を `|` で分割して引き当てる。実データは厳密に 1:1 だったが、
mzTab-M は多対多を許すので規則を決めておく。

| 形 | 扱い |
|---|---|
| 1 SML → 1 SMF | その feature に注釈を付ける |
| 1 SML → 複数 SMF（同一分子の複数 adduct） | **各 feature に同じ注釈を付ける**。正しい意味 |
| 複数 SML → 同一 SMF | **選ばない**。`{"ambiguous": True, "sml_ids": [...], "name": None}` を記録し、警告を 1 件積む |
| SML が指す SMF が存在しない | 無視し、警告を 1 件積む |

「同じ名前が複数 feature に付く」（実データの Glutamate ×2）は上表の逆向きで、
各 feature が自分の注釈を持つだけ。特別扱いはしない。

警告は件数ではなく**種類**で集約する（`reader.py` の SME 末尾空欄警告と同じ流儀。
行ごとに積むと実データで数百件になり、重要な警告が表示制限に埋もれる）。

## 7. consumer 側の規則

`feature_metadata` の読み手 6 箇所のうち同定を使うのは 3 箇所。残る 3 箇所
（`assay_evidence.py:267` / `mztab/evidence.py:289` / `feature_bindings.py:241`）は
m/z・RT しか使わないので手を入れない。

### 7.1 `feature_export.py` の `.annotations.tsv`

既存の `identification_status` に **第 3 の値 `ms1_annotation`** を足す。

| SME 候補 | SML 注釈 | `identification_status` | `name` ほかの出所 |
|---|---|---|---|
| あり | 問わない | `candidate`（現状のまま） | SME 候補 |
| 無し | あり（曖昧でない） | **`ms1_annotation`** | `feature_annotations` |
| 無し | 無し / 曖昧 | `unidentified`（現状のまま） | なし |

列は既存の 12 列（`_ANNOTATION_COLUMNS`）のままで足りる。`name` /
`database_identifier` / `adduct` / `confidence_measure` / `confidence_value` が
SML の項目に 1:1 で対応するため。`candidate_rank` は `ms1_annotation` では `None`。

`feature_annotations` を読むのは **`ms1_annotation` の行だけ**。`candidate` と
`unidentified` の行は現状どおり `feature_metadata` / `feature_candidates` から組む
（`inchikey` を含む）。既存 2 状態の出力は 1 バイトも変わらないこと。

`_ANNOTATION_COLUMNS` は v2 内部の表であり別リポ契約ではない。値の追加は安全。

### 7.2 `dataset_load` の要約（`mztab_tools.py:130,216`）

`inchikey_coverage` に出所内訳を足す。

```python
ds.inchikey_coverage = {
    "total_features": int,
    "with_inchikey": int,          # 意味は不変（SME 由来 + SML 由来）
    "by_source": {...},            # 既存語彙のまま
    "rdkit_available": bool,
    # 追加
    "identified_by": {"sme": int, "sml_only": int, "none": int},
}
```

要約行に **「MS/MS 証拠あり N 件 / MS1 注釈のみ M 件」** を出す。
**LLM が MS1 注釈を MS/MS 裏付けと取り違えないために、ここは必ず可視化する**
（MCP サーバ指示の「`analytical_checks.msms.band` の PASS と FLAG_ONLY を同一視するな」と
同じ趣旨）。

### 7.3 `dataset_export.py` の差次的エクスポート

優先順位は **SME → SML**。ただし**列ごとに独立して選ばない**——1 行の中で `name` が
SML 由来、`inchikey` が SME 由来、といった食い違いが起きるため。

**行単位で出所を 1 つに決める。決め手は「InChIKey を供給した側」**（InChIKey が
エクスポートのゲートであり、行の同一性の根拠だから）。

| 列 | 規則 |
|---|---|
| 出所の決定 | `feature_metadata["inchikey"]` があれば SME、無ければ `feature_annotations["inchikey"]` を見て SML。どちらも無ければ行を捨てる（従来どおり） |
| `name` / `inchikey` / `inchikey_source` | **決まった側からまとめて採る** |
| `name_source` | `"mztab_sme"` / `"mztab_sml"` |
| `msi_level` | **空欄のまま**（§8） |
| `ontology` | 空欄のまま（mzTab-M に対応物なし） |

`ambiguous` な注釈（§6）は無いものとして扱う。

`name_source` は現在 `"mztab_smf"` を書いているが、**実際の出所は SME なのでこれは
事実と違う**。`"mztab_sme"` へ訂正する。下流（`massbank-context` の
`experiment/contract.py`）の `REQUIRED_COLUMNS` は
`spot_id, name, ontology, inchikey, mz, rt, log2fc, p_value, q_value, mean_a, mean_b,
significant` で、`name_source` / `inchikey_source` / `msi_level` は**必須でも読まれても
いない**（`contract.py:14-17` と行構築 `:176-186`）。したがって値の変更・追加は
下流に影響しない。`CONTRACT_VERSION` は 1 のまま。

**引き継ぎ事項**: 下流は `inchikey` を MassBank / パスウェイへの結合キーにしている
（`contract.py:71`）。MS1 注釈由来の InChIKey が混ざるようになるため、
`massbank-context` 側が `name_source` / `inchikey_source` を読んで証拠水準で
絞り込めるようにすべき。本設計の範囲外だが、先方へ伝える。

## 8. `msi_level` に値を入れない理由

`msi_level` のもう一方の生産者は `lipidmix/msdial/lipid_identity.py` の `msi_level` で、
これは .arf2 向けのヒューリスティックであり **Level 2 が MS/MS の取得を要件にしている**。
SML の MS1 注釈を同じ列に別ルールで入れると、1 つの列に比較不能な 2 つの意味が同居する。
`name_source = "mztab_sml"` が同じことを曖昧さなく言えるので、列を二重定義しない。

## 9. この設計が解かないこと

`dataset_export.py:70-73` の **InChIKey が無い feature を捨てるゲートは変えない**
（下流が InChIKey で結合するため）。帰結:

- **今回の GABA データでは、差次的エクスポートは依然 `NO_ANNOTATED_FEATURES` で失敗する。**
  SML に SMILES が無いため InChIKey に到達できない。
- 本設計が解くのは「対話・特徴表・レポート・`dataset_load` 要約で化合物名が見える」
  ところまで。
- エクスポートまで到達させるには、**v2 profile が使うテキスト DB に SMILES 列
  （`TextLibraryParser` の `[6]smiles`）を要求する**必要がある。InChIKey 列（`[4]`）を
  入れても mzTab には出ない（§2 の上流規則）。

**未確認事項**: `metadata["SMILES"]` が Text DB 由来の参照レコードから実際に埋まるかは
未確認。**SMILES 列を足したテキスト DB で実 Console を再実行して確認する**こと。
Stage B の確認項目に載せる。確認が取れるまで、SMILES 要件は profile の推奨であって
保証ではない。

## 10. 検証

### 10.1 受け入れ条件

| # | 条件 |
|---|---|
| A1 | SME 0 行・SML 注釈ありの mzTab で `feature_annotations` に名前・adduct・スコアが入る |
| A2 | `git diff` に `lipidmix/analysis/feature_bindings.py` が現れない |
| A3 | 同じ dataset で `_check_identity` が `identity_not_evaluable` を返す（`matched` ではない） |
| A4 | 同じ dataset で `required_evidence: library_match` がブロックされる |
| A5 | 1 SML → 複数 SMF で全 feature に注釈が付く |
| A6 | 複数 SML → 1 SMF で名前を付けず `ambiguous` を記録し、警告が 1 件積まれる |
| A7 | SML の `smiles` から InChIKey が導出される。`inchi` キーは存在しない |
| A8 | `.annotations.tsv` が `candidate` / `ms1_annotation` / `unidentified` を出し分ける |
| A9 | `dataset_load` 要約に「MS/MS 証拠あり / MS1 注釈のみ」の件数が出る |
| A10 | SML 由来 InChIKey を持つ行が `name_source=mztab_sml` でエクスポートされる |
| A11 | 名前だけで InChIKey が無い行は**従来どおり捨てられる**（ゲート不変） |
| A12 | `EXPORT_COLUMNS` と `CONTRACT_VERSION` に差分が無い |

### 10.2 試験の作り方

- **fixture は実形式にする**。`SMH` ヘッダ・SME セクション 0 行・SML に注釈。
  合成 fixture がヘッダ行を `SML` 接頭辞で書いて実形式とずれていたことが
  `_HEADER_PREFIX_MAP` バグの温床だった（`docs/HISTRY.md` 2026-09-17(3)）。
- A2 は差分の有無で見る。AST テストは置かない（ファイルを触らないことが条件であり、
  中身の形を縛りたいわけではない）。
- A3/A4 は `feature_bindings` を**呼んで**確認する。呼ばずに「変更していないから
  大丈夫」とはしない。
- 実データ（`data/kanzo_multiomics/.../AlignResult-2026917123.mzTab`、追跡外）は
  テストが依存してはならない。手元確認にのみ使う。

## 11. 更新する文書

| 文書 | 内容 |
|---|---|
| `docs/output_format/identity.md` | 同定の出所（SME / SML）と `ms1_annotation` の意味 |
| `docs/output_format/` の mzTab トピック | `feature_annotations` の項目定義 |
| `docs/workflow/mztab.md` | 呼び出し連鎖に SML 経路を追加 |
| `USAGE.md` | `dataset_load` の戻り値に出所内訳が増える |

vault の流れ図（`ms-data-parser-flow.md`）は**対象外**。入口の判定規則・pipeline の
stage 列・`missing_state` の連鎖・経路の増減・既定値のいずれも変わらない。

## 12. 未解決・持ち越し

1. `metadata["SMILES"]` が Text DB 由来で埋まるか（§9 の未確認事項）。Stage B で確認。
2. `massbank-context` が `name_source` / `inchikey_source` を読んで証拠水準で絞れるように
   する（先方リポの作業）。
3. MS-DIAL の SML `reliability` は mzTab-M 標準の 1〜4 コードではなく自由文字列。
   将来ここを解釈する場合は上流の値域を先に確かめる。本設計では素通しで保持するだけ。
