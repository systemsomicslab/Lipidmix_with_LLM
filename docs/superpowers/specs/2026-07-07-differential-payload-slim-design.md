# 差次ツール戻り値のスリム化 設計

- 日付: 2026-07-07
- 背景: `docs/superpowers/notes/2026-07-07-interpretation-quality-findings.md` のステップ5アクション。解釈品質評価で、ローカル3モデルすべてが差次的解析を誤読した（LPS vs control 有意52件 / ILG vs control 有意58件を読めず「全て ns」または地熱幻覚）。根因は解釈力だけでなく**ツール出力の形**——`summary`（結論）が先頭にあるのに、後続の全特徴 `volcano` 配列（~700行）＋8000字切り詰めで結論が埋没した。

## 目的

`arf_differential` の2群 payload から巨大 `volcano` 全量を外し、`summary`（有意件数＋有意上位）中心の要約に整形する。これにより 8000字切り詰め後もモデルが結論（有意件数・上位有意脂質・群対比）を必ず見られるようにする。ANOVA 分岐は既に volcano 非同梱で top15 のみを返すため、2群を同じ形へ揃える。

## スコープ

- 対象: `tools_arf.py` の `arf_differential` **2群分岐のみ**（現行 line 559–582 相当）。
- 非対象: `differential.py` の純ロジック（`volcano_data`/`summarize_two_group` は不変）、ANOVA 分岐、`save_volcano_figure`。

## 設計

### データフロー（責務分離）
- **全量 volcano → session 経由でプロットへ**: `session_state.session.last_differential["volcano"]` への全量保存は維持（現行 line 578–579）。`save_volcano_figure`（`tools_reports.py`）はここから読むため無傷。
- **要約 → payload 経由でモデルへ**: payload はスリム化した要約のみ。

### 変更点（1箇所）
2群 payload を以下へ変更する:

```python
payload = {"status": "success", "kind": "two_group",
           "group_a": group_a, "group_b": group_b,
           "summary": summary, "caveats": caveats,
           "volcano_note": "全特徴の volcano 点列は本要約に非同梱。"
                           "save_volcano_figure で図示できます。"}
```

- `"volcano": volcano` を**削除**し、一行 `"volcano_note"` を追加。
- `volcano = differential.volcano_data(...)` の算出自体は残す（session 保存に必要）。
- `summary` は現行のまま（`n_tested`/`n_significant`/`n_up`/`n_down`＋有意 `top`[top_n=15]：feature/mean_a/mean_b/log2fc/t/p/q）。

### エラー処理・退化
- 有意0件のときは `summary.top` が空・`n_significant=0` となり、既存の caveat（「有意0件を群間差なしと解釈しないでください」）がそのまま効く。要約中心化でこの caveat がより目立つ利点がある。

## テスト（TDD）

`tests/test_differential_tools.py` に2群 payload 整形のテストを追加:
1. **payload 形状**: 2群実行の戻り JSON をパースし、`summary["n_significant"]` が存在し、キー `"volcano"` が**無い**、`"volcano_note"` が**ある**こと。
2. **全量は session に残る**: 実行後 `session_state.session.last_differential["volcano"]` が非空（全特徴分）で、`save_volcano_figure` が従来通り図を返すこと。
3. **回帰**: `test_differential_tools` / `test_server_registration` / 全体スイートが緑。

## 付随更新
- `docs/output_format.md` が差次 payload 形状を記載していれば、`volcano` 全量→要約＋note へ同期。

## 実装後の検証（任意）
eval harness で差次2ケース（`differential_lps`/`differential_ilg`）のみ freeze→generate→再採点し、「全ns誤読」が解消しローカル解釈が救済されるかを実測（findings の救済仮説の確認）。

## 非目標（YAGNI）
- 有意点のみの圧縮 volcano 同梱はしない（`summary.top` と情報重複、トークン増）。
- top_n の変更はしない（15 で十分）。
