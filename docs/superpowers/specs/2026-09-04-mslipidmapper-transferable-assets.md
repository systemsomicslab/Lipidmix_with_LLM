# MSLipidMapper からの転用候補調査 —— 何が使えて、なぜ使えるのか

作成日: 2026-09-04
状態: **調査記録**。実装は一切していない。将来必要になったときに読む棚。
      各項目の「なぜ使えるか」は、この時点のこのリポジトリの欠落と突き合わせて書いてある。
      実装に進むときは、本文書を入力として別途 plan を書く。
出典: <https://github.com/systemsomicslab/MSLipidMapper>（既定ブランチ `main`、2026-09-04 時点）／
      プレプリント <https://www.biorxiv.org/content/10.64898/2026.05.21.726751v1>

---

## §0 なぜ調べたか

`C:\Users\yuu18\.claude\kb\facts\pubchem-pathway-coverage-is-too-low-for-lipids.md` に
2026-09-04 の実測が残っている。実データ（RAW 細胞・NEG・60 サンプル・714 特徴）の差次的結果から
InChIKey 279 種を PubChem SPARQL に照会し、**パスウェイが付いたのは 11/279 = 3.9%**。
mouse に絞ると背景 10 化合物・パスウェイ 2 件で `fisher_q` は全部 1.0。生物種も 4 割規模で混在する。
**コードが正しくても、この参照側では濃縮統計が意味を持たない。** 原因は照合失敗ではなく注釈の不在。

同エントリの結論は「参照側を替えよ —— LIPID MAPS／Reactome の上位粒度、あるいは分子種をクラスへ
丸めてクラス単位で検定する」。MSLipidMapper はまさにその路線で組まれており、しかも**同じ研究室の
MIT ライセンス実装**なので、成果物ごと持ってこられる。

---

## §1 対象の同定

| 項目 | 内容 |
|---|---|
| 正体 | R パッケージ + Shiny アプリ。「pathway-centered lipidome analysis environment」 |
| 著者 | Takaki Oka, Kozo Nishida, Takeshi Harayama, Hiroshi Tsugawa（**MS-DIAL と同じ systemsomicslab**） |
| ライセンス | **MIT。Copyright (c) 2024, Takaki OKA**（→ コードもデータも取り込み可。帰属表示だけ要る） |
| 依存 | R ≥ 4.3、Bioconductor: `SummarizedExperiment` `S4Vectors` `ComplexHeatmap` `clusterProfiler` `GO.db` `ropls` `rgoslin` |
| 起動 | `MSLipidMapper::run_mslipidmapper()`（port 3838）／Docker（3838 + API 7310）／Windows は `MSLipidMapper.bat` |
| 入力 | MS-DIAL アライメントテーブル CSV ／ MS-DIAL mzTab-M ／ 汎用 CSV 2 枚（abundance + lipid→Ontology 対応表） |
| **できないこと** | **生データもバイナリも読まない**（"does not process raw mass spectrometry files"）。`.arf` `.arf2` `.pai2` `.dcl` は対象外 |
| 中核データ構造 | `SummarizedExperiment`（定量値・サンプルメタ・サブクラス注釈・**パース済みアシル鎖特徴**を 1 つに束ねる） |

`R/` は 42 ファイル。転用の観点で意味があるのは
`Enrich_module.R` `lipid_rules.yaml` `lipid_chain_parser_se.R` `ccd_analysis.R`
`mztab_m_import.R` `normalize.R` `pathway_cli.R` `mod_utility_pathway_editor.R`
`mod_utility_ontology_builder.R` `load_transcriptome_se.R` `remodeling.cyjs`。

**位置づけの結論**: 競合ではなく**下流**。向こうの入口（CSV / mzTab-M）は、こちらの出口。
こちらの差分はバイナリを読むこと、MCP としてツール化されていること、Console 実行層を持つこと。

---

## §2 転用候補（価値順）

### 候補 1 —— サブクラス粒度のキュレート済みパスウェイ網（`.cyjs`）

**何があるか**: `inst/extdata/examples/` に 7 枚。

| ファイル | 内容 |
|---|---|
| `remodeling.cyjs` | リン脂質リモデリング。**73 ノード**。脂質ノード + 酵素/遺伝子ノード |
| `remodeling_lipidonly.cyjs` | 同じ網の脂質のみ版。**約 45 ノード（脂質クラス 26 + 中間/接合ノード約 19）・エッジ約 60** |
| `ceramidepathway.cyjs` / `ceramidepathway_lipidonly.cyjs` | セラミド経路 |
| `globalpathway.cyjs` / `global.cyjs` | 全体図 |
| `pathway-cli.yml` | CLI 設定例（→ 候補 5） |

ノードの実体（`remodeling_lipidonly.cyjs`）:

```json
{ "id": "344", "label": "PC", "name": "PC", "SUID": 344,
  "Width": 140, "Height": 140, "Color": "Blue",
  "Ensembl_ID": "", "LipidMAPS_ID": "" }
```

`remodeling.cyjs` は脂質ノードと酵素ノードが**`Color` フィールドで区別**される:

```json
{ "label": "Agps",   "Color": "Black", "Ensembl_ID": "ENSMUSG00000042410" }
{ "label": "Lpcat3", "Color": "Black", "Ensembl_ID": "ENSMUSG00000004270" }
{ "label": "EtherPS","Color": "Blue",  "Ensembl_ID": "" }
```

エッジは `source` / `target` / `shared_interaction: "interacts with"` / `name`（例 `"PC (interacts with) LPC"`）。
**酵素名も KEGG ID も反応式もエッジには載っていない** —— 生化学的な隣接関係だけ。

#### 開発者からの回答（2026-09-04、Slack。著者 Takaki Oka 氏）

「なぜ DB をシステムに入れているのか（処理が軽いからか）」という問いに対する回答:

> それは単純に脂質の代謝パスウェイのテンプレートを自分で作って配置している感じ。DB として同じ情報は
> 世の中にある気がするけど、cytoscape でそのまま読み込んで整列されたレイアウトとして app 上で
> 表示するためのファイルになるかな
>
> （図を添えて）こんな感じで整理したレイアウトに並べる用のファイル。各ノードとかの座標も入ってる。
> **言ってしまえば DB とは別に考えて**

**この回答が本文書の前提を訂正する。** `.cyjs` は**参照 DB ではなく描画資産**である。
エッジに酵素名・KEGG ID・反応式が無いのは実装の不足ではなく、そもそもそういう性質のファイル。
帰結は 2 方向:

- **弱まる方向 — 生物学的主張の権威**。出典は「同研究室の研究者が自作したテンプレート」であり、
  文献参照が付いていない。**LLM に「PC → LPC が隣接している」を*根拠として*引用させてはいけない。**
  ツールの戻り値には出典（自作テンプレート／文献未参照）を必ず添える。
  `docs/output_format/` の「推測はしない」流儀と同じ扱いにする。
- **強まる方向 — 座標の稀少性**。**座標は自動生成できない**。45 ノードにばね配置や graphviz を
  掛けても、リピドミクス研究者が読める図にはならない。「どのクラスをどこに置くか」は
  生化学の知識が入った人手の産物で、MIT で入手できること自体が価値。

**PubChem 経路の代替になるのは候補 2（脂質名から項目を導出する濃縮）であって、`.cyjs` ではない。**
両者は独立している。混同しないこと。

**なぜ使えるのか（ここが本題）**:

1. **ノードのキーが素のクラストークン**（`PC` `PA` `DG` `EtherPS`）。InChIKey も外部 DB 照会も要らない。
   分子種 → ノードの写像に必要な材料は、**このリポジトリが既に全部持っている**:
   - `.arf2` の `Ontology` 列（`lipidmix/arf2/reader.py` Key14。「脂質クラス (FA, PC, TG...)」）
   - `lipidmix/msdial/peak_verification.py` の `extract_class_token(name, ontology)`
   - `reference/lipidmaps_classes.tsv`（クラス→LIPID MAPS カテゴリ/メインクラス）
2. **被覆の問題が原理的に消える**。静的 JSON なので、同梱物に載っているクラスは 100% 当たり、
   外部照会も失敗もしない。**ただし §0 の PubChem 問題を解くのは候補 2 であってこれではない**
   （上の「開発者からの回答」参照）。`.cyjs` が解くのは「変化を*どこに*置いて見せるか」だけ。
3. **MIT なので vendor できる**。`reference/` に既に `lipidmaps_classes.tsv` `refmet_map.tsv` を
   同梱する前例があり、置き場所の議論が要らない。
4. 静的でよい根拠が論文側にもある —— "static, curated lipid metabolic pathway maps ... at the subclass level"。
   動的にパスウェイ DB を引く設計そのものが、脂質では過剰だった。

**注意**: `Ensembl_ID` は**マウス**（`ENSMUSG…`）。生物種が違う実験では酵素ノードの ID は流用できない
（脂質ノードは種非依存なので影響しない）。`_lipidonly` 版を既定にし、酵素ノードは候補 6 の文脈でのみ使う。

---

### 候補 2 —— 濃縮の項目定義（`R/Enrich_module.R`）: 外部 DB ゼロで成立する設計

**何をしているか**: ORA（over-representation analysis）を `clusterProfiler::enricher()` で回す。

```r
clusterProfiler::enricher(
  gene = sig_in,
  universe = universe_ids,
  TERM2GENE = term2gene,
  pvalueCutoff = 1,
  qvalueCutoff = 1,
  pAdjustMethod = p_adjust_method
)
```

- 項目（TERM）は 2 系統: **`"Ontology:<クラス>"`** と **`"Chain:<20:4>"`**（`use_chain_terms` で切替）
- 遺伝子（GENE）に相当するのは**脂質 ID**
- universe = **背景ファイルの脂質列の全体**（`universe_ids <- .norm_chr(df[[lipid_col]])`）
- 検定は `enricher()` 既定の Fisher 正確検定。多重補正は `BH`（既定）/ `bonferroni` / `holm` /
  `hochberg` / `hommel` / `BY` / `fdr` / `none` から選択、`p.adjust <= padj_cut` で絞る

**なぜ使えるのか**:

1. **項目集合を脂質名そのものから導出している**。注釈 DB の被覆に一切依存しない。
   §0 の「背景 10 化合物」問題が構造的に起きない —— universe は測定された全脂質（実データで 714 特徴）。
2. **kb エントリの推奨そのもの**（「分子種 → クラスへ丸めてクラス単位で検定する（被覆が桁で上がる）」）を
   実装として具体化している。しかも `Chain:` 系統という第 2 の軸まで用意されている。
3. こちらに移すのに新しい依存が要らない。Fisher + BH は `scipy.stats.fisher_exact` と
   BH の 10 行で足り、`lipidmix/analysis/` の既存流儀（入力形式に依存しない数値処理）にそのまま収まる。
4. 入力は既にある。`lipidmix/analysis/export_contract.py` が吐く TSV に
   `name` `ontology` `inchikey` が入っており、有意集合と背景集合の両方がそこから取れる。

**注意**: `pvalueCutoff = 1, qvalueCutoff = 1` で呼んで**後段で絞っている**。
つまり全項目の p / q を得てから閾値を当てる設計で、これは正しい（LLM に渡すときも
「有意なし」を「計算できなかった」と混同させないために、q 値の分布を返せるほうがよい）。

---

### 候補 3 —— アシル鎖パーサ（`R/lipid_rules.yaml` + `R/lipid_chain_parser_se.R`）

**現状の欠落（実測）**: `lipidmix/` を `acyl|chain_len|carbon|double_bond` で grep してヒットするのは
`lipidmix/corpus/knowledge_store.py` の語彙だけ。**アシル鎖レベルの処理は存在しない。**
同定層（`docs/output_format/identity.md` §12）は pygoslin の `normalized` / `level` /
`lipid_maps_category` とクラストークンで止まっている。

**何があるか**: 優先度付きの宣言的ルール表（パターン約 20 本）。

```yaml
defaults:
  oxygen_detect_keywords: [";OH", "(OH)"]
patterns:
  - id: bile_acid
    class_regex: "^BA$"
    no_chain: true
    priority: 200
  - id: ceramide_force_drop_base
    class_regex: "^(Cer|HexCer|ASHexCer|...)"
    split: "/"
    detect_fa_suffix: true
    priority: 180
```

パーサ側（`lipid_chain_parser_se.R`）のアルゴリズム:

1. `load_lipid_rules(yaml_path)` で YAML を読む。`select_rule()` が `.score_rule()` で採点
   （`priority × 1000` ＋ 正規表現長をタイブレーク ＋ フラグ加点: `no_chain: 80` / `ignore_sum_only: 20`）
2. `.parse_lipid_chain_metadata()` が:
   - `"A|B"` の候補列挙を `.pick_best_alt()` で解決（**鎖トークン数が最多の候補を採る**）
   - クラス接頭辞と括弧を除去して core を切り出す
   - `no_chain: TRUE` なら空を返す。`ignore_sum_only: TRUE` かつ「sum-only 風」（トークン 1 個・
     区切りも接尾辞証拠も無い）なら空を返す
   - `detect_fa_suffix` で `(FA 22:6)`、`detect_fa_tail` で末尾 `… FA 20:4` を抽出
   - `split: "auto"` は `/` → `_` → 空白の順に試す
   - `.norm_chain_token()` が `0:0` を除去、`;2O` → `;O2` に正規化、`(2OH)` は保持
   - `exclude_first_chain: TRUE` かつ 2 本以上あれば先頭鎖を落とし、**落ちた鎖はスフィンゴイド塩基に回す**
3. 結果は rowData の 2 つのリスト列 `acyl_chains` / `sphingoid_bases`（`add_chain_list_to_se()`）。
   `.ensure_listcol()` が NULL を作らせない（空は `list(character(0))`、NA にしない）
4. パース不能な名前は例外を投げず `character(0)` を返す

**なぜ使えるのか**:

1. **入力が同じ**。MS-DIAL のショートハンド表記に合わせて作られている。こちらのパーサが読む
   `.arf2` の `MetaboliteName` と同じ文法。
2. **pygoslin では取れない実務処理が入っている**。「Cer の base を強制的に落とす」
   「`;OH` / `(OH)` を酸素として検出」「`;2O` → `;O2` の表記揺れ吸収」「sum composition 表記を
   分子種と誤認しない」。これらは仕様書ではなく実データから来た知見で、ゼロから再発明するのは高い。
3. **ルールが YAML に外出しされている**。移植は「YAML をそのまま持ってきて、
   ルール解釈器だけ Python で書く」で済む。ルール表は上流の更新に追従できる。
4. 既存の同定層と衝突しない。pygoslin は正規化名と構造レベルを担当し、
   このパーサは**鎖トークンの集合**を担当する。役割が直交している。
5. 候補 2 の `Chain:` 項目と候補 5 のサブセット再投影が、**両方このパーサの出力を前提にしている**
   —— 依存関係の根。

**注意**: `docs/output_format/identity.md` §12.1 の**プラズマローゲン注意と同種の落とし穴が残る**。
pygoslin は species レベルで `PC P-34:0` と `PC O-34:1` を同一化する。鎖トークン集合でも
P-/O- の区別は名前の書き方に依存するので、鎖ベースの部分集合定義に P-/O- を混ぜてはいけない。
既存の `peak_verification.ether_caveats()` を併記する設計を引き継ぐ。

---

### 候補 4 —— CCD 解析（`R/ccd_analysis.R`）: クラスと鎖サブセットの乖離

**何をしているか**: ファイル冒頭の宣言は "Class x chain-subset divergence analysis utilities."

`compute_ccd_table()` が「脂質クラス × 鎖サブセット」1 組を 1 行として:

- クラス内の分子種を `colSums()` でサンプル方向に合算 → クラスプロファイル
- サブセット（特定の鎖を含む種）も同様に合算 → サブセットプロファイル
- `stats::cor()` で両者の相関 `cor`
- **`CCD = 1 - cor`**（乖離）
- **`SCR = subset_total / class_total`**（サブセットの占有率）
- `.ccd_direction_label()` が相関の閾値から `direction` を付ける

出力列: `class` `subset` `cor` `CCD` `SCR` `direction` `n_species` `warning_flag`。
`compute_ccd_class_summary()` がクラス 1 行に畳み、最上位サブセットと
`interpretation`（`"stable class"` / `"major divergent subset"` 等）を返す。

**なぜ使えるのか**:

1. **「クラスで丸めると情報が落ちる」への直接の答え**。候補 2 でクラス単位の検定に逃がした分を、
   「そのクラスは本当に一枚岩か、特定の鎖を持つ種だけが逆方向に動いていないか」で回収できる。
   クラス集約の妥当性を**数値で示す**手段になる。
2. **戻り値が小さい**。クラス数 × 上位サブセットの表 1 枚で、LLM に載る。
   CLAUDE.md の payload 規律（座標や行列を返さない）と相性がよい。`warning_flag` と
   `n_species` が付いているので「語れない行」を LLM に判別させられる。
3. 依存が相関だけ。numpy で足りる。

**注意**: `CCD = 1 - cor` は**サンプル数が少ないと不安定**（相関の分散が大きい）。
`n_species` と `warning_flag` を落とさずに運ぶこと。既存の
`preprocessing.run_order_correlation()` が「語れないものは None を返す」流儀を持っているので、
それを踏襲する（相関を無条件に数値で返さない）。

---

### 候補 5 —— アシル鎖サブセットの再投影と、CLI の設定契約（`R/pathway_cli.R` + `pathway-cli.yml`）

**何をしているか**: 論文の中核操作 —— "define lipid subsets based on structural features such as
specific acyl chains, and **re-project these subsets onto the same pathway context**"。

CLI のオプション: `--input` / `--network`(`--cyjs`,`-n`) / `--output`(`-o`) /
`--acyl-chains`(`-a`) / `--acyl-match`(`any` | `all`)。
**適用順序が明記されている: アシル鎖フィルタ → 正規化 → 脂質クラス集約。**
「明示的にパースできた鎖だけがマッチする」（＝ sum composition は当たらない）。
ネットワーク未指定なら remodeling / ceramide / global の同梱 3 枚を描く。

設定 YAML の全体（`inst/extdata/examples/pathway-cli.yml`）:

```yaml
input:
  lipidomics: Lipid_AlignmentTable.csv
  format: msdial
  annotation_cols: "1:35"
  header_rows: 5
  data_start_row: 6
analysis:
  normalization: none
  aggregation: sum
  group_column: class
  exclude_groups: [Blank, Quality control]
pathway:
  timeout_seconds: 90
plot:
  type: dot            # dot | box | violin
  x_order: ["2", "12", "19", "24"]
  font_size: 20
  palette: { "2": "#1B9E77", "12": "#D95F02", "19": "#7570B3", "24": "#E7298A" }
  dot: { point_size: 2.0, jitter_width: 0.15, alpha: 0.9, show_median: true }
output:
  directory: pathway_projection
  paper_size: A4
  orientation: landscape
```

**なぜ使えるのか**:

1. **MCP ツール 1 本として自然**。「20:4 を含む種だけでパスウェイを塗り直す」は LLM から呼ぶ価値が高く、
   引数が `chains` と `match` の 2 つで済む。
2. **適用順序が仕様として書かれている**のが重要。フィルタを正規化の後に掛けると
   正規化係数が母集団に依存して結果が変わる。この順序（フィルタ → 正規化 → 集約）は
   そのまま採るべき決定で、自分で悩む必要がない。
3. `exclude_groups: [Blank, Quality control]` は既存の `preprocessing.detect_sample_roles()`
   （sample / qc / blank 分類）と**同じ問題を同じ解き方で扱っている** —— 設計の裏取りになる。
4. ノード内に各サブクラスの分子種分布（dot / box / violin）を描く発想は、
   「ノードを塗るだけでは分子種の非一枚岩性が見えない」という候補 4 と同じ問題意識。

**注意**: 設定ファイルをこのリポジトリに持ち込むかは別問題。MCP はツール引数が契約なので、
YAML 設定を再現する必要はない。**採るのは順序と語彙**（`aggregation: sum`、`match: any|all`）。

---

### 候補 6 —— 酵素/遺伝子ノードへの発現量重畳（`load_transcriptome_se.R` / `mod_plot_tx.R`）

**何をしているか**: "Gene or protein expression data can also be overlaid on pathway-associated
reactions"。`remodeling.cyjs` の `Color: "Black"` ノードが `Ensembl_ID` を持っており、そこに載せる。

**なぜ使える（かもしれない）のか**:

1. 脂質側だけでは「なぜその変化が起きたか」に到達できない。酵素ノードは
   `lipidmix/tools/` の目的記録（`record_objective`）や `knowledge/` の文献知識と結びつく先になる。
2. ノードに `Ensembl_ID` が既に埋まっているので、**マッピング表を自作しなくてよい**。

**なぜ今は保留すべきか**:

1. **こちらはトランスクリプトームを扱っていない**。入力もツールも無い。
2. `ENSMUSG…` は**マウス限定**。実データ（RAW 細胞 = マウスマクロファージ）とは合うが、
   汎用にするなら種ごとの表が要る。
3. 優先度が低い —— 候補 1〜3 が入らないと重畳する土台がない。

**記録しておく価値**: 「酵素ノード付きの網が MIT で手に入る」という事実自体。
将来 multi-omics に手を出すとき、ゼロからの網構築が要らないと分かっているのは大きい。

---

### 候補 7 —— 正規化メソッド（`R/normalize.R`）: **こちらのほうが広い**

向こうの実装（5 種）:

| 名前 | 内容 |
|---|---|
| none | 無変換 |
| `.norm_log2` | `log2(X + offset)`、offset 既定 `1e-9`。非有限は NA へ |
| `.norm_sum` | TIC スケーリング。列和を目標値（既定: 全列和の中央値）へ揃える |
| `.norm_median` | 列中央値を目標値（既定: 全列中央値の中央値）へ揃える |
| `.norm_zscore` | 行方向 `(X - row_mean) / row_sd`、SD に `1e-12` の下限 |

全て `na.rm = TRUE` で集約し、NA は出力に残す。

こちら（[preprocessing.py](../../../lipidmix/analysis/preprocessing.py)）が既に持っているもの:
`detect_sample_roles` / `detect_qc_strata` / `blank_filter` / `normalize` /
`run_order_correlation` / `qc_drift_correct`（QC-RLSC 相当の移動中央値）/
`_qc_interspersion` / `qc_rsd_filter` / `detect_failed_qc` / `impute` /
`drop_samples_by_role` / `detection_rates`（gap-fill を除いた実検出率）。

**結論: 転用しない。** QC ドリフト補正・RSD フィルタ・失敗注入検出・検出率（gap-fill 区別）は
向こうに無い。**ただし `zscore` の SD 下限 `1e-12` のような数値的ガードは、こちらの実装と
突き合わせる価値がある**（ゼロ分散特徴での挙動）。

---

### 候補 8 —— mzTab-M 取り込み（`R/mztab_m_import.R`）: **仕様解釈はこちらが正しい**

向こうが読む区画: `MTD` / `SMH`+`SML` / フォールバックで `SFH`+`SMF`。
**`SME` は明示的に扱っていない。**

名前の優先順: `"Metabolite name"` → `"chemical_name"` → `"SML_ID"` / `"SMF_ID_REFS"`。
サブクラス: **`"opt_global_Ontology"`**（MS-DIAL 標準エクスポート）→ `"Ontology"` / `"ontology"` → `"subclass"`。
強度: `abundance_assay[n]`。群は `study_variable[n]-assay_refs`（パイプ区切り）。

MS-DIAL 固有の回避策:

1. `"null"` を NA として扱う
2. 名前や m/z の欠損が 50% を超えたら次の識別子候補へ自動フォールバック（name → mass → SML_ID）
3. 名前や質量の重複はアダクトを付けて一意化（`"name_[M+H]+"`）。なお衝突すれば SML_ID を付す
4. `SMH` / `SFH` の直下 1 行を列名として使い、タグ列自体も残す
5. Ontology 情報が無ければ `rgoslin` でクラス推定。**部分成功を受け入れる**（失敗行は `"Unknown"`）
6. SML が空なら SMF へ自動切替
7. 特徴数と長さが合わない rowData 列は拒否

**こちらの実装との差（`lipidmix/mztab/`）**:

[dataset_state.py](../../../lipidmix/mztab/dataset_state.py) にこう書いてある ——
「mzTab-M 2.0.0-M では**構造・名称は SME セクションにしか無い**。SMF が持つのは
`SMF_ID` / `SME_ID_REFS` / `exp_mass_to_charge` / `retention_time_in_seconds` /
`abundance_assay[N]` で、`database_identifier`・`smiles`・`inchi`・`chemical_name` は SME 専用」。
こちらは `_index_sme_rows()` / `_best_evidence()` で SME を rank 順に引いている。
[reader.py](../../../lipidmix/mztab/reader.py) は `SEH`→`SME` / `SFH`→`SMF` / `SLH`→`SML` の
非標準ヘッダ読み替えと、SME 行末尾の空欄除去（既知の MS-DIAL 問題）まで持っている。

**結論**:

- **向こうの mzTab-M 取り込みは移植しない。** SME を読まない設計なので、
  MS-DIAL の実エクスポートでは注釈を取りこぼす可能性がある（こちらが SME 専用と明記した列を
  SML/SMF から読もうとしている）。
- **ただし 2 点は取り込む価値がある**:
  1. **`opt_global_Ontology`** —— こちらの mzTab 経路は `opt_global` を一切見ていない
     （grep でヒット 0）。現在クラストークンは `.arf` 由来の evidence 結合で得ているので、
     **`.arf` が無い場合にクラスが付かない**。この列を読めば mzTab 単独でクラスが取れる。
     → §4 の未検証項目。
  2. **重複名のアダクト付き一意化**。同名異アダクトの特徴が並ぶのは MS-DIAL では普通で、
     `feature_ids` の一意性に関わる。

---

### 候補 9 —— 相互運用: MSLipidMapper を下流の GUI として使う

向こうの入口は CSV / mzTab-M / 汎用 CSV 2 枚のみで、バイナリは読めない。
こちらは `.arf` `.arf2` `.pai2` `.dcl` `.EIC.aef` を読み、Console でベンダ生データから
mzTab-M を作るところまで持っている。

**なぜ使えるのか**: `dataset_export_differential` の隣に「MSLipidMapper が読める CSV」を吐く出口を
足すだけで、**同研究室の既存 GUI をこのサーバの下流として使える**。汎用入力（abundance CSV +
lipid→Ontology 対応表）の形が一番簡単で、多段ヘッダの MS-DIAL CSV を偽造する必要はない。

**注意（厳守）**: [export_contract.py](../../../lipidmix/analysis/export_contract.py) は
massbank-context との**別リポジトリ間契約**で、`CONTRACT_VERSION` の引き上げと下流の同時更新なしに
列を触ってはいけない。MSLipidMapper 向けは**別関数・別列定義**にする。

---

### 候補 10 —— 隣接ペアの不一致検出: グラフを「図」ではなく「文」に変える（本リポジトリ側の発案）

**これは MSLipidMapper に無い。** 向こうはノードを塗った図を人間に目視させる設計だが、
MCP サーバの相手は LLM なので、同じ操作を明示的な計算にしたほうが価値が出る。

**発想の根拠 —— 図は人間向けで、LLM 向けではない。**
CLAUDE.md の「サーバで描いて画像で返すほうが 2 桁安い」は**このケースには転用できない**:

| 返し方 | トークン | LLM から見た精度 |
|---|---|---|
| PNG | 約 327 画像トークン（volcano の実測値） | 45 ノードの小さなラベルと色を確実には読めない |
| 26 行の表（クラス / log2FC / q / 検出種数） | 同程度 | 値が正確に読める |

volcano は点が数万あるので画像しか選べないが、`remodeling_lipidonly.cyjs` の脂質ノードは
**26 クラス**。**トークンで得をせず、精度で負ける。**

**では `.cyjs` から LLM が受け取るべきものは何か**: 座標ではなく**隣接関係**。
エッジ 1 本ごとに両端の動きを突き合わせ、方向が食い違う組だけを挙げる。

> `PC`（log2FC −0.8, q 0.003）→ `LPC`（log2FC +1.2, q 0.001）は隣接。**方向が不一致** ——
> リモデリング（PLA2 側）の亢進と整合する。

**なぜ使えるのか**:

1. 約 60 本のエッジに対して機械的に計算でき、出力は「不一致な隣接ペア」数本のリスト。
   **表よりさらに小さく、しかも解釈の仮説そのもの。**
2. 「隣接している」という主張だけは `.cyjs` に書いてあることで、
   **不一致の生化学的解釈は LLM がやる** —— 役割分担が正しい向きになる。
3. 出典の弱さ（自作テンプレート）と両立する。断定ではなく「隣接するこの 2 クラスが逆方向に動いている」
   という**観察の提示**なので、文献未参照であることを開示したまま出せる。

**想定するツール構成**:

| ツール | 戻り値 | 相手 |
|---|---|---|
| `pathway_project` | ノード表（クラス / log2FC / q / 検出種数）＋ **不一致隣接ペアのリスト** | LLM |
| `save_pathway_figure` | 座標から matplotlib で PNG を保存し、**パスだけ返す** | 人間 |

座標は `lipidmix/core/session_state.py` に保持し、図保存ツールがそこから読む。
CLAUDE.md の「座標配列など巨大な中間データは payload から外してセッションに保持する
（図保存ツールがそこから読む）」および既存の `save_volcano_figure` / `save_pca_figure` /
`save_eic_figure` の型にそのまま乗る。

**注意**: 「不一致」の判定に q 値の閾値が要る。片方が有意でないペアを不一致と呼んではいけない。
`preprocessing.run_order_correlation()` の「語れないものは None を返す」流儀を踏襲し、
判定不能なエッジは判定不能として出す。

---

## §3 取らない判断（と理由）

| 取らないもの | 理由 |
|---|---|
| Chrome/Chromium ヘッドレスで Cytoscape.js → **PDF** レンダリング | 設計思想（座標を LLM に渡さずサーバで描く）は CLAUDE.md の規律と一致するが、**PDF は LLM が読めない**。Chrome 依存も重い。`lipidmix/plots/` で PNG を描く（画像トークン = 幅×高さ/750。dpi を上げない） |
| `SummarizedExperiment` 相当の再実装 | `DatasetState` が同じ役割。**ただし「パース済みアシル鎖を feature メタデータとして同じ構造に載せる」一点は真似る**（`DatasetState` に `acyl_chains` / `sphingoid_bases` を持たせる） |
| Shiny / Cytoscape.js の対話 UI | MCP はツール引数が UI。別リポの WebUI（Use-LLLM）が描くなら payload を返す既存の仕組み（`LIPIDMIX_PLOT_OUTPUT=payload`）で足りる |
| `normalize.R` の 5 メソッド | 候補 7。既存の `preprocessing.py` が上位互換 |
| `mztab_m_import.R` | 候補 8。SME を読まない |
| `ropls`（OPLS-DA） | 既存は PCA と差次的解析。OPLS-DA は過学習の説明責任が重く、LLM に解釈させる相手として不適 |
| `ComplexHeatmap` 由来のヒートマップ | 行列を返すことになりやすい。図で返す既存方針と衝突しない設計が別途必要 |
| **パスウェイ図を LLM への主たる戻り値にすること** | 候補 10。ノード 26 個では PNG がトークンで得をせず、LLM の読み取り精度で負ける。**図は人間向けの副産物**にし、LLM には表と隣接不一致リストを返す |
| ノード内に分子種分布（dot / box / violin）を描いて人間に目視させる設計 | 向こうの主機能だが、目視の代わりに候補 10 で計算する。図が要るなら `save_pathway_figure` の中の話に留める |

---

## §4 未検証の点（実装前に確かめること）

**解決済み（2026-09-04、Slack で著者に直接確認）**: 「なぜネットワークを DB ではなくファイルで
同梱しているのか」 → 自作テンプレートの**レイアウト（座標）ファイル**であり DB とは別物。
回答の全文と帰結は §2 候補 1 の「開発者からの回答」に記載。**§2 候補 1 の位置づけをこれに合わせて訂正した。**

1. **`opt_global_Ontology` が MS-DIAL の実エクスポートに本当に出るか。**
   このマシンに `.mztab` の実ファイルが無く（`data/` には `reports` のみ、リポジトリ内も 0 件）、
   確認できていない。**向こうのコードが「MS-DIAL standard export」と書いていることが唯一の根拠。**
   実走で mzTab-M を作ったときに `SMH` / `SFH` のヘッダを見る。
2. **`.cyjs` のクラストークンが MS-DIAL の `Ontology` 語彙と一致するか。**
   `EtherPS` のような表記が MS-DIAL 側で `PS` + エーテル表記なのか `EtherPS` なのか。
   一致しないなら語彙の対応表が 1 枚要る（`reference/` 行き）。
   `remodeling_lipidonly.cyjs` の脂質クラス 26 個を MS-DIAL の Ontology 実測値と突き合わせるだけ。
3. **中間/接合ノード（`Node1`、`Width/Height` が 1.0）の意味。** 描画上のベンドポイントと思われるが、
   投影時に「値が付かないノード」として扱う必要がある。
4. `lipid_rules.yaml` の全文を読んでいない（パターン約 20 本の要約のみ）。移植時に全文を読む。
5. `inst/extdata/examples/pathway_projection/` の中身（CLI の出力例と思われる）は未確認。
6. `mod_utility_ontology_builder.R` / `mod_utility_pathway_editor.R` は名前と役割だけ把握。
   前者は `reference/lipidmaps_classes.tsv` の拡充に、後者は `.cyjs` の自作に関係しうる。
7. `Enrich_module.R` の `Chain:` 項目が**どの粒度の鎖トークン**で作られるか未確認
   （`20:4` そのままか、炭素数と二重結合数に分解するか）。候補 4 の CCD とも整合を取る必要がある。
8. **テンプレート転用の事前共有**。MIT が既に許諾しているので**法的には不要**だが、
   `.cyjs` は著者の手描きの成果物であり、Slack で会話が通じている状況なので
   「別システムに組み込む」ことを一言伝えておく価値がある。§4-1 の
   `opt_global_Ontology` の確認も同じ場でついでに聞ける。

---

## §5 実装するときの依存順（計画ではなく、前後関係だけ）

```
候補 3（アシル鎖パーサ）
   ├─→ 候補 2 の `Chain:` 項目
   ├─→ 候補 4（CCD）
   └─→ 候補 5（サブセット再投影）

候補 1（.cyjs 同梱 = ノード集合・エッジ・座標）
   ├─→ 候補 10（隣接不一致の検出 ＋ save_pathway_figure）── 座標とエッジだけで動く
   │      └─→ 候補 5（アシル鎖サブセットで塗り直す）※候補 3 も必要
   └─→ 候補 6（酵素ノードへの重畳）

候補 2（クラス単位 Fisher + BH）── 独立に着手できる（既存の差次的 TSV だけで動く）
```

**最も割がいい最初の一手は候補 2**。既存の `export_contract.py` の TSV だけで動き、
新しい依存も新しいパーサも要らず、§0 の「濃縮が全部 q=1.0」を実際に解く。

**次が候補 1 + 10**。候補 2 と独立に着手でき、必要なのは `.cyjs` の vendor と
クラストークンの照合（§4-2）だけ。アシル鎖パーサ（候補 3）を待たない。
ここまでで「クラス単位の濃縮」と「隣接関係の観察」という 2 種類の解釈材料が揃う。

その後が候補 3（ここから工数が上がる）→ 候補 4・5。候補 6 は最後（土台が要る）。

---

## §6 出典

| 参照先 | 内容 |
|---|---|
| <https://github.com/systemsomicslab/MSLipidMapper> | リポジトリ本体。README・`R/`（42 ファイル）・`inst/extdata/examples/` |
| `LICENSE` | MIT。Copyright (c) 2024, Takaki OKA |
| <https://www.biorxiv.org/content/10.64898/2026.05.21.726751v1> | プレプリント。Oka, Nishida, Harayama, Tsugawa。設計意図（subclass 粒度・構造サブセット・multi-omics 重畳） |
| `~/.claude/kb/facts/pubchem-pathway-coverage-is-too-low-for-lipids.md` | §0 の実測。PubChem 経路が成立しない根拠 |
| [docs/output_format/identity.md](../../output_format/identity.md) | 既存同定層（pygoslin 正規化・参照表・MSI レベル）の範囲 |
| [lipidmix/analysis/preprocessing.py](../../../lipidmix/analysis/preprocessing.py) | 既存前処理/QC の範囲（候補 7 の比較対象） |
| [lipidmix/mztab/dataset_state.py](../../../lipidmix/mztab/dataset_state.py) | SME 専用列の扱い（候補 8 の比較対象） |
| [lipidmix/analysis/export_contract.py](../../../lipidmix/analysis/export_contract.py) | 差次的 TSV の列定義（候補 2 の入力、候補 9 で触ってはいけない対象） |
