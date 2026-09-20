# 参照ライブラリ照合（MS/MS スペクトル照合）の出力フィールド

`library_load` / `library_match_feature` / `library_plot_mirror` が返す値の意味と、
`verify_peak_annotation`（`analytical_checks.msms.spectral_match`）に載る同じ形の値。

> 先に `lipidmix://docs/output-format`（共通核）を読むこと。行・列の粒度、脂質名文法、必須注意事項はそちらで定義され、ここでは繰り返さない。節番号は分割前の通し番号。

ツールが**どのファイルのどの関数をどの順に呼ぶか**は `docs/workflow/library.md`。
ここは**値の意味**だけを定義する。設計の根拠・実測は
[docs/superpowers/specs/2026-09-19-msms-spectral-matching-design.md](../superpowers/specs/2026-09-19-msms-spectral-matching-design.md)
にある。

## 14. 参照ライブラリ照合（`lipidmix/library/`, `lipidmix/analysis/spectral_match.py`）

`.dcl` の測定 MS/MS を参照ライブラリ（`*_Loaded.msp2.dbs` 優先、無ければ `*.msp`）と
突き合わせ、MS-DIAL の個別スコア定義をそのまま移植して数値を出す。目的は「付いている
名前は本当か」を人が対向プロットと数値の両方で判断できるようにすることで、MS-DIAL の
`TotalScore`（重み付け合成）は再現しない——個別スコアの定義一致だけを保証する。

### 14.1 各スコアの意味

`library_match_feature` の候補一覧・`library_plot_mirror` の材料・
`verify_peak_annotation` の `spectral_match.best_match` はすべて同じ 5 指標を使う。

| キー | MS-DIAL 対応 | 意味 |
|---|---|---|
| `simple_dot_product` | `GetSimpleDotProduct` | m/z 重みなし・ペナルティなしの単純ドット積 |
| `weighted_dot_product` | `GetWeightedDotProduct` | m/z 重みとピーク数ペナルティを掛けたドット積。**候補の既定の並び順（`ranked_by`）** |
| `reverse_dot_product` | `GetReverseDotProduct` | 参照側のピークだけを基準に測定側で説明できているかを問う非対称スコア |
| `matched_peaks_percentage` | `GetMatchedPeaksScores`（比） | 参照ピークのうち測定側でも観測された割合 |
| `matched_peaks_count` | 同上（count） | 一致した参照ピークの本数 |
| `entropy_similarity` | `GetSpectralEntropySimilarity` | スペクトルエントロピーに基づく類似度。**mzTab には出ないため実データとの突き合わせ対象外**（設計 §2.7） |

**`simple` / `weighted` / `reverse` の 3 つの dot product は平方根側の値**である
（`match_spectrum` が二乗値から `sqrt` を取って返す。単体関数
`weighted_dot_product()` 等はモジュール内部の二乗値を返す——混同しないこと）。
これは mzTab-M の `id_confidence_measure[4..6]` と同じ土俵で比較するための変換で、
`0.0`〜`1.0` の類似度として読める。

### 14.2 `-1` と `0` の区別（最重要）

**`-1` は「比較していない」、`0` は「合わなかった」。混同すると「照合していない」が
「合わなかった」に化ける。**

- `-1`（3 つの dot product・`matched_peaks_percentage`・`matched_peaks_count`・
  `entropy_similarity` いずれも）: 測定・参照どちらかのスペクトルが空で、
  そもそも比較できなかった。`entropy_similarity` も他の 5 指標と同じ
  `_is_compared_available` ガードを使い（`spectral_match.py` の
  `spectral_entropy_similarity`）、`match_spectrum` では `sqrt` を経ずに素通し
  するため `candidates_table` にも `-1.0` がそのまま出る。
- `0`: 比較はできたが、一致度がゼロだった（本当に合わなかった）。

**mzTab の値とは番兵の扱いが違う**ので、実データ突き合わせの際に注意する:

- `id_confidence_measure[4..6]`（simple/weighted/reverse dot product）は
  MS-DIAL 側で非二乗 getter が `Math.Sqrt(Math.Max(Squared*, 0f))` として `-1` を
  `0` にクランプしてから `sqrt` を取る。**したがって mzTab 上のこの 3 列では、
  比較不能は `-1` ではなく `0` として出ている。** この関数の `-1` と mzTab の `0` は、
  比較不能ケースに限り同一視してよい。
- `id_confidence_measure[7..8]`（matched peaks count / percentage）は素のフィールドで、
  クランプを経ないため `-1` のまま出る。こちらは `-1` 同士で素直に比較できる。

### 14.3 意図的に写した瑕疵（`lipidmix/analysis/spectral_match.py`）

移植の目的は MS-DIAL との**数値の一致**であって、実装の改善ではない。次の 5 点は
上流の「明らかに変な点」を**意図的にそのまま残している**。素直な実装に書き直すと
比較の土俵が消えるため、直さないこと（docstring にも明記済み）。

1. **`wM` / `wR` の計算が未使用のまま残っている（weighted/reverse）。** 上流にも
   これらの値を使う後続コードが無い。移植していない。
2. **weighted の `if (sumM <= 0 && sumR > 0) {...} else {...}` は両枝が同一処理。**
   条件ごと落として共通処理だけ残している。
3. **simple の `× 999` は比を取る時点で打ち消えるので数値に影響しない。** 忠実に
   写しているが意味は無い。
4. **窓の走査は固定幅グリッドではなく、同じピークが隣り合う 2 つの窓に二重計上され
   得るカーソル走査。** 素直な 1 対 1 アラインメントに書き直してはいけない
   （数値が変わる）。
5. **entropy に Li et al. 2021 の低エントロピー重み変換は入っていない。** 上流にも
   無いので足さない。

### 14.4 `.msp` 由来には許容幅が同梱されない

`.dbs` の `Storage` エントリには、その run が実際に使った許容幅
（`mz_tol` / `ms2_tol` / `rt_tol` 等、`docs/schema/molecule_ms_reference.md` の
`MsRefSearchParameterBase`）が入っている。`library_load` はこれを
`search_params` として store のメタに保存し、`library_match_feature` が
明示指定 > `search_params` > 既定値の順で使う。

**`.msp` にはこの情報が無い。** `.msp` 由来の store は `search_params=None` を持ち、
`library_load` の戻り値に既定値を使う旨の `note` が付く。既定値は
`lipidmix/library/defaults.py` の `DEFAULT_MZ_TOL` (0.01) / `DEFAULT_MS2_TOL` (0.025) /
`DEFAULT_RT_TOL` (0.2)。

### 14.5 `library_load`

`.dbs`/`.msp` を解決して SQLite store を構築（または既存キャッシュを再利用）した
要約を返す。

| キー | 意味 |
|---|---|
| `record_count` | store に格納した参照レコード件数 |
| `ion_modes` | `{"positive": N, "negative": N}` のような件数内訳 |
| `compound_classes` | 化合物クラス別件数の上位 10（`compound_class IS NULL` は除外） |
| `search_params` | `.dbs` 由来なら実測の許容幅、`.msp` 由来なら `null`（§14.4） |
| `source_sha256` | 元ファイルの sha256（store のキャッシュキーと同じ） |

### 14.6 `library_match_feature`

`status` は 3 通り。

| `status` | 意味 |
|---|---|
| `not_found` | `.dcl` に該当 precursor の MS/MS が無い（**未取得**であって「合わなかった」ではない。`dcl_find_msms` の `not_found` と同じ文言方針） |
| `no_candidates` | 測定 MS/MS はあるが、m/z 窓・極性・RT に該当する参照レコードが無い |
| `success` | 候補を採点して `candidates_table`（TSV、`rank`/`name`/... 列。§14.1 の 5 指標を含む）を返した |

候補は既定で `weighted_dot_product` の降順（`ranked_by` フィールドが基準を明示）。
スペクトル座標・alignment は戻り値に含めず `session.library.last_match` に持つ
（`library_plot_mirror` がそこから読む）。

### 14.7 `library_plot_mirror`

`build_mirror_payload` / `render_mirror`（`lipidmix/plots/mirror.py`）の座標契約は
`lipidmix.mirror.v1`。上段が測定（上向き）、下段が参照（下向き）、横軸 m/z 共通。
`matched_mz` は**常に参照側の m/z**（参照グリッドの窓中心）で、測定側の一致 m/z は
別フィールド `matched_measured_mz`（`ms2_tol` を渡したときだけ計算、渡さなければ空）。
`ms2_tol` を渡さない呼び出しでは、無根拠な厳密一致で色を付けないという設計判断により
測定側は一致色分けされない。

### 14.8 `verify_peak_annotation` への統合（`analytical_checks.msms.spectral_match`）

`peak_verification.msms_evidence()` の 3 状態契約（`PASS`/`FLAG_ONLY`/`ABSENT`、
`identity` トピック §12.4）は変えない。`band == "PASS"` かつ参照ライブラリが
読み込み済み（`session.library.store` あり）のときだけ、`spectral_match` ブロックが
追加で載る。ライブラリ未読み込みなら `spectral_match` キー自体を持たない。

**`status` の語彙は暫定である。** `library_match_feature` とは独立に実装されており
（`_spectral_match_for_feature`、`lipidmix/msdial/peak_verification.py`）、
実際の `verify_peak_annotation` の使われ方を見る前に固まらないよう、
現時点では次の 4 値を使う（将来 `library_match_feature` 側の語彙
`not_found`/`no_candidates`/`success` と統一する可能性がある）:

| `status` | 意味 |
|---|---|
| `unavailable` | precursor m/z が feature に無く照合できない |
| `error` | ライブラリ照会自体が例外を投げた（`store.candidates()` の失敗など） |
| `no_candidates` | 候補が 0 件 |
| `matched` | 最良候補（`weighted_dot_product` 最大）のスコアを `best_match` に載せた |

どの段階で失敗しても例外は投げない——`spectral_match` は msms_evidence 本来の目的
（`band` 判定）を止めてはいけないため、失敗は `status` の値として開示するだけに留める。

`best_match` は候補一覧を持たない（候補比較が要るときは `library_match_feature` を
直接使うこと）。`weighted_dot_product` 等は §14.1 と同じ丸め済みの平方根側の値。
