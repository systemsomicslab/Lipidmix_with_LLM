# MCP 状態契約の汎用化（missing_state エンベロープ ＋ 標準 annotations）設計

- 日付: 2026-08-07
- 状態: 設計確定（ユーザ承認済み・実装計画へ）
- 対象リポジトリ: `Lipidmix_with_LLM`（ms-data-parser）と `Use-LLLM`（別リポ、汎用 MCP クライアント）
- 関連: `mcp_core.py` / `tools_*.py`（サーバ）, `core/policy.py` / `core/mcp_state_policy.py` /
  `general/agent_loop.py`（クライアント）
- 前提: HISTRY 2026-08-07「パーサ別セッション状態の分離」で状態破壊そのものは解消済み。
  本設計はその**検知と復旧**を扱う。

## 1. 背景と目的

### 1.1 守るべき前提

- **ms-data-parser** は質量分析データ解析に LLM を組み込むための**ドメイン特化 MCP サーバ**。
- **Use-LLLM** はローカル LLM と従量課金 AI（Azure）を Claude Desktop / ChatGPT と同じ使用感で
  使うための **WebUI 汎用 MCP クライアント**。その上でローカル MCP サーバを問題なく使いたい。

汎用クライアントは特定サーバの語彙を知らないまま動かなければならない。

### 1.2 現状はこの前提を2箇所で崩している

1. **`core/mcp_state_policy.py`** — `RULES` に `ms-data-parser::arf_preprocess` などツール名を
   16件ハードコードし、`requires` / `provides` / `replay_safe` を宣言している。
   `indicates_missing_state` は「先に arf_parser」等の**日本語部分文字列5個**で状態喪失を判定する。
2. **`core/policy.py`** — `BUILTIN_PROFILE_SERVERS = {"ms-data-parser"}` と4つのツール名リスト
   （READ_ONLY 29件 / LOCAL_WRITE 6件 / EXTERNAL_NETWORK 1件 / KNOWLEDGE_MUTATION 4件）で
   承認要否を決めている。

いずれもサーバ側にツールが増えるたびに両リポジトリを同時に直す必要があり、実際に破綻していた。
`policy.py` のコメント「旧名が残ると classify_tool が UNKNOWN を返し、read-only 解析が毎回
承認待ちで止まる」がその症状である。

### 1.3 現状の安全網は二重に機能していない

実メッセージ11件を `indicates_missing_state` に通すと2件を見逃す。見逃す2件
（`arf_differential` の「先に arf_preprocess を実行してください（前処理後行列が必要）。」と
`arf_pca_preprocessed` の「前処理後の行列がありません。…」）は、実セッション 52ecf212 で
実際に失敗した箇所そのものである。

さらに `_restore_mcp_state` は接続世代が変わったときしかリプレイしない。
`mcp_generations` はセットのみで解除経路が無いため、一度成功した後にプロセス内で状態が
壊れた場合はリプレイが**構造的に起動しない**。検知を直しても復旧しない。

### 1.4 目的

サーバ固有知識をクライアントから完全に除去したうえで、状態喪失の検知と復旧を成立させる。

## 2. SDK 由来の硬い制約（設計を縛る）

使用中の `mcp` 1.27/1.28（サーバは `mcp.server.fastmcp.FastMCP`）には次の制約がある。

1. **`isError=true` と `structuredContent` は両立しない。** `isError=true` を作るのは
   `lowlevel/server.py` の `_make_error_result()` だけで、テキストのみを返し
   `structuredContent` を捨てる。
2. **1つのツールが「成功時 Markdown / 失敗時 構造化」を返せない。** FastMCP は戻り値
   アノテーションから `outputSchema` を導出するため、`-> str` のツールが失敗時だけ dict を
   返すことはできない。ms-data-parser のツールは大半が成功時に Markdown を返す。

したがって機械可読なエラーは**テキスト本文に JSON を載せる**形しか採れない。
既に `arf_differential` など複数のツールが `json.dumps({"status": "error", ...})` を返しており、
その延長として無理なく全ツールへ適用できる。

## 3. 契約（両リポジトリが共有する唯一の取り決め）

### 3.1 `missing_state` エンベロープ

前提となるセッション状態が無くて実行できないとき、ツールは本文にこの JSON を返す。

```json
{"error": {"code":           "missing_state",
           "state":          "preprocessed_matrix",
           "required_tools": ["arf_preprocess"],
           "message":        "前処理後の行列がありません。先に arf_preprocess を実行してください。"}}
```

| フィールド | 意味 | クライアントの扱い |
| --- | --- | --- |
| `code` | エラー種別。今回は `missing_state` のみ | 完全一致で判定。未知の code は素通し |
| `state` | 欠けている状態の識別子 | **不透明**。解釈も比較もしない。ログと開示にのみ使う |
| `required_tools` | その状態を作るツール名（サーバ名を含まない裸の名前）の**代替候補リスト（OR）**。実際の生成元をすべて列挙する | エラーを返したサーバの名前空間で解決し、「リプレイ安全かつ本セッションで成功実績あり」の候補のうち**直近に成功したもの**を再実行する |
| `message` | LLM・人間向けの説明 | そのまま LLM へ渡す |

`state` を不透明に保つことで、クライアントが脂質omicsの語彙（`preprocessed_matrix` など）を
知っている状態を避ける。`required_tools` にサーバ名を含めないのは、同じサーバが別名で
登録されうるため（クライアント側の名前空間はクライアントが決める）。

**`required_tools` は AND チェーンではなく OR の代替候補**である。`eic_plot` は
`eic_plot_chromatograms` でも `eic_plot_compounds` でも作れる。連鎖が必要な依存
（A を作るには先に B）は、A の生成ツール自身が呼ばれたときに再び `missing_state` を返すので、
リトライ1段の範囲で自然に解決するか、解決しなければ LLM へ委ねられる。

**サーバは実際の生成元をすべて列挙する義務がある。** 取りこぼすと2通りに壊れる。
（a）実際に使われた生成元が漏れていると復旧が不可能になる。実セッション 52ecf212 では
`load_dataset` だけが呼ばれ `arf_parser` は直接呼ばれていないので、`arf_dataset` の候補に
`load_dataset` が無いと復旧できない。（b）漏れた候補のほうが新しかった場合、**ユーザーが実際に
見ていたものとは別の状態が復元される**。`pca_result` を `arf_parser` だけで復旧すると、
ユーザーが見ていた図が `arf_pca_preprocessed` 由来だったときに別の PCA が「その図」として
PNG 保存されてしまう。

**クライアントは候補の中から「直近に成功した呼び出し」を選ぶ**（リスト順ではない）。
ユーザーが最後に見ていた状態を再現するのが目的だからである。

`message` には既存の日本語文面をそのまま入れる。Claude Desktop などエンベロープを解釈しない
クライアントでも、LLM が読む内容は今と変わらない。

### 3.2 annotations の解釈（明文化）

MCP 仕様のグレーゾーンを両リポジトリの docstring に明記する。

- **`readOnlyHint=true` は「サーバの外に副作用が無い」を意味する。** ファイルとネットワークを
  変更しないこと。**サーバ自身の解析セッション状態の更新は副作用に数えない。** セッション状態の
  依存関係は §3.1 の契約で伝えるため、annotations で二重に表現しない。
  この解釈を採らないと `arf_preprocess` 等が LOCAL_WRITE に落ち、解析の1手ごとに承認を求められる。
- **リプレイ安全 = `readOnlyHint=true` または `idempotentHint=true`。**
  MCP 仕様は `idempotentHint` を「`readOnlyHint == false` のときだけ意味を持つ」と定義するため、
  OR で拾う。

## 4. サーバ側（ms-data-parser）

### 4.1 新規 leaf モジュール `mcp_errors.py`

```python
def missing_state(state: str, required_tools: list[str], message: str) -> str:
    """前提状態が無いことを、クライアント非依存の JSON エンベロープで返す。"""
```

依存は stdlib のみ。既存の leaf ルール（`tools_*` / `server` を import しない）に従う。

### 4.2 状態不足13箇所の置換

| # | ツール（該当行） | 現在の形 | `state` | `required_tools` |
| --- | --- | --- | --- | --- |
| 1 | `arf_list_tags` | テキスト | `arf_dataset` | `arf_parser`, `load_dataset` |
| 2 | `arf_list_classes` | テキスト | `arf_dataset` | `arf_parser`, `load_dataset` |
| 3 | `arf_list_sample_roles` | JSON | `arf_dataset` | `arf_parser`, `load_dataset` |
| 4 | `arf_exclude` | JSON | `arf_dataset` | `arf_parser`, `load_dataset` |
| 5 | `arf_preprocess` | JSON | `arf_dataset` | `arf_parser`, `load_dataset` |
| 6 | `arf_pca_preprocessed` | テキスト | `preprocessed_matrix` | `arf_preprocess` |
| 7 | `arf_differential` | JSON | `preprocessed_matrix` | `arf_preprocess` |
| 8 | `arf_plot_volcano` | **例外** | `differential_result` | `arf_differential` |
| 9 | `pai2_inspect_peak` | JSON | `pai2_dataset` | `pai2_parser` |
| 10 | `verify_peak_annotation` | JSON | `pai2_dataset` | `pai2_parser` |
| 11 | `save_pca_figure` | テキスト | `pca_result` | `arf_parser`, `arf_pca_preprocessed`, `load_dataset` |
| 12 | `save_volcano_figure` | テキスト | `differential_result` | `arf_differential` |
| 13 | `save_eic_figure` | テキスト | `eic_plot` | `eic_plot_chromatograms`, `eic_plot_compounds` |

候補は**コードで確認した実際の生成元**である。`arf.features` は `arf_parser`（`arf.load_data`）が
作り、`load_dataset` は内部で `arf_parser` を呼ぶ（`tools_dataset.py:79`）。`last_pca_plot` は
`arf_parser`（`tools_arf.py:549`）と `arf_pca_preprocessed`（:385）の2経路が
`_remember_arf_pca_plot` 経由で書く。`feature_matrix` は `arf_preprocess`（:312）のみ、
`last_differential` は `arf_differential`（:893）のみ、`eic.last_plot` は
`eic_plot_chromatograms` と `eic_plot_compounds`（`tools_eic.py:95, 173`）。

`message` には現在の日本語文面をそのまま入れる。

`arf_plot_volcano`（#8）だけは扱いが違う。現在 `ValueError` を送出しており `isError=true` に
なるが、このツールの戻り値は構造化ペイロード `VolcanoPlotPayload`（webUI の描画経路が使う）で、
失敗時に `str` を返すと §2 の制約2で outputSchema 導出が壊れる。**ここだけは例外を残し、
例外メッセージの本文にエンベロープを載せる。**

その結果 FastMCP が `Error executing tool arf_plot_volcano: ` を前置するため、クライアント側の
パーサは**本文全体が JSON でなくても、埋め込まれた JSON オブジェクトを取り出せる**必要がある
（§5.1 の要件に含める）。

**対象外**: `tools_samples.py:63` の「先に arf_parser で ARF を読み込んでください」は状態不足では
ない。`sample_search` は ARF ロード前でも動くことが要件で（`.mddata` や実ファイル名から
サンプル集合を組む）、この文言はサンプルを1件も特定できなかったときの**複数ある対処法の1つ**を
案内しているにすぎない。エンベロープ化すると、ARF を必要としない検索のために `arf_parser` の
自動リプレイが走ってしまう。

### 4.3 全40ツールへの `ToolAnnotations` 付与

`@mcp.tool(annotations=ToolAnnotations(...))` は使用中の SDK で対応済み。付与方針:

- 解析・検索・読み取り系（29件）: `readOnlyHint=True`
- レポート/図/objective の書き出し（6件）: `readOnlyHint=False`, `destructiveHint=False`,
  `idempotentHint=True`（同じ引数で同じパスへ上書きするため）
- `paper_search`: `openWorldHint=True`, `readOnlyHint=True`
- `ingest_promote` / `ingest_reject`: `readOnlyHint=False`, `destructiveHint=True`
  （`_inbox` からの移動・削除を伴う）
- `ingest_stage`: `readOnlyHint=False`, `destructiveHint=False`（新規ノートを書くだけ）
- `ingest_review_queue`: `readOnlyHint=True`（`build_inbox_index()` を返すだけの純粋な読み取り）

### 4.4 ドキュメント

`docs/output_format/core.md` に §「状態不足の伝え方」を追加し、エンベロープと annotations の
解釈を定義する。

## 5. クライアント側（Use-LLLM）

### 5.1 新規 `core/tool_result_contract.py`

本文から envelope を読む純関数のみを置く。**サーバ名もツール名も知らない。**

```python
@dataclass(frozen=True, slots=True)
class MissingState:
    state: str
    required_tools: tuple[str, ...]
    message: str

def read_missing_state(result_text: str) -> MissingState | None:
    """本文が missing_state エンベロープならそれを返す。違えば None。"""
```

JSON として読めない本文、`error` を持たない本文、未知の `code`、`required_tools` が空または
文字列でないものは、すべて `None`。例外は投げない（契約を実装しないサーバの通常の出力が大量に
流れてくる経路なので、ここで落ちると全ツール呼び出しが壊れる）。エンベロープを実装しない
サーバでは単に何も起きない（後方互換）。

本文全体が JSON でない場合は、**埋め込まれた最初の JSON オブジェクトを切り出して読み直す**。
§4.2 の `arf_plot_volcano` が例外経由になり、FastMCP が
`Error executing tool <name>: ` を前置するため。

### 5.2 削除するもの

- `core/mcp_state_policy.py` 全体（`RULES` / `ToolStateRule` / `build_replay_plan` /
  `indicates_missing_state` / `StateRestoreBlocked`）
- `general/agent_loop.py` の `_restore_mcp_state` / `_mark_mcp_state_live` /
  セッション state の `mcp_generations`
- `core/policy.py` の `BUILTIN_PROFILE_SERVERS` / `READ_ONLY_TOOLS` / `LOCAL_WRITE_TOOLS` /
  `EXTERNAL_NETWORK_TOOLS` / `KNOWLEDGE_MUTATION_TOOLS` / `classify_tool` / `decide_tool`
  （名前ベースの分類経路一式）

事前リプレイ（接続世代トリガ）は事後リカバリに一本化されるため消える。代償は再接続直後に
1回だけ無駄な失敗呼び出しが挟まることだが、自己修復するため許容する。
`mcp_generations` の「セットのみで解除されない」バグも削除で消滅する。

### 5.2.1 `MCPClient` の enforcement 経路（削除ではなく付け替え）

`policy.enforce_tool` は `MCPClient.call_tool` / `call_sequence` から使われており、これは
`cli.py` の単一サーバ経路が通る。名前ベース分類を消すだけでは CLI が壊れるため、
**annotations ベースの enforcement へ付け替える**。

`MCPClient.call_tool` は既に呼び出し前に `list_all_tools(session)` でツール一覧を取得している
（引数スキーマ検証のため）。`enforce_tool` の呼び出しを接続前から**ツール発見後**へ移し、
`decide_server_tool(server_name, tool_name, annotations=tools[tool_name].annotations, ...)` を
使う。`call_sequence` も同様に、一覧取得後に全 call を検証してから実行する。

`enforce_tool` はシグネチャに `annotations` を受け取る形へ変更し、`decide_server_tool` に委譲する
薄いラッパとして残す（`ToolPolicyError` を投げる責務はそのまま）。

副作用として、CLI 経路でも「annotations の無いサーバは UNKNOWN＝承認必須」が効くようになる。
これは緩和ではなく厳格化なので許容する。

### 5.3 リカバリのアルゴリズム

`_call_and_record` の直後、`agent_loop` に置く。

```
1. ツール結果本文を read_missing_state() に通す。None なら何もしない
2. envelope なら is_error=True として記録・開示する
3. required_tools を「そのエラーを返したサーバ」の名前空間で解決する
4. 候補（OR。AND チェーンではない）のうち次を両方満たす呼び出しを全部集め、
   その中で **invocation id が最大＝直近に成功したもの**を1つ選ぶ
     - リプレイ安全か: annotations の readOnlyHint or idempotentHint
     - 本セッションで成功した呼び出しが tool_invocations にあるか
5. 選べたら記録済み引数のまま再実行し、成功したら本命を1回だけリトライする
6. 1つも選べない／リプレイ自体が失敗した場合は envelope の message を LLM へ渡す
     （Claude Desktop と同じ振る舞い。LLM が自分で前提ツールを呼べる）
7. リトライは1段のみ。復旧の復旧はしない
```

**記録済み引数で再実行する点が要点。** LLM に再実行させると前処理の引数が変わって解析条件が
黙って変わりうる。科学的妥当性に関わるため、そこはコードで担保する。

リプレイした呼び出しは既存の `replay_of_id` で記録し、監査で本来の呼び出しと区別できるようにする。

### 5.4 `policy.classify_server_tool` の置き換え

annotations 単独判定にする。判定順が重要。

| 判定順 | 条件 | ToolSafety |
| --- | --- | --- |
| 1 | `openWorldHint=true` | EXTERNAL_NETWORK |
| 2 | `readOnlyHint=true` | READ_ONLY |
| 3 | `destructiveHint=true` | KNOWLEDGE_MUTATION |
| 4 | `readOnlyHint=false` かつ `destructiveHint=false` | LOCAL_WRITE |
| 5 | annotations 無し | UNKNOWN（承認必須） |

`openWorldHint` を最優先にしないと `paper_search`（外部を読むだけなので `readOnlyHint` も真）が
READ_ONLY に落ち、`network_mode` のゲートを迂回する。`policy.py` の既存コメントが警告している
罠であり、判定順をテストで固定する。

## 6. 承認挙動の差分

### 6.1 権限が変わるもの: 1件

- **`ingest_review_queue`: 承認必須 → `read_only_auto` 時に自動実行。**
  実装は `knowledge_store.build_inbox_index()` を返すだけの純粋な読み取りで
  （`tools_objective.py:236-238`）、現在 KNOWLEDGE_MUTATION に入っているのは名前リストの
  分類ミス。ユーザ承認済みの緩和。

### 6.2 監査ラベルのみ変わるもの: 1件

- `ingest_stage`: `knowledge_mutation` → `local_write`。`decide_server_tool` では
  LOCAL_WRITE も KNOWLEDGE_MUTATION も等しく承認必須なので実行可否は不変。

### 6.3 他サーバへの影響

今日は `readOnlyHint=true` 以外すべて UNKNOWN だった。新マッピングは `openWorldHint` と
`destructiveHint` を解釈する分だけ**厳しくなる方向にしか動かない**（緩む経路は無い）。

## 7. エラー処理

- **エンベロープを実装しないサーバ**: `read_missing_state` が `None` を返し、従来どおり
  結果がそのまま LLM へ渡る。何も壊れない。
- **`required_tools` が解決できない**（そのサーバに存在しないツール名）: リプレイせず
  `message` を LLM へ渡す。
- **リプレイ自体が失敗**: リプレイの失敗を開示し、本命はリトライしない。LLM に判断を委ねる。
- **リプレイ後の本命が再び missing_state**: リトライは1段のみなので、そのまま LLM へ渡す。
- **不正な JSON / 部分的なエンベロープ**: `None` 扱い。例外は出さない。

## 8. テスト戦略

承認経路まで一度に変えるため、**承認挙動の回帰テストを実装より先に書く**。

1. **承認スナップショット（先行）** — ms-data-parser の40ツールについて現行 `classify_tool` の
   分類を固定した表をテストに置き、annotations 経由の新判定がこれを再現すること、差分が
   `ingest_review_queue` の1件だけであることを明示的に検証する。
2. **判定順** — `openWorldHint` と `readOnlyHint` を両方持つツールが EXTERNAL_NETWORK になること。
3. **契約テスト** — 同一のサンプル envelope 文字列を両リポジトリのテストに置く。サーバ側は
   「生成できる」、クライアント側は「解釈できる」。片側だけ変わったら落ちる。
4. **サーバ側** — §4.2 の13箇所が envelope を返すこと、`message` が従来の日本語文面を保持すること、
   40ツール全部に annotations があること（`test_server_registration.py` に追加）。
5. **クライアント側リカバリ** — 復旧成功 / リプレイ非安全で断念 / 履歴に無くて断念 /
   リトライは1段のみ、の4経路。
6. **削除分の埋め合わせ** — `test_mcp_state_policy.py` と
   `test_general_agent.py::test_reconnect_replays_parser_before_differential` は消えるので、
   同等シナリオを新機構で書き直す。
7. **実データ E2E** — MCP 再接続を模し、`arf_preprocess` が記録済み引数のまま復元されることを
   確認する。

## 9. スコープ外

- `code` の追加（`missing_state` 以外のエラー種別）。今回は1種類のみ。
- 状態の**自動無効化**宣言（あるツールが別の状態を壊すことの表明）。パーサ別スロット分離で
  破壊自体が無くなったため不要。必要になった時点で `code` を足す。
- ms-data-parser 以外のサーバへの annotations 付与。契約は後方互換なので順次でよい。

## 10. 既知の別件

`tests/test_server_registration.py::test_tool_count_is_stable` が `39` を期待して失敗している
（実際は40）。コミット `f981cf3` が `EXPECTED_TOOLS` に `arf_plot_volcano` を足した際にリテラルを
更新し忘れたもの。本設計とは無関係だが、同ファイルへ annotations の検証を足す際に
`assert len(tools) == len(EXPECTED_TOOLS)` へ直してドリフトを構造的に防ぐ。
