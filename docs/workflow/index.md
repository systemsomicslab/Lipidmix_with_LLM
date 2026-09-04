# ワークフロー: MCP ツールの呼び出し連鎖

各 MCP ツールが、どのファイルのどの関数を、どの順に呼ぶかを記録する。
出力フィールドの**意味**は `docs/output_format/` に、ツールの**外形**（引数・用途）は
`USAGE.md` にある。ここに書くのは**内部の呼び出し順**だけ。

## 読み方

各節は 3 要素からなる。

- **前提** — 呼ぶ前に成立していなければならないセッション状態。満たされない場合に
  どの手順で `MissingState` を返すかを書く。
- **状態変更** — そのツールが `session` に書き込む内容。次のツールの前提になる。
- **呼び出し連鎖** — 番号付きリスト。`└─` の深さがコールスタックの深さに対応する。

呼び出し連鎖には**行番号を書かない**。リファクタで静かに嘘になるため。パスと関数名は
`tests/test_workflow_docs.py` が AST で実在を検証しており、実装から乖離すると
テストが落ちる。

メソッドは `ClassName.method()` と書く。特に `session` の状態は形式ごとに別クラス
（`ArfState` / `Arf2State` / `Pai2State` / `EicState`）が持っており、`AnalysisSession`
自身のメソッドは `maybe_prepend_caveat()` / `section_hint()` だけである点に注意。

## 層の構成

- `lipidmix/core/` — FastMCP インスタンス、セッション状態、パス解決、共通ヘルパ
- `lipidmix/msdial/` — MS-DIAL 固有のサイドカー（`*_tags.xml` / `.mddata`）と同定・検証
- `lipidmix/arf/` `arf2/` `pai2/` `dcl/` `eic/` — 形式ごとのパーサと MCP ツール
- `lipidmix/analysis/` — 入力形式に依存しない数値処理
- `lipidmix/plots/` — レンダラ中立の payload 組み立て
- `lipidmix/corpus/` — 蓄積ノートの純ロジック
- `lipidmix/tools/` — 形式に紐づかない MCP 公開層

## 目次

| 文書 | 対象 | ツール数 |
|---|---|---|
| [dataset.md](dataset.md) | データセット投入の入口 | 3 |
| [arf.md](arf.md) | `.arf` — サンプル別強度・PCA・差次的解析 | 10 |
| [arf2.md](arf2.md) | `.arf2` — スポット代表カタログ | 2 |
| [pai2.md](pai2.md) | `.pai2` — 単一測定のピークと MS/MS 検証 | 3 |
| [dcl.md](dcl.md) | `.dcl` — デコンボリューション済み MS/MS | 2 |
| [eic.md](eic.md) | `.EIC.aef` — クロマトグラムの検索と描画 | 6 |
| [plots.md](plots.md) | 図の PNG 保存と payload 契約の比較 | 3 |
| [mztab.md](mztab.md) | mzTab-M — DatasetState への読み込み | 2 |
| [dataset_analysis.md](dataset_analysis.md) | DatasetState — 前処理・PCA・差次的解析・エクスポート | 4 |

合計 35 ツール。対象外は 19 ツール——文献探索・レポート記録系 12
（`record_objective` `knowledge_coverage` `paper_search` `ingest_*` `write_report` など）＋
Console 実行層 7（`console_plan` `console_prepare_input` `console_method_template`
`console_run` `console_status` `console_cleanup` `job_list`）。
登録ツール総数は 54（35 + 19）。

## 一気通貫の順序はここには無い

本文書群は**ツール 1 つずつの内部呼び出し順**を記録する。生データから Console を経て
mzTab-M・差次的解析・別リポジトリのパスウェイ濃縮まで、**どの順に何を行うか**と
各関数の引数・戻り値は
[../superpowers/specs/2026-09-03-end-to-end-pipeline-design.md](../superpowers/specs/2026-09-03-end-to-end-pipeline-design.md)
にある（Console 実行層 4 ツールを含む。実装完了後に `Lipidmix/` へ昇格する予定）。
