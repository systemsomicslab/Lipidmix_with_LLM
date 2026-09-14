# 研究文献の定期収集 → vault 蓄積 → 設計導線 の設計

- 日付: 2026-09-14
- 状態: Draft
- 主対象リポジトリ: `C:\Users\yuu18\Research-Organizer\api\work\literature-vault-agent`（実装の大半）
- 副対象リポジトリ: `C:\Users\yuu18\Lipidmix_with_LLM`（本書と、採択済み提案のミラー 1 ファイル）
- 正準ストア: Obsidian vault `C:\Users\yuu18\Documents\KnowledgeVault`

本書は**本リポジトリの外側に実装の重心がある**設計である。本リポジトリに入るのは
本 spec と `docs/research-backlog.md` のみ。

---

## 1. 目的

本システム（MCP サーバ `ms-data-parser` とその周辺）の**作り方・立ち位置**に効く研究成果を、
人手の探索なしに継続的に発見し、要約して vault に蓄積し、**設計判断に接続する**。

「接続する」とは、蓄積が読み物として終わらず、次の 2 つの生きた成果物を更新し続けることを指す。

1. **設計候補バックログ** — 「この論文の手法を本システムのここに適用できる」という提案。人が採否を決める。
2. **ギャップ表** — 「隣接ツール／論文がやっていて本システムがやっていないこと」の一覧。

ギャップ表の未解決行が翌週の探索クエリの入力になる。これによりクエリが静的に腐らない。

## 2. 対象範囲（何を「関連性が高い」とするか）

収集する軸は 2 つに限定する。

- **手法・アーキテクチャ系** — 「このシステムをどう作るか」に効くもの。KG 推論、LLM エージェント×科学データ、
  マルチオミクス統合、MS データ処理アルゴリズム、ワークフロー再現性。
- **競合・隣接ツール** — リピドミクス／メタボロミクス／プロテオミクス解析ツール、LLM×オミクス、
  MCP・エージェント基盤の新規リリースや比較研究。

**収集しない軸**（意図的な除外）:

- **脂質生物学・薬学のドメイン知識**（脂質クラスの生理的意味、疾患との関連、薬効メカニズム）。
  これは本リポジトリの `knowledge/` が既に担っており、`playbook/gap-driven-literature-discovery.md` の
  定める通り**確定した objective の GAP を埋める目的でのみ**探索される。本パイプラインは
  objective に紐づかないため、同じ場所に混ぜてはならない。
- **データ標準・リソースの更新**（mzTab-M、LIPID MAPS、MassBank 等）。今回は対象外とする。

### 2.1 適用先の 3 層

本システムは別途**プロテオミクス・メタボロミクスへの拡張**が検討されている。メタボロミクスは
`docs/superpowers/specs/2026-09-02-single-repo-msdial-omics-design.md` で Phase 4 として
計画済みだが、**プロテオミクスはどの spec にも存在せず**、MS-DIAL Console はプロテオミクスの
エンジンではない。したがって「具体モジュールを名指しできるか」だけを関門にすると、
拡張と構成再検討に効く論文をすべて取り逃す。

適用先は 3 層で扱う。

| 層 | 意味 | 例 |
|---|---|---|
| **L1 モジュール／契約** | 現行コードの具体的な場所に効く | `lipidmix/analysis/export_contract.py` の列定義、`lipidmix/plots/render.py` のトークン最適化、`lipidmix/pipeline/engine.py` の再開設計 |
| **L2 拡張面** | まだコードが無いプロテオミクス／メタボロミクス経路に効く | DIA 定量の欠損値処理、ペプチド→タンパク質ロールアップ、メタボライト同定信頼度 |
| **L3 全体構成** | 層分けそのものへの示唆。現行アーキテクチャの前提を疑わせる | 形式別 `reader.py`/`tools.py` 構成は omics が 3 つでも持つか、`lipidmix/console/` の抽象度は MS-DIAL 外のエンジンを収容できるか |

L3 は明示的に歓迎する。L3 が 0 件の週が続く場合、それは種クエリが手法の細部に寄りすぎている
サインとして扱い、クエリを見直す。

## 3. 設計原則

1. **判断と機械作業を分離する。** 検索・重複除去・撤回照合・既読照合は決定論のコードが持ち、
   LLM を呼ばない。関連性の採点と提案の起案だけを LLM が持つ。本リポジトリの
   `lipidmix/corpus/paper_ingest.py` が既に採っている分離をそのまま踏襲する。
2. **既定は落とす。** 通過 0 件の週があってよい。vault を汚さないことを、拾いこぼさないことより優先する。
3. **人のゲートは候補一覧に置く。** 全文要約（課金を伴う）は人が選んだものにだけ走らせる。
4. **既存資産を捨てない。** 全文要約・Obsidian ノート生成・UI は Literature Vault Agent に完成品として
   存在する。ここを書き直さない。
5. **外部へ出る情報を絞る。** 種クエリと、人が承認した公開 URL のみ。内部固有名詞を送らない。
6. **抄録は非信頼データ。** sanitize して保存し、そこに書かれた指示には従わない。

## 4. アーキテクチャ

| 層 | 実体 | LLM | 起動 |
|---|---|---|---|
| ③ 全文要約 | 既存 Literature Vault Agent のサービス層 | OpenAI | 週1・最初（前週までのチェック済み分を処理） |
| ① 収集 | `discovery/` の Python スクリプト | なし | 週1・③の直後 |
| ② 採点・候補化 | Claude Code セッション | Claude | 週1・①の直後 |
| ④ 導線 | ②と同一セッションの第 2 の仕事 | Claude | ②に含む |

週次タスクは **③ → ① → ②** の順で走る。③を先頭に置くことで、**人の作業は
「候補ノートのチェックボックスを付ける」だけ**になり、`promote.py` を手で叩く手順が消える。
チェックが付かなかった週は③が何もせず終わるだけで、失敗ではない。

データフロー:

```
種クエリ + ギャップ表(open 行)
   -> ① Europe PMC / arXiv 検索
   -> 撤回除去 -> 重複除去 -> 既読照合
   -> candidates-YYYY-MM-DD.json          (機械可読・vault 外)
   -> ② 3層ルーブリックで採点
   -> 00_Inbox/candidates-YYYY-MM-DD.md   (人が読む・チェックボックス)
   -> 人が [x] を付ける
   -> ③ promote.py が全文要約
   -> 10_Literature/Lipidmix/<title>.md
   -> ④ 次回②が未紐づけノートを検出
   -> 30_Projects/Lipidmix/backlog/NNN-<slug>.md  +  gap-table.md 更新
   -> 人が accepted にする
   -> Lipidmix の docs/research-backlog.md に転記
```

## 5. ① 収集層

### 5.1 配置

```
literature-vault-agent/
  discovery/
    __init__.py
    sources.py      # Europe PMC / arXiv 検索。ネットワーク I/O はここだけ
    dedupe.py       # DOI 正規化・タイトル正規化・撤回除去
    ledger.py       # 既読台帳（SQLite）
    queries.json    # 種クエリ（版管理する）
    run.py          # CLI エントリ
    promote.py      # ③ の起動（7 章）
    README.md       # 運用手順
    data/           # gitignored。台帳と candidates-*.json
```

Research-Organizer 直下の `.git` は中身が空で実質リポジトリではないため、既存の
`literature-vault-agent` リポに同居させる。`.gitignore` に `discovery/data/` を追加する。

### 5.2 ソース

2 本に限定する。

- **Europe PMC** — 査読論文に加え `source=PPR`（bioRxiv / medRxiv）も**含める**。
  本リポジトリの `paper_ingest.py` はプレプリントを除外するが、それは解釈根拠としての
  信頼性を担保する設計であり、手法・ツールの動向追跡には不適切（手法論文は査読通過まで
  1〜2 年かかる）。プレプリントは取得し、`is_preprint` で明示する。
- **arXiv API** — `cs.LG` / `cs.AI` / `q-bio.QM`。Europe PMC が索引しない計算機科学側の
  手法論文とエージェント基盤の論文はここにしかない。

3 本目（Semantic Scholar 等）は追加しない。引用グラフが必要になった時点で再検討する。

撤回判定は `paper_ingest.py` の実装（`pubType` と `commentCorrectionList` の両方を見る）を
`dedupe.py` に移植する。Lipidmix 側からの import はしない（リポジトリ間の依存を作らない）。

### 5.3 クエリ

2 系統。

- **種クエリ** — `queries.json` に版管理する。各エントリは
  `{id, axis, omics, query, sources, active}`。`axis` は `method` / `tool`、
  `omics` は `lipid` / `metabo` / `proteo` / `cross` / `none`。
  初期セットは 4 群を張る:
  - 手法・基盤: 生物医学ナレッジグラフ推論、LLM エージェント×科学データ解析、
    ツール／プロトコル設計、ワークフロー再現性
  - プロテオミクス: DIA 定量、ペプチド-タンパク質推論、FDR 制御、DIA-NN / FragPipe / MaxQuant
  - メタボロミクス: MZmine / XCMS / GNPS、分子ネットワーキング、同定信頼度
  - マルチオミクス: 統合解析フレームワーク、共通データモデル、クロスオミクス正規化
- **動的クエリ** — ギャップ表の `open` 行から②が起案し、`queries.json` への追記を**提案**する。
  採用は人が行う（②は書き込まない）。**手法語彙のみで構成し、内部固有名詞を含めない**（9 章）。

### 5.4 除去の順序

1. 撤回（Europe PMC の `pubType` / `commentCorrectionList`）
2. DOI 正規化重複（小文字化・`https://doi.org/` 接頭辞除去）
3. arXiv ID 重複（版数 `vN` を落として比較）
4. タイトル正規化重複（英数字以外を除去し先頭 80 文字で比較。同一論文の preprint と
   査読版が両ソースから来た場合、**査読版を残す**）
5. 既読台帳照合

台帳は「一度候補として出力した ID」だけを持つ。採否の理由は持たない（それは②の責務）。
スキーマ: `seen(id TEXT PRIMARY KEY, source TEXT, first_seen_at TEXT)`。

### 5.5 出力

`discovery/data/candidates-YYYY-MM-DD.json`。

```json
{
  "generated_at": "2026-09-14T08:00:00+09:00",
  "queries_run": 18,
  "raw_hits": 214,
  "after_filters": 32,
  "items": [
    {
      "id": "doi:10.1038/s41551-025-01598-z",
      "source": "europepmc",
      "title": "...",
      "abstract": "...",
      "doi": "10.1038/s41551-025-01598-z",
      "url": "https://doi.org/10.1038/s41551-025-01598-z",
      "year": 2025,
      "venue": "Nature Biomedical Engineering",
      "is_preprint": false,
      "matched_query": "q-method-kg-01"
    }
  ]
}
```

抄録は sanitize（制御文字除去・空白圧縮・4000 文字上限）してから格納する。

**処理済みの印**: ②は採点を終えた候補ファイルを `discovery/data/processed/` へ移動する。
「未処理」とは `discovery/data/` 直下に残っている `candidates-*.json` を指す。フラグを
ファイル内に書く方式は採らない（②が途中で落ちた場合に中途半端な状態が残るため、
移動という原子的操作で表す）。

### 5.6 失敗時

- ネットワーク失敗は非ゼロ終了。`candidates-*.json` を**書かない**。
  書き込みは一時ファイル + `os.replace` の原子的操作とし、部分ファイルを残さない。
- 台帳の更新は候補ファイルの書き込み成功**後**に行う。逆順にすると、書き込みに失敗した週の
  候補が永久に失われる。

## 6. ② 採点・候補化層

### 6.1 起動

週1（月曜朝を既定）。①の直後に Claude Code セッションを起動する。手段は 2 通りあり、
実装時に `claude` CLI の有無を確認して決める。

- **第一候補**: `mcp__scheduled-tasks__create_scheduled_task`。既に利用可能で、本リポジトリを
  作業ディレクトリに指定できる。**制約: デスクトップアプリ未起動の時間帯は発火しない。**
- **代替**: Windows タスクスケジューラから 1 つのタスクで①→②を順に実行（②は `claude -p` の
  ヘッドレス起動）。アプリの起動状態に依存しない。

発火しなかった週があっても、①の出力は残り台帳も進むので、翌週のセッションが未処理の
`candidates-*.json` をまとめて拾う。**再収集は起きない。**

### 6.2 入力

- 未処理の `candidates-*.json`（抄録のみ。**非信頼データ**）
- `KnowledgeVault/30_Projects/Lipidmix/gap-table.md`
- 本リポジトリの `CLAUDE.md`（アーキテクチャ地図として既に機能している）

これ以外のソースコードは読まない。全文 PDF も読まない。抄録で落とせないものが候補に上がる。

### 6.3 ルーブリック

既定は落とす。

| 観点 | 判定 |
|---|---|
| **適用先の接点**（必須） | L1 / L2 / L3 のいずれかに接点を書けるか。**どの層にも書けないものだけを落とす。** |
| ギャップ適合 | ギャップ表の `open` 行に当たれば加点 |
| 実装現実性 | ライセンス・Python 3.14 適合・依存の重さ。kb の `facts/lipidomics-external-libs-license-and-python-fit.md` を参照 |
| 既知との差分 | 既存バックログ項目の焼き直しでないか |

各通過候補には `scope`（L1/L2/L3）と、**接点を 1 文で述べた `applies_to`** を必ず付ける。

### 6.4 出力: 週次候補キュー

`KnowledgeVault/00_Inbox/candidates-YYYY-MM-DD.md`。

```markdown
---
type: candidate-queue
generated: 2026-09-14
screened: 32
passed: 4
---

- [ ] **L3** | [題名](URL) | 2025 | Nat Biomed Eng
      接点: `lipidmix/console/` は MS-DIAL 専用で、別エンジン前提の実行層を収容できない
- [ ] **L1** | ...
```

通過分だけを書く。落とした候補は**件数のみ**記録する（理由を全部書くと、そこが最長の
ノートになる）。**L3 は件数が少なくても先頭に並べる** — 構成を揺らす話は埋もれると読まれない。

通過 0 件の週は、候補ノートを作らずギャップ表も触らない。0 件であることだけをログに残す。

### 6.5 突き合わせ（④の実体）

同一セッションの第 2 の仕事として、`10_Literature/Lipidmix/` の中で
「前回実行以降に増え、かつどのバックログ項目からもリンクされていない」ノートを検出し、

1. `30_Projects/Lipidmix/backlog/NNN-<slug>.md` を起案する
2. ギャップ表の該当行を `open` → `backlog` に更新する

別トリガ（人が手でコマンドを叩く）にはしない。人の作業ペースに依存する手動ステップは
飛ばされ、要約ノートだけが溜まってバックログが空のまま腐る。未紐づけノートの検出を
機械側の責務にしておけば、遅れても次週に回収される。

## 7. ③ 要約層の接続

### 7.1 フィールド追加

`backend/app/services/fields.py` の `DEFAULT_FIELDS` に **`Lipidmix`** を追加し、vault init を
再実行する（`POST /api/vault/init` または UI のボタン）。`10_Literature/Lipidmix/` が生成される。
フロントは API 経由でフィールド一覧を読むため変更不要。

3 omics で分けない。分けると L3（構成横断）の論文が行き場を失う。`30_Projects/Lipidmix/` と
名前が揃う利点もある。

### 7.2 promote.py

人は候補ノートの行を `- [x]` にするだけ。`discovery/promote.py` がそのノートを読み、
チェック済み行の URL を**既存サービスに in-process で渡す**。
`app.services.analyzer` → `markdown` → `vault` を、`routers/pipeline.py` の
`/analyze` + `/save` が呼ぶのと同じ順で呼ぶ。

HTTP を挟まない理由: バックエンドと frontend dev server が両方起動していることを週次処理の
前提にしたくない。UI は従来通り「思いついた URL をその場で貼る」用途に残す。

**注意**: router は保存時に SQLite の history にも書いている。`promote.py` も同じ書き込みを
通さないと、UI の履歴に週次ノートが現れず「保存されていない」と誤認される。

**二重要約の防止**: 保存に成功したら、`promote.py` はその行の末尾に生成ノートへの
`→ [[ノート名]]` を追記する。**既にリンクが付いている行は飛ばす。** これにより、
`00_Inbox` に古い候補ノートが残っていても、同じ論文を再度課金して要約することはない。
保存に失敗した行はリンクが付かないので、翌週に自動で再試行される。

**候補ノートの整理**: ②は `00_Inbox/candidates-*.md` のうち 4 週より古いものを
`99_System/archive/candidates/` へ移す。移動するだけで中身は変えない
（チェックし忘れた候補を後から掘り出せるようにするため）。

### 7.3 ノートの frontmatter 拡張

既存スキーマに 3 フィールドを足す。

- `discovery_scope: L1 | L2 | L3`
- `discovery_gap: G-003`（該当が無ければ空）
- `is_preprint: true | false`

プレプリントであることはノート本体に刻む。後で査読通過を確認する必要があるため。

## 8. 成果物スキーマ

### 8.1 ギャップ表（正準）

`KnowledgeVault/30_Projects/Lipidmix/gap-table.md`

```markdown
| id | scope | omics | 隣接ツール/論文がやっていること | 本システムの現状 | 状態 | 根拠 |
|----|-------|-------|------|------|------|------|
| G-003 | L1 | lipid | KG 上のパス推論で脂質-疾患リンクを予測 | パスウェイ解析は PubChem SPARQL のみ・被覆不足が既知 | open | [[BioPathNet]] |
| G-007 | L3 | proteo | 専用エンジン(DIA-NN 等)前提の実行層 | `lipidmix/console/` は MS-DIAL 専用・抽象化なし | open | — |
```

`状態` は `open` / `backlog` / `done` / `wontfix` の 4 値。`open` の行が翌週の動的クエリの入力。

**初期行は手で種を入れる。** 空のギャップ表から始めると 1 週目の採点が効かない。種の出所:

- メタボロミクス経路の未実装（`2026-09-02-single-repo-msdial-omics-design.md` Phase 4）
- プロテオミクスのエンジン不在（どの spec にも無い、L3）
- パスウェイ被覆不足（kb `facts/pubchem-pathway-coverage-is-too-low-for-lipids.md`）

### 8.2 バックログ項目

`KnowledgeVault/30_Projects/Lipidmix/backlog/NNN-<slug>.md`。**1 提案 = 1 ノート**。
単一ファイルに詰めると 30 件で読めなくなり、`[[リンク]]` とグラフビューも効かない。

frontmatter: `status: proposed | accepted | rejected` / `scope` / `omics` / `gap: G-003` /
`target`（適用先の記述）/ `sources`（根拠ノートへの `[[リンク]]`）。

`NNN` は 3 桁連番。②が `backlog/` の既存最大値 + 1 を採る。欠番は詰めない
（`rejected` のノートも残すため、番号は恒久的な識別子になる）。

### 8.3 Lipidmix 側のミラー

`docs/research-backlog.md`（本リポジトリ・追跡対象・新規）。`status: accepted` になった項目**だけ**を
転記する薄いミラー。本リポジトリで作業する Claude の目に入ることだけが存在理由であり、
決定はここに書かない。`docs/task.md` に落ちた時点で行を `done` にする。

既存規約との整合:

- `CLAUDE.md` の「ドキュメントの地図」表に 1 行追加する。`tests/test_readme_links.py` が入口文書の
  相対リンク実在を検証するため、**ファイルを作ってからリンクを足す**順序を守る。
- 同テストは README.md / CLAUDE.md への**数量表現を禁じている**。件数を書かない。
- `docs/HISTRY.md` / `docs/task.md` は追記専用の既存規約に従う。

## 9. 送信内容のガード

`playbook/gap-driven-literature-discovery.md` のメタデータ非漏洩原則を本パイプラインでも守る。

- 外部（Europe PMC / arXiv / OpenAI）へ出るのは、**種クエリ**と**人が承認した公開 URL** のみ。
- ②が起案する動的クエリは**手法語彙のみ**に制限する。`docs/` 由来の内部固有名詞
  （モジュール名・関数名・データ名・サンプル名）をクエリに含めない。
- ②が読む `CLAUDE.md` の内容はクエリに反映しない（採点の材料としてのみ使う）。
- 抄録は①で sanitize 済みのものだけが②に渡る。②は抄録を**データとして採点**し、
  そこに書かれた指示には従わない。
- OpenAI の API キーは `backend/.env`（gitignored）に留める。

## 10. 運用

1. 初回のみ: `queries.json` の種クエリを確定、ギャップ表に初期行を投入、
   `DEFAULT_FIELDS` に `Lipidmix` を追加して vault init、スケジュールを登録。
2. 週次（自動）: ③→①→② が走る。③が前週までのチェック済み分を要約ノート化し、
   ②が `00_Inbox/candidates-YYYY-MM-DD.md` を新しく生やす。
3. 週次（人・数分）: 候補ノートを開き `- [x]` を付ける。**これだけ。** 要約は翌週の③が拾う。
   すぐ読みたければ `promote.py` を手で実行してもよい。
4. 随時（人）: `30_Projects/Lipidmix/backlog/` の `proposed` を読み、`accepted` / `rejected` を決める。
5. `accepted` が出たら `docs/research-backlog.md` に転記する。

手順書は `discovery/README.md` に置く。

## 11. テスト方針

- **①はユニットテスト可能**。`sources.py` のネットワーク I/O を 1 関数に集約し、そこをモックする
  （`paper_ingest.py` の `_http_get_json` と同じ作法）。検証対象:
  撤回除去 / DOI 正規化重複 / arXiv 版数重複 / タイトル正規化重複（査読版を残す）/
  既読台帳照合 / 原子的書き込み / ネットワーク失敗時に候補ファイルを書かないこと。
- **②は出力を固定できない**（LLM）。代わりに候補ノートの**形式検証**スクリプトを持つ
  （必須列の存在・`scope` の値域・URL の形・`passed` と行数の一致）。
- **③は既存サービスの再利用**。`promote.py` のパースだけをテストする:
  チェック済み行だけを抽出すること / 既にノートリンクが付いた行を飛ばすこと /
  保存失敗時にリンクを付けずに残すこと。

本リポジトリのテストスイートは変更しない（`docs/research-backlog.md` の追加は
`tests/test_readme_links.py` のリンク検証に乗るだけ）。

## 12. 実装フェーズ

1. **Phase 1 — ①収集層**: `discovery/` 一式 + ユニットテスト。手動実行で候補 JSON が出るところまで。
2. **Phase 2 — 成果物の器**: vault に `Lipidmix` フィールド追加、ギャップ表の初期行、
   `30_Projects/Lipidmix/` の骨組み、本リポジトリの `docs/research-backlog.md`。
3. **Phase 3 — ②採点**: Claude セッションのプロンプト（スキル化）と候補ノート形式検証。
   1 週分を手動起動で通す。
4. **Phase 4 — ③promote**: `promote.py` と history 書き込みの整合。
5. **Phase 5 — スケジュール登録**: 起動手段を確定して登録。2 週間の試験運用後にクエリを見直す。

## 13. 決定事項と却下した案

| 決定 | 理由 |
|---|---|
| ハイブリッド（決定論の収集 + Claude の採点 + 既存の要約） | 下記 2 案の欠点を回避 |
| 却下: Research-Organizer に探索層を増築し全部そこで完結 | 本システムとの親和性を判断できず、クエリが静的になり半年で腐る |
| 却下: Claude が収集から全部やる | 決定論でよい処理まで LLM が走る。既存の要約資産を使わない |
| プレプリントを含める | 手法論文は査読通過まで 1〜2 年。除外すると動向追跡にならない |
| ソースは 2 本 | 3 本目は引用グラフが必要になってから |
| 人のゲートは候補一覧 | 全文要約の課金と vault のノイズを同時に抑える |
| vault が正準・Lipidmix はミラー | Obsidian のリンクとグラフが効く場所を正準にする |
| 落とすのは「3 層どれにも接点を書けないもの」のみ | プロテオミクス・メタボロミクス拡張と構成再検討を取り逃さない |

## 14. 未決事項（実装時に確定する）

- ②の起動手段（`scheduled-tasks` MCP か、タスクスケジューラ + `claude -p` か）。
  `claude` CLI の有無を確認して決める。
- `promote.py` が history へ書く際の、既存 router との実装共有の粒度
  （サービス関数を切り出すか、router を薄くして再利用するか）。
