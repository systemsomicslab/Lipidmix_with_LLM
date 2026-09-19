# MS/MS スペクトル照合（参照ライブラリとの突き合わせ） 設計

- 日付: 2026-09-19
- 発端: 親水性メタボロミクスでは MS/MS 照合と統計検証が揃えば実用に足りるが、
  **照合そのものが無い**。`.dcl` の実スペクトルは読めるのに、参照スペクトルを
  持ってくる経路も、突き合わせて点数と図を出す経路も存在しない。
- 関連: [mzTab-M の SML 注釈](2026-09-17-mztab-sml-annotation-design.md)、
  [LC–MS メタボロミクス v2](2026-09-15-validated-lcms-metabolomics-design.md)

## 1. 目的と完成条件

**1 つの feature について「付いている名前は本当か」を、参照スペクトルとの突き合わせで
検証できるようにする。** MS-DIAL の peak spot 画面が出す `Deconvolution vs. Reference`
の対向プロットと同じ判断材料を、MCP 経由で出す。

完成条件:

1. 解析出力フォルダから参照ライブラリを解決し、precursor m/z と極性で候補を集め、
   全候補を採点して上位を返せる。
2. 個別スコアの定義が **MS-DIAL と一致**している。このうち simple / weighted /
   reverse dot product と matched peaks count / percentage の 5 つは mzTab-M の
   `id_confidence_measure[4..8]` と同じ土俵で比較できる。spectral entropy similarity は
   定義は揃えるが **mzTab に出ないため突き合わせ先が無い**（2.7）。
3. 測定と参照の対向プロットを画像で返せる。
4. `verify_peak_annotation` が、MS/MS の有無（`PASS` / `FLAG_ONLY` / `ABSENT`）に
   加えて照合結果を証拠として載せられる。
5. 実データ（`.dcl` + `.dbs` + mzTab）で、移植したスコアが MS-DIAL の出力と一致する
   ことを確認済みである。

## 2. 背景と根拠（実測）

上流は `C:\Users\yuu18\source\repos\MsdialWorkbench`（`master`、`afd5f9522`）。
以下はすべて実ファイルまたは上流コードで確認した事実で、推測を含まない。

### 2.1 メタボロミクス経路が参照するのは `.msp` である

`LibraryHandler.ReadMsLibrary`（`src/MSDIAL5/MsdialCore/Utility/LibraryHandler.cs`）は
拡張子で分岐する。`.lbm` / `.lbm2` は `ReadLipidMsLibrary` にしか到達せず、その関数は
`LbmQuery`（脂質クラスの選択リスト）を必須の絞り込みに使い、**選択が空なら空リストを
返す**。`TargetOmics.Metabolomics` からこの関数を呼ぶ経路は存在しない。

メタボロミクスは `MspFilePath` → `MspFileParser.MspFileReader`（ASCII `.msp`）→
`MoleculeDataBase(SourceType.MspDB)` → `LcmsMspAnnotator` を通る。ほかに Text DB
（`.txt`、`TextLibraryParser`）の経路があるが、これは m/z と付加イオンだけで
**参照スペクトルを持たない**。

### 2.2 参照ライブラリは解析出力に同梱されている

`MsdialDataStorage.cs` がプロジェクト名から 3 つのファイル名を導く。

| 導出 | ファイル | 中身 |
|---|---|---|
| `GetNewMspFileName` | `<Project>_Loaded.msp2` | 読み込んだ MspDB のシリアライズ |
| `GetNewZippedDatabaseFileName` | `<Project>_Loaded.msp2.zip` | 同じものの zip |
| `GetDataBasesFileName` | `<Project>_Loaded.msp2.dbs` | **DataBaseStorage**（DB 一式＋注釈器パラメータ） |

`.dbs` は `DataBaseStorage.Save` が `ZipArchive` で書く **ZIP** である。実測
（`C:\Users\yuu18\datasets\a_lipidome_landscape_of_aging_in_mice\rplc\kidney\neg\`。
**リポジトリ外**の GUI 実行成果物）:

```
    212,980,700 B   MetabolomicsDB/Msp20260116160945_NCDK_conventional_converted_dev/DataBase
              0 B   MetabolomicsDB/MS-FINDER/DataBase
            408 B   Storage
```

`DataBase` エントリの中身は `.lbm2` と**同一の枠組み**で、1,406,352 レコードだった。
全件 `IonMode=1`（negative）、付加イオンは `[M-H]-` 829,705 / `[M+CH3COO]-` 518,565 /
`[M-2H]2-` 55,341。**その run 用に極性とクラスで絞り込み済み**である（元の `.lbm2` は
5,180,324 件）。

**重要な制約**: `.dbs` は**プロジェクト保存時にしか書かれない**。Console 実行の出力
（`data/kanzo_multiomics/.../console/attempt-0001/msdial/`）には存在せず、`.mdmsp` /
`.mdpeak` だけだった。Smart App Control のために `save_project=false` を使う環境
（[metabolomics.md](../../workflow/metabolomics.md)）では、この経路から `.dbs` は
得られない。`.msp` を並列で実装する理由がこれである。

### 2.3 `_Loaded.msp2` が 0 バイトなのは異常とは限らない

同フォルダの `Dataset_..._Loaded.msp2` は 0 バイトだった。`.msp2` は **ASCII `.msp` を
指定したときだけ**中身が入るので、脂質（`.lbm2` 経路）の run では常に 0 バイトになる。

`kb/failures/msdial-console-empty-lbm-path-zero-annotations.md` は「`_Loaded.msp2` が
0 バイト」を同定ゼロの確認手順に挙げているが、**0 バイト単独では異常の指標にならない**。
指標になるのは `.dbs` 側が空であること、および `grep -c '^SME'` が 0 であること。
KB を訂正する。

### 2.4 シリアライズの枠組み（`.lbm2` / `.dbs` の `DataBase` 共通）

`src/Common/CommonStandard/MessagePack/LargeListMessagePack.cs`。
`ExtensionTypeCode = 99`、`HeaderSize = 11`、`OffsetCutoff = 1073741824`。

ファイルはチャンクの列で、各チャンクは:

```
c9                    MessagePack ext32
<4 bytes BE>          ext 長（= LZ4 本体長 + 5）
63                    type 99
d2 <4 bytes BE>       展開後サイズ（int32）
<本体>                LZ4 block（frame ではない）
```

展開すると MessagePack の配列が現れ、要素が `MoleculeMsReference` である。

**踏んだ罠**: 展開後の先頭 5 バイトは配列ヘッダのために予約されているが、
`MessagePackBinary.WriteArrayHeader` は**要素数が小さいと短形式**（`0xdc` / fixarray）
で書く。したがってヘッダを常に 5 バイトの `0xdd` と仮定すると最終チャンクで壊れる。
ヘッダは先頭バイトを見て解釈し、**要素は常にオフセット 5 から始まる**
（`DeserializeList` が `offset = 5` を固定している）。

実測（`data/kanzo_multiomics/library/Msp20240820100242_NCDK-TUAT-LC25_converted_dev.lbm2`）:
779,105,721 B / 4 チャンク / 展開後 3.52 GB / **5,180,324 レコード**。
Python の `lz4.block` + `msgpack` で読めることを確認済み（LZ4 展開は 1 GB あたり約 0.4 秒）。

### 2.5 `MoleculeMsReference` の Key 番号

`src/Common/CommonStandard/Components/MoleculeMsReference.cs`。0–28 で、**27 が飛び番**
（`DatabaseUniqueIdentifier` が 20 と 21 のあいだに書かれている）。正準表は
`docs/schema/` に置く。本設計が使うのは以下。

| Key | 名前 | Key | 名前 |
|---|---|---|---|
| 0 | ScanID | 9 | InChIKey |
| 1 | PrecursorMz | 10 | AdductType |
| 2 | ChromXs | 14 | CompoundClass |
| 3 | IonMode | 15 | Comment |
| 4 | Spectrum | 17 | InstrumentType |
| 5 | Name | 19 | CollisionEnergy |
| 6 | Formula | | |
| 7 | Ontology | | |
| 8 | SMILES | | |

### 2.6 `.dbs` の `Storage` が持つ許容幅

`Storage` エントリ自体も ext99 + LZ4 で包まれた MessagePack である。復号すると、
使った DB の名前・元ファイルの絶対パス・注釈器のパラメータが入っている。

観測値を `MsRefSearchParameterBase`（`src/Common/CommonStandard/Parameters/`）の
`[Key]` 表と突き合わせたところ**完全に整合**した（既定のままの項目が既定値と一致し、
変更された項目だけが異なる）。

| Key | 名前 | 既定 | この run |
|---|---|---|---|
| 0 / 1 | MassRangeBegin / End | 0 / 2000 | 0 / 2000 |
| 2 | RtTolerance | 100.0 | **2.0** |
| 5 | Ms1Tolerance | 0.01 | 0.01 |
| 6 | Ms2Tolerance | 0.025 | 0.025 |
| 9 / 10 | Squared{Weighted,Simple}DotProductCutOff | 0.36 | **0.0225**（= 0.15²） |
| 11 | SquaredReverseDotProductCutOff | 0.64 | **0.09**（= 0.3²） |
| 12 | MatchedPeaksPercentageCutOff | 0.25 | **0.0** |
| 13 | TotalScoreCutoff | 0.8 | 0.8 |
| 14 | MinimumSpectrumMatch | 3 | **1** |
| 15 / 16 | IsUseTimeForAnnotation{Filtering,Scoring} | false | **true** |

照合の既定許容幅をここから取る。`IsUseTimeForAnnotationFiltering=true` の run では
MS-DIAL が RT でも候補を絞っているので、候補集合を再現するには RT も使う必要がある。

### 2.7 mzTab-M に出ている個別スコア

`MztabFormatExport.cs` `SetIdConfidenceMeasure`。LCMS では 8 本が固定順で出る。
実データ（`Height_AlignmentResult_....mzTab`）の SEH 行に 8 列すべて実在する。

```
[1] MS-DIAL algorithm matching score     [5] Weighted dot product
[2] Retention time similarity            [6] Reverse dot product
[3] m/z similarity                       [7] Matched peaks count
[4] Simple dot product                   [8] Matched peaks percentage
```

書かれる値は `matchResult.SimpleDotProduct` などの**平方根側**である
（`MsScanMatchResult` は二乗値を保持し、非二乗プロパティが `sqrt` を返す）。
`EnhancedDotProduct` と `SpectralEntropy` は mzTab に**出ない**。

現在 `lipidmix/mztab/dataset_state.py` は `best_id_confidence_value`（= `[1]` の
total のみ）しか読んでおらず、`[2..8]` を捨てている。

## 3. 範囲

### 3.1 含むもの

- `.dbs` / `.msp` の読み取りと、正規化レコードへの変換。
- sha256 を鍵とする永続 store の構築・展開と、m/z 窓 × 極性での候補取得。
- 採点エンジン（MS-DIAL と定義が一致する 4 種 + entropy）。
- 対向プロットの組み立てと描画。
- MCP ツール 3 本の新設と `verify_peak_annotation` の拡張。
- mzTab の `id_confidence_measure[2..8]` の回収（比較対象として必要なため）。

### 3.2 含まないもの

- 全 feature の再採点と乖離レポート。採点エンジンを周回するだけで足せる形にはするが、
  今回は作らない。
- MassBank provider。候補取得を境界にしておくので、採点と描画を触らずに足せる。
- 未同定 feature への新規同定（索引構造・FDR 的判断が別途必要）。
- `MS-DIAL の TotalScore の再現`。重み付けは上流の版差に追随する負債になるため追わない。
- pipeline への stage 追加。
- `.lbm2` を直接指定する経路。`.dbs` と枠組みが同一なので読めはするが、入口としては
  出さない（脂質の in-silico ライブラリであり、本設計の目的に合わない）。

## 4. 構成

既存の構成規約は「形式ごとに `lipidmix/<形式>/`」だが、`.dbs` と `.msp` は**同じ
レコード形と同じ store を共有する 2 つの入口**にすぎない。形式ごとに分けず 1 パッケージに
まとめる。規約からの意図的な逸脱であり、`CLAUDE.md` の構成図に理由を添える。

```
lipidmix/library/dbs.py      .dbs（ZIP → LargeListMessagePack）→ レコード列
lipidmix/library/msp.py      .msp（テキスト）→ レコード列
lipidmix/library/store.py    sha256 鍵の永続 store。構築・展開・候補取得
lipidmix/library/tools.py    MCP 公開層（__all__ に載せる）
lipidmix/analysis/spectral_match.py   採点（純関数）
lipidmix/plots/mirror.py     対向プロットの payload 組み立てと描画
lipidmix/core/path_resolvers.py       resolve_library_path を追加
```

- `lipidmix/core/mcp_core.py` は leaf のまま。ここから `lipidmix.library.*` を
  import しない。
- セッションスロットは `session.library` を新設する。既存スロット
  （`session.arf` / `.arf2` / `.pai2` / `.eic`）には触らない。

## 5. 正規化レコードと store

### 5.1 レコード

2 つの入口が吐く形を揃える。これが store のスキーマであり、採点エンジンの入力契約。

```
name, precursor_mz, ion_mode, adduct, rt|None,
formula, inchikey, smiles, compound_class, ontology,
spectrum: [[mz, intensity], ...],
library_id, record_index
```

`ion_mode` は上流の `IonMode` 列挙（`{Positive=0, Negative=1, Both=2}`、
`src/Common/CommonStandard/Enum/CommonEnums.cs`）をそのまま持たず、**レコードには
`"positive"` / `"negative"` の文字列で持つ**。`.dbs` 由来の整数はここで変換する
（negative の実 run が全件 `1` であることを確認済み）。

`.msp` 側のフィールド別名は上流 `MspFileParcer.cs` の `switch` を写す（約 40 ケース。
`precursormz` / `precursor_mz` / `precursor_m/z`、`num peaks` / `numpeaks` /
`num_peaks`、`inchikey` / `inchi_key` / `inchi key` など）。

### 5.2 なぜ永続 store か

`.dbs` の LZ4 block は**チャンク単位でしか展開できず**、チャンク内の N 番目のレコード
だけを読む手段がない（`DeserializeAt` も結局チャンクを丸ごと展開して読み飛ばす）。
したがって全走査は避けられない。

一方、必要な情報は元の 1% 以下である。容量の大半は `SpectrumPeak` が 1 ピークあたり
13 フィールドを持つことに由来し、本設計が使うのは `[m/z, intensity]` だけ。

よって**初回に 1 度だけコンパクトな store へ変換し、以後はそれを開く**。

- 鍵は元ファイルの **sha256**。`lipidmix/analysis/feature_bindings.py` が既に
  `library_sha256` を持っており、そこと整合する。
- 置き場は `LIPIDMIX_LIBRARY_CACHE_DIR`。既定は `<LIPIDMIX_DATA_DIR>/.library-cache`
  （`data/` は追跡外なので、キャッシュが git に載ることはない）。他の蓄積先と同じく
  env で上書きできるようにする（NAS 共有運用のため）。
- `.dbs` も `.msp` も同じ store に着地する。採点と描画は store の先だけを見るので、
  将来 provider が増えても触らない。

`.dbs` 由来のときは `Storage` 由来の許容幅（2.6）を store のメタに保存する。
`.msp` 由来のときはこのメタが無いので、既定値を使った旨を戻り値に明示する。

## 6. 採点

`lipidmix/analysis/spectral_match.py`。純関数のみ。

### 6.1 走査（これも定義の一部）

4 つの関数は素直な内積ではなく、**固定幅でない m/z グリッドの上を歩く独自の走査**を
含む。カーソル `focusedMz` を進めながら `[focusedMz − bin, focusedMz + bin)` に入る
ピークを両側から合算する。ピークの 1 対 1 対応を取らないので、**同じピークが隣り合う
2 つの窓に二重計上されうる**。

| 関数 | カーソルが歩く対象 |
|---|---|
| weighted / simple | 両スペクトルのピーク m/z の和集合 |
| reverse / matched peaks | **参照スペクトルのピーク m/z だけ** |

reverse が「参照のピークが測定側で説明できているか」を問う量になるのは、この
非対称性による。

`bin` は `Ms2Tolerance`（`.dbs` 由来なら 0.025 Da）。

### 6.2 式

各窓の合算値を `mᵢ`（測定）・`rᵢ`（参照）とし、それぞれ自分の最大値で正規化したうえで:

```
weighted  = cov² / (scalarM · scalarR) × penalty        mᵢ < 0.01 の窓を捨てる
              scalarM = Σ mᵢ·mzᵢ,  scalarR = Σ rᵢ·mzᵢ,  cov = Σ √(mᵢ·rᵢ)·mzᵢ

reverse   = 同じ式。捨てるのは rᵢ < 0.01 の窓。カーソルは参照グリッド

simple    = cov² / (scalarM · scalarR)                  m/z 重みなし・penalty なし・cutoff なし
              scalarM = Σ mᵢ,  scalarR = Σ rᵢ,  cov = Σ √(mᵢ·rᵢ)

matched   = [counter / libCounter, counter]
              libCounter = 参照側合算が「参照の生の最大強度の 1%」以上の窓の数
              counter    = そのうち測定側合算 > 0 の窓の数
              libCounter == 0 のとき [0, 0]

entropy   = 1 − (2·S₁₂ − S₁ − S₂) / 2      S(x) = −Σ p log₂ p,  p = 強度 / 総和
              S₁₂ は両者を総和 1 に正規化してから bin で合成したスペクトルのエントロピー

penalty（weighted と reverse のみ）
  正規化強度 > 0.1 の参照窓が 1 個→0.75 / 2 個→0.88 / 3 個→0.94 / 4 個→0.97 / 他→1.0
```

**3 つの dot product は二乗値を返す。** mzTab と比較するときは `sqrt` を取る（2.7）。

### 6.3 そのまま写す瑕疵

移植の目的が数値の一致である以上、**直してはいけない**。実装の docstring に
「上流の写しであり意図的に残している」と明記し、後から善意で修正されないようにする。

- weighted と reverse に `wM` / `wR` の計算があるが**どこにも使われていない**。
  実装しない。
- weighted の `if (sumM <= 0 && sumR > 0)` 分岐と `else` 分岐は**中身が同一**。
  条件ごと落とす。
- simple の `× 999` は比になる時点で**打ち消える**。写すが無意味である旨を書く。
- 窓の二重計上（6.1）は仕様として維持する。
- entropy に Li et al. 2021 の低エントロピー重み変換は**入っていない**。足さない。

### 6.4 足すもの

新しい指標は導入しない。加えるのは**アラインメント**だけ — どの参照窓がどの測定ピークに
対応したか。対向プロットが注釈を打つのも、人が読んで納得するのもここで、数値には
現れない。

## 7. 描画

`lipidmix/plots/mirror.py` が payload を組み、`lipidmix/plots/render.py` が描く。

上段が測定（上向き）、下段が参照（下向き）、横軸 m/z 共通。一致した窓に印を付け、
強度上位のフラグメントにだけ m/z ラベルを置く。

既存の描画規約に従う。**座標列は payload に載せずセッションに保持**し、図保存ツールが
そこから読む。dpi は上げない（画像トークン = 幅 × 高さ / 750）。
`LIPIDMIX_PLOT_OUTPUT=payload` のときは座標を返す既存の分岐に乗る。

## 8. MCP ツール面

| ツール | 役割 | `readOnlyHint` |
|---|---|---|
| `library_load` | `.dbs` / `.msp` を解決し store を構築または展開。`session.library` へ | **False**（キャッシュを書く） |
| `library_match_feature` | `.dcl` から測定スペクトルを引き、候補を集め、全候補を採点して上位を返す | True |
| `library_plot_mirror` | セッションの座標から対向プロットを描く | True |
| `verify_peak_annotation` | `session.library` があるとき `spectral_match` ブロックが増える | 既存のまま |

- 全ツールに `structured_output=False`。戻り値は `json_payload()`。
- 候補一覧は TSV（列名 1 回）。float は丸める。既定の候補数は 5 件。
- スペクトルは一致した窓と上位のみ返す。

`verify_peak_annotation` の `analytical_checks.msms` は **`PASS` / `FLAG_ONLY` /
`ABSENT` の 3 状態を維持**し、その内側に `spectral_match` を足す。`FLAG_ONLY` を
`PASS` と同一視しない既存の契約を壊さない。

前提が無いとき（`session.library` 未設定、`.dcl` 未添付）は例外ではなく
`missing_state` 封筒を返し、`required_tools` に `library_load` を載せる。

`dcl_find_msms` の `not_found` は「MS/MS 未取得」であって「期待フラグメントが無い」では
ない、という既存の区別を照合経路でも維持する。

## 9. 検証

### 9.1 実データとの突き合わせ（受け入れ試験）

**MS-DIAL を再実行せずに移植の正しさを検証できる。**
`C:\Users\yuu18\datasets\a_lipidome_landscape_of_aging_in_mice\rplc\kidney\neg\`
に材料が揃っている。

| 要素 | 出所 |
|---|---|
| 測定スペクトル | 各 `.dcl` |
| MS-DIAL が選んだ参照レコード | `Dataset_..._Loaded.msp2.dbs` |
| MS-DIAL が出した答え | `Height_AlignmentResult_....mzTab` の `id_confidence_measure[4..8]` |
| 使った許容幅 | `.dbs` の `Storage` |

同じ入力から計算して `[4] Simple` `[5] Weighted` `[6] Reverse` `[7] count`
`[8] percentage` が一致するかを突き合わせる。合成 fixture の緑ではなく、**上流の実出力
との一致**で担保する。

これは CLI として用意し、**pytest には入れない**。`data/` は追跡外、
`C:\Users\yuu18\datasets\` はリポジトリ外なので、依存させるとクリーンチェックアウトで
落ちるユーザ環境依存の不安定テストになる。実行結果は `docs/HISTRY.md` に記録する。

この検証が使うのは**脂質の run** である（親水性の実 run にはまだ `.dbs` が無い）。
照合エンジンと `.dbs` 読み取りの正しさはこれで担保できるが、**親水性メタボロミクスでの
実用性は別途確認が要る**（本設計の目的そのものは親水性にある）。合成入力での完走を
実用性の合格と読まない、という v2 と同じ注意がここにも当てはまる。

### 9.2 未解決の risk（実装の最初に片付ける）

MS-DIAL が採点にかけるのは `query.NormalizedScan` であって `.dcl` の生スペクトル
そのものではない（`MsReferenceScorer.Score` が `query.NormalizedScan` を渡している）。
あいだにどんな正規化が挟まるかを確認しないと、数値が合わない原因が「移植の誤り」なのか
「入力の違い」なのか切り分けられない。

**`NormalizedScan` の実体を上流で確認してから採点の移植に入る。** これを実装の第 1 工程と
する。

### 結論（2026-09-19）

上流 clone（`MsdialWorkbench`、既定ブランチ `master`、HEAD `afd5f9522`）を読んだ。
`IAnnotationQuery`（`IAnnotationQuery.cs`）の `NormalizedScan` は `AnnotationQuery.cs` /
`AnnotationQueryWithReference.cs`（後者は前者に委譲）が実装しており、両方とも
`DataAccess.GetNormalizedMSScanProperty(Scan, Parameter)`（`DataAccess.cs`）を呼ぶだけ。
LC-MS 経路（`StandardAnnotationProcess.cs` の `RunAnnotationCoreAsync` → LC-MS の
`PeakAnnotationProcess.cs` から呼ばれる）では、この `Scan` は `MSDecResult` インスタンス
そのもの（`MSDecResult : IMSScanProperty`）——つまり `.dcl` に永続化される
デコンボリューション済みスペクトルと同一のオブジェクトである。

`GetNormalizedMSScanProperty` は `ChromXs` / `IonMode` / `PrecursorMz` / `ScanID` を
そのままコピーし、`Spectrum` だけを `GetNormalizedMs2Spectra(spectrum, AbsoluteAmpCutoff,
RelativeAmpCutoff)` に通す。この関数がやっているのは**2 個だけ**:

1. **相対・絶対強度の足切り**: 各ピークについて
   `Intensity > maxIntensity * RelativeAmpCutoff && Intensity > AbsoluteAmpCutoff`
   を満たすものだけ残す（元の並び順のまま。**並べ替えはしない**）。
2. **強度の再スケール**: 生き残ったピークを `Intensity / maxIntensity * 100.0` に置き換える
   （最大ピークが 100 になる正規化）。元の強度は `Resolution` フィールドに退避される。

**precursor 近傍の除去は無い。m/z 範囲の制限も `NormalizedScan` の構築には無い**
（`GetNormalizedMs2Spectra` は `MassRangeBegin`/`MassRangeEnd` を一切参照しない）。
ただし m/z 範囲の制限自体は別の場所に実在する: `MsReferenceScorer.CalculateScore`
（`MsReferenceScorer.cs`）が `MsScanMatching.GetWeightedDotProduct` などの採点関数
（`MsScanMatching.cs`）を呼ぶときに `parameter.MassRangeBegin` / `MassRangeEnd`
（既定 0〜2000）を渡しており、ドットプロダクトの m/z ビニング内で
クエリ・参照の両スペクトルに対して掛かる。**`NormalizedScan` 自体には無いが、
採点関数の内部では効く**——Task 6 の `match_spectrum` は正規化ステップではなく
採点ステップでこの範囲を適用しないと数値が合わない。

`RelativeAmpCutoff` / `AbsoluteAmpCutoff` の既定値は両方 0
（`MsRefSearchParameterBase.cs`）。既定のままなら足切りは実質無効（強度 0 のピークだけ
落ちる）なので、**変換は「正規化（0〜100 スケール）」の 1 個だけに縮退する**。
ただし両パラメータは GUI から変更可能な検索パラメータ
（`MsRefSearchParameterBaseViewModel.cs`）であり、値がどこから来るかは
コード上ハードコードされていない——プロジェクトの手法パラメータの一部としてしか
確定しない。**本リポジトリ（Lipidmix_with_LLM）には現時点でこの 2 値を読む経路が
無い**（`lipidmix/` 配下に `RelativeAmpCutoff` 等への参照は無し、確認済み）。
既定値 0/0 を仮定して進めることはできるが、ユーザーが足切りを変更した run では
`match_spectrum` が数値不一致を起こす。この差は「移植の誤り」ではなく「入力パラメータの
未取得」に分類できるよう、Task 6/7 はこの 2 値の出所（`.dbs`/mzTab-M/手法テンプレートの
いずれかに露出しているか）を別途確認すること。

**判断: 続行。** 変換は `.dcl` の生スペクトルと 2 個のスカラーパラメータ
（既定 0/0）だけから決定的に再現できる。生データの再読み込みは不要。
Task 6 の `match_spectrum` にこの正規化（足切り→0〜100 再スケール、並び順維持）を
実装し、m/z 範囲の制限は採点関数側に実装する。上記のパラメータ出所の未解決点は
ブロッカーではなく Task 6/7 側での確認事項として引き継ぐ。

### 9.3 pytest

fixture はテスト自身が tmp に作る（既存規約）。

- `.msp` の別名表（上流の約 40 ケース）。
- `.dbs` のチャンク枠組み。特に**最終チャンクの array header が短形式になる**件を
  回帰として固定する（2.4 で実際に踏んだ）。
- 採点 4 関数 + entropy を手計算値に対して固定。**6.3 の瑕疵も含めて固定する**ので、
  善意の修正が入ると落ちる。
- 腐敗防止: `tests/test_server_registration.py` の登録数と `ToolAnnotations`、
  `tests/test_readme_links.py` が突き合わせる `USAGE.md` のツール集合と宣言件数、
  `tests/test_workflow_docs.py` が検証する新設 `docs/workflow/library.md`。

## 10. 実装順序

1. `query.NormalizedScan` の確認（9.2）。ここで前提が崩れたら設計に戻る。
2. `docs/schema/` に `MoleculeMsReference` の Key 番号表と `.dbs` / `.lbm2` の枠組みを
   書く。インデックス定数の正準はここという規約に従い、**実装より先に**置く。
3. `lipidmix/library/dbs.py` と `msp.py`。正規化レコードまで。
4. `lipidmix/library/store.py`。
5. `lipidmix/analysis/spectral_match.py`。
6. 9.1 の CLI で実データ突き合わせ。**ここが通るまで先へ進まない。**
7. `lipidmix/plots/mirror.py`。
8. `lipidmix/library/tools.py` と `verify_peak_annotation` の拡張。
9. mzTab `id_confidence_measure[2..8]` の回収。
10. 文書（11）。

## 11. 記録義務

この改修で腐るものが多い。同じ作業の中で直す。

- `docs/schema/` — Key 番号表（実装より先、上記 10-2）。
- `docs/output_format/` — 新トピック（照合スコアの意味、6.3 の瑕疵の告知）と
  MCP リソースへの登録。
- `docs/workflow/library.md` と `docs/workflow/index.md`。
- `USAGE.md` — ツール 3 本の追加と宣言件数。
- `CLAUDE.md` — 構成図に `lipidmix/library/` の 1 行（規約からの逸脱の理由つき）。
- vault の流れ図
  （`C:\Users\yuu18\Documents\KnowledgeVault\30_Projects\ms-data-parser\ms-data-parser-flow.md`）
  — `library_load` が前提状態の連鎖に入るので対象。末尾の出典行の日付も書き直す。
- KB の訂正 — `failures/msdial-console-empty-lbm-path-zero-annotations.md` の
  `_Loaded.msp2` 0 バイトの解釈（2.3）。
- `docs/HISTRY.md` / `docs/task.md`。

## 12. 決定の記録

議論の中で確定した判断と、その理由。

| 判断 | 理由 |
|---|---|
| 参照は `.dbs` を第一候補、`.msp` を並列 | `.dbs` はその run が実際に使った絞り込み済みの参照。ただしプロジェクト保存が無い Console 経路では得られない（2.2） |
| `.lbm2` を入口に出さない | 脂質専用の in-silico ライブラリで目的に合わない。枠組みは `.dbs` と同一なので読む能力は残る |
| スコアは MS-DIAL の**個別定義だけ**揃える | 比較可能性を取り、TotalScore の重み付けへの追随負債は負わない |
| 6.3 の瑕疵はそのまま写す | 数値の一致が目的。素直に書き直すと比較の土俵が消える |
| 適用範囲は 1 特徴の裏取り | 集計は同じエンジンを周回するだけで後から足せる |
| store は永続化 | `.dbs` は全走査が不可避で、必要な情報は元の 1% 以下（5.2） |
| `lipidmix/library/` に 2 形式をまとめる | 同じレコード形と同じ store を共有する 2 つの入口にすぎない |
