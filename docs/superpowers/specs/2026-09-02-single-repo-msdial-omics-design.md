# 単一リポジトリ統合設計：MS-DIAL 実行 → MCP → GUI

- 日付: 2026-09-02（2026-09-02 確定事項を反映）
- 前版: `docs/superpowers/specs/2026-08-29-msdial-mztab-lipidmix-integration-design.md`
- 状態: Draft
- 対象リポジトリ: `C:\Users\yuu18\Lipidmix_with_LLM`（単一リポジトリ）
- 参照先（外部・変更なし）:
  - `C:\Users\yuu18\repository_catalog`（read-only SQLite、将来の study context）
  - `massbank-context`（将来のスペクトル照合 MCP）
- 主交換形式: mzTab-M 2.0
- 内部永続形式: `analysis-job.json`（旧 omics-handoff.v2 を改称・再定義）

---

## 1. 前版からの変更点の要約

| 項目 | 前版 | 本版 |
|---|---|---|
| リポジトリ構成 | 上流（msdial-interactive-app）＋下流（本リポ） | 単一リポジトリ |
| omics-handoff | 上流→下流のクロスプロセス契約 | 同一プロセス内の永続形式（analysis-job.json）|
| MS-DIAL 実行層 | 別リポジトリの MCP アダプタを経由 | 本リポ `lipidmix/console/` で直接管理 |
| 対象 omics | リピドミクスのみ | リピドミクス＋メタボロミクス（Phase 4 以降） |
| GUI | 別リポジトリ依存 | 本リポ `ui/` に追加（最終フェーズ）。**LLM 不要の手動操作ツールとして設計** |
| 開発順序 | 非明示 | Console backend → MCP → GUI |
| ログ・job ファイルの置き場 | 本リポ `analyses/` 配下（追跡外） | **データフォルダ内のラン ディレクトリ**（リポジトリに解析履歴を溜めない） |
| MSDIAL_EXE 管理 | 未定 | **環境変数 `MSDIAL_EXE`（確定）** |
| console_run 承認 | 未定 | **MCP クライアント（Claude Desktop）の権限設定に委ねる（確定）** |

前版で確認した技術的事実（Height/Area metadata 不整合、gap-fill 非識別、InChIKey 導出手順）は
すべて引き継ぐ。本版はアーキテクチャ上の再設計のみ行い、前版 §3–§10・§22–§23 の内容を
上書きしない。

---

## 2. 目的

生データのディレクトリパスを渡すだけで、次の処理を安全・再現可能・ローカルファーストで
実行できる単一ツールチェーンを **1 つのリポジトリ**に収める。

1. MS-DIAL Console を Python から実行し、ピーク検出・アライン・同定・エクスポートを行う。
2. 出力の構造・定量値・ジョブ所有権を検証し、`analysis-job.json` に永続化する。
3. MCP サーバ `ms-data-parser` が永続状態を読み込み、前処理・QC・PCA・差次的解析・
   根拠確認・レポート生成を行う。
4. Web GUI がジョブ進捗・結果・図を表示し、ユーザ入力（sample manifest 承認等）を受ける。
5. 将来、リピドミクスに加えてメタボロミクスの解析経路を追加する。

---

## 3. 設計原則

前版の原則を継承し、単一リポジトリ化に伴う変更のみ反映する。

1. **データ面と制御面を分離する。** mzTab-M は定量表・同定表（データ面）、
   `analysis-job.json` はジョブ状態・ファイル選択・検証結果・来歴（制御面）とする。
   両者の役割は変わらない。ただし後者は「別プロセスへ渡す契約書」ではなく
   「ジョブの永続スナップショット」として位置づける。
0. **解析ログはリポジトリに残さない。** `analysis-job.json`・`msdial.log`・`feature-qc.tsv`・
   `sample-manifest.tsv` はすべて**データフォルダ内のランディレクトリ**に置く。
   本リポジトリは常にクリーンな状態を保ち、`analyses/` を解析の蓄積場所として使わない。
2. **LLM にファイルの意味を推測させない。** 極性・定量種別・サンプル因子・主ファイルの
   選択は `analysis-job.json` で確定する。
3. **ジョブ所有権を優先する。** 実行前後の差分でジョブ生成物を特定し、更新時刻での
   自動選択はしない。
4. **検証不能は停止する。** 定量値の実体とメタデータが衝突した状態で統計解析を続けない。
5. **責務は層で分離する。** Console 実行・解析 MCP・GUI を 1 モジュールに混在させない。
   ただし1プロセスで起動してよい（MCP が Console も呼べる）。
6. **ローカルファーストを維持する。** 生データ・解析結果・ジョブ状態はローカルに置く。
7. **既存 ARF 系機能を一度に廃止しない。** mzTab-M 経路を追加した後も、ARF/ARF2/PAI2/DCL/EIC
   経路を互換・根拠確認用として残す。
8. **omics 非依存な共通層を意識する。** リピドミクス固有処理（脂質名解析、クラス集計等）は
   `lipidmix/` に留め、メタボロミクスが追加されたときに共有できる汎用処理を分離する。

---

## 4. リポジトリ構成

```
<repo_root>/
├── server.py                    # MCP ファサード（変更なし）
├── check.py                     # スクラッチ
├── ui/                          # 新規: Web GUI（Phase 5）
│   ├── app.py                   # FastAPI HTTP サーバ（MCP + GUI を兼ねてもよい）
│   └── frontend/                # SPA（React/Vue）
├── lipidmix/
│   ├── core/                    # 変更なし（leaf）
│   ├── console/                 # 新規: MS-DIAL Console 実行層（Phase 1）
│   │   ├── runner.py            # MS-DIAL.Console.exe 起動・stdout/stderr 捕捉
│   │   ├── job_manager.py       # ジョブ状態 CRUD（analysis-job.json の書き込み元）
│   │   └── output_collector.py  # 実行前後の差分でジョブ生成物を収集
│   ├── handoff/                 # 新規: analysis-job.json の schema・読み書き（Phase 1）
│   │   ├── schema.py            # dataclass / TypedDict
│   │   └── validator.py         # JSON Schema + ビジネスルール検証
│   ├── mztab/                   # 既存: Phase 1 実装済み
│   ├── analysis/                # 既存
│   ├── arf/                     # 既存
│   ├── arf2/                    # 既存
│   ├── pai2/                    # 既存
│   ├── dcl/                     # 既存
│   ├── eic/                     # 既存
│   ├── msdial/                  # 既存
│   ├── plots/                   # 既存
│   ├── corpus/                  # 既存
│   └── tools/                   # 既存 + console MCP tools を追加
│       ├── dataset.py           # 既存
│       ├── mztab_tools.py       # 既存
│       └── console_tools.py     # 新規: MCP ツールとして console/ を公開（Phase 2）
└── tests/
    ├── test_console_runner.py   # 新規
    ├── test_handoff_schema.py   # 新規
    └── ...（既存）
```

`ui/` は最終フェーズまで存在しない。`lipidmix/console/` と `lipidmix/handoff/` が本版の
主要追加であり、既存モジュールへの侵襲は最小限とする。

---

## 5. 全体アーキテクチャ

```text
生データディレクトリパス
        |
        v
LLM / CLI / GUI
        |
        +---- [Layer 1] lipidmix/console/
        |       run_plan → execute(MS-DIAL.Console.exe) → collect → validate
        |       analysis-job.json へ永続化
        |
        +---- [Layer 2] MCP server: ms-data-parser
        |       dataset_load(job_path) → DatasetState
        |       preprocess / QC / PCA / differential / evidence / report
        |
        +---- [Layer 3] ui/  (Phase 5)
        |       job 一覧・進捗・図・ユーザ入力 (sample manifest 承認等)
        |
        +---- 将来の外部 MCP: repository-context / massbank-context
```

Layer 1 と Layer 2 は単一プロセスで起動できる。MCP の `console_tools.py` が Layer 1 を
呼び出す形にすれば、LLM はすべてを MCP tool 経由で操作できる。GUI（Layer 3）は
Layer 2 の MCP または Layer 1 の API を HTTP で呼ぶ。

---

## 6. `analysis-job.json`（旧 omics-handoff.v2 の再定義）

### 6.1 位置づけの変更

前版の `omics-handoff.v2` はプロセス間の契約書だった。本版では：

- **書き込み元**: `lipidmix/console/job_manager.py`（Layer 1）
- **読み込み元**: `lipidmix/tools/dataset.py`（Layer 2）、`ui/app.py`（Layer 3）
- **役割**: ジョブの永続スナップショット。MCP サーバ再起動後の再開、GUI での表示、
  hash 検証による改ざん検知に使う。

クロスプロセス境界がなくなった分、schema を簡略化できる箇所がある（下記）。

### 6.0 ファイルの置き場所（確定）

`analysis-job.json` および全サイドカーは、**本リポジトリの外**・データフォルダ内に置く。

```text
D:/lab/raw/study-001/               ← データフォルダ（ユーザが指定するルート）
  runs/
    20260902_neg_height/            ← job_manager が生成するランディレクトリ
      analysis-job.json
      msdial.log
      mztab/
        neg-height.mzTab
      sidecars/
        feature-qc.tsv
        sample-manifest.tsv
      msdial/
        AlignmentResult_*.arf
        ...
```

本リポジトリの `analyses/` はコードのスクラッチとして残すが、ランタイムの解析状態を
ここに書き込まない。`job_manager.py` がランディレクトリのルートをデータフォルダ内で
決定し、リポジトリパスへの書き込みを行わない。

### 6.2 スキーマ例

```json
{
  "schema": "analysis-job.v1",
  "job_id": "job_20260902_001",
  "status": "completed",
  "created_at": "2026-09-02T10:00:00+09:00",
  "updated_at": "2026-09-02T10:45:00+09:00",
  "source": {
    "dataset_root": "D:/lab/raw/study-001",
    "input_count": 56
  },
  "software": {
    "name": "MS-DIAL",
    "version": "5.5.260820",
    "execution_mode": "console"
  },
  "project": {
    "omics": "lipidomics",
    "polarity": "negative"
  },
  "primary_mztab_files": [
    {
      "path": "mztab/neg-height.mzTab",
      "polarity": "negative",
      "measure": "peak_height",
      "sha256": "<sha256>",
      "validation": {
        "structure": "passed_with_warnings",
        "quantification": "verified",
        "warnings": ["SME_TRAILING_EMPTY_COLUMN"]
      }
    }
  ],
  "artifacts": [
    {
      "path": "sidecars/feature-qc.tsv",
      "role": "feature_qc",
      "format": "tsv",
      "sha256": "<sha256>"
    },
    {
      "path": "msdial/AlignmentResult_001.arf",
      "role": "peak_matrix_source",
      "format": "arf",
      "sha256": "<sha256>"
    }
  ],
  "sample_manifest": {
    "path": "sidecars/sample-manifest.tsv",
    "sha256": "<sha256>",
    "status": "approved"
  },
  "warnings": []
}
```

前版から削除したフィールド: `analysis_id`（`job_id` に一本化）、`source.commit`
（CLI でのコミット追跡は不要）。

### 6.3 必須フィールドと不変条件

前版 §7.2 の契約をそのまま継承する。変更点のみ記す。

| フィールド | 前版からの変更 |
|---|---|
| `schema` | `analysis-job.v1`（旧 `omics-handoff.v2`）|
| `job_id` | 上流・下流を通じた不変 ID（`analysis_id` を廃止して統合）|
| `project.omics` | `lipidomics` / `metabolomics`（メタボ拡張に備える）|
| `status` | 前版と同じ 5 状態 |

---

## 7. Layer 1: Console 実行層の設計

### 7.1 `runner.py`

MS-DIAL.Console.exe を `subprocess` で起動し、stdout/stderr をリアルタイム捕捉する。

```python
# lipidmix/console/runner.py（骨格）
import os, subprocess
from pathlib import Path

def run_msdial(param_file: Path, run_dir: Path, timeout_s: int = 3600) -> int:
    """MS-DIAL Console を実行し、戻り値コードを返す。
    exe_path は環境変数 MSDIAL_EXE から取得する。未設定なら MSDIAL_EXE_NOT_FOUND を上げる。
    ログは run_dir/msdial.log に書く（リポジトリ外）。
    """
    exe = os.environ.get("MSDIAL_EXE")
    if not exe:
        raise EnvironmentError("MSDIAL_EXE not set")
    log_path = run_dir / "msdial.log"
    with log_path.open("w") as log:
        result = subprocess.run(
            [exe, str(param_file)],
            stdout=log, stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
    return result.returncode
```

- `exe_path` は**環境変数 `MSDIAL_EXE` のみ**から取得する（確定）。設定ファイルは使わない。
- stdout/stderr は `run_dir/msdial.log`（データフォルダ内）に書く。リポジトリには書かない。
- タイムアウト・終了コード非ゼロはエラーコードとして Layer 2 に伝える。

### 7.2 `output_collector.py`

実行前にディレクトリスナップショットを取り、実行後に差分で今回ジョブの生成物を特定する。
更新時刻ではなく、「実行前に存在しなかった or サイズ変化したファイル」を収集する。

役割 (`role`) の割り当て基準:

| 拡張子パターン | role |
|---|---|
| `*.mzTab` | `primary_mztab` |
| `AlignmentResult_*.arf` | `peak_matrix_source` |
| `AlignmentResult_*.arf2` | `spot_catalog` |
| `*.pai2` | `sample_peaks` |
| `*.dcl` | `msms_evidence` |
| `*.EIC.aef` | `chromatogram` |

### 7.3 エラーコード（Layer 1 固有追加分）

前版 §15 のコード表に以下を追加する。

| code | 意味 | 既定動作 |
|---|---|---|
| `MSDIAL_EXE_NOT_FOUND` | 実行ファイルが見つからない | failed |
| `MSDIAL_TIMEOUT` | 規定時間内に完了しない | failed |
| `MSDIAL_NONZERO_EXIT` | 終了コード非ゼロ | failed |
| `NO_JOB_OUTPUT` | 実行後に新規生成物が見つからない | failed |
| `UNSUPPORTED_AREA_CONSOLE` | Console で Area を要求（前版から継承） | 実行前 failed |

---

## 8. Layer 2: MCP 公開面

前版 §12 を継承し、`dataset_load` の入力を拡張する。

| tool | 変更点 |
|---|---|
| `dataset_load` | `job_path`（`analysis-job.json` へのパス）を受け付けるようにする。既存の `directory` 引数も維持し、handoff なし ARF フォールバックを残す |
| `console_plan` | 新規。MS-DIAL 実行計画（パラメータファイル生成）を返す |
| `console_run` | 新規。計画を実行し、job を `analysis-job.json` に永続化する |
| `console_status` | 新規。実行中・完了・失敗の状態を返す |
| `job_list` | 新規。ジョブ一覧をローカルに返す |

`console_tools.py` は `lipidmix/tools/` に置き、`server.py` の `__all__` で公開する。
CLAUDE.md の「ツールを足すときは実体を該当モジュールに書き `__all__` に載せる」規約に従う。

---

## 9. Layer 3: Web GUI（Phase 5）

### 9.1 目的と位置づけ（確定）

Web GUI は **LLM を使わずにユーザが単独で操作できる手動ツール**として設計する。
MCP 経由で LLM と連携する使い方も可能だが、GUI 単体で全工程を完結できることを必須とする。

GUI が提供する操作:
- データフォルダの指定とジョブの新規作成
- MS-DIAL パラメータの選択・確認
- 解析実行の開始・中止
- ジョブ一覧・進捗・ログのリアルタイム表示
- sample manifest の入力・承認（`needs_input` 解消）
- PCA・volcano・EIC 図のインタラクティブ閲覧
- 差次的解析結果テーブルのフィルタ・ソート・エクスポート

### 9.2 技術方針（Phase 5 開始前に確認）

- バックエンド: `ui/app.py`（FastAPI）。Layer 1・Layer 2 の Python コードを直接 import する。
  MCP として公開している関数を HTTP エンドポイントとして再公開する形にすることで、
  MCP client（LLM）と Web クライアント（ブラウザ）の両方から呼べる。
- フロントエンド: `ui/frontend/`。技術スタックは Phase 5 開始前に確認する（推奨: React + Vite）。
- ログ・進捗の push: WebSocket でランディレクトリ内の `msdial.log` を tail してブラウザに流す。
- GUI はランディレクトリを「ログ置き場」として直接参照する。リポジトリに状態を書かない。

### 9.3 GUI 化しない機能

- MS-DIAL の詳細パラメータ設定（XML/TOML を直接編集。GUI はテンプレート選択のみ）
- MassBank 照合（将来の massbank-context MCP が担当）

---

## 10. メタボロミクス拡張

### 10.1 設計方針

本版では「メタボロミクスに拡張しやすい構造を作る」ことを目標とし、
メタボロミクス固有の実装は Phase 4 以降とする。

拡張時に影響を受ける箇所:

| 場所 | リピドミクス固有処理 | メタボへの対応方針 |
|---|---|---|
| `lipidmix/` トップ名前空間 | 脂質名解析・クラス集計 | `lipidmix/` は維持。汎用処理を `omics_core/` 等へ分離するかは Phase 4 で判断 |
| `DatasetState.project.omics` | `"lipidomics"` 固定 | `"metabolomics"` を追加するだけで読み込みは動く |
| `feature_metadata` | 脂質名・FA 組成など | mzTab-M 由来フィールドは既に汎用。MS-DIAL 固有の脂質分類は optional フィールドとして分離済み |
| PCA・differential | 入力非依存 | 変更なし |
| `identity_join.py` | InChIKey ベースで汎用 | 変更なし |
| MS-DIAL パラメータ | LC-MS lipidomics 設定 | Console runner がパラメータテンプレートを omics 別に持てばよい |

### 10.2 リポジトリ名について

`Lipidmix_with_LLM` という名称はメタボロミクス拡張後に誤解を生む。改名するかどうかはユーザの
判断とする（本 spec では決定しない）。MCP サーバ名 `ms-data-parser` は omics 非依存な名称であり、
このままでよい。

---

## 11. `DatasetState` モデル

前版 §11 をそのまま継承する。`project` フィールドに `omics` を追加する点のみ変更。

```python
# DatasetState への追加（省略以外は前版と同じ）
@dataclass
class ProjectMeta:
    omics: str           # "lipidomics" | "metabolomics"
    polarity: str        # "positive" | "negative"
    measure: str         # "peak_height" | "peak_area_above_zero"
```

`session.dataset: DatasetState | None = None` スロットは Phase 1 実装済み。

---

## 12. 実装フェーズ

各フェーズは独立して検証・運用可能にする。

### Phase 1: mzTab-M 読み込みと意味検証（**実装済み**）

前版 §18 Phase 1 の全項目が完了している（git log で確認済み）。

### Phase 2: Console 実行層

- `lipidmix/console/runner.py`・`job_manager.py`・`output_collector.py` を追加する。
- `analysis-job.json` の schema（`lipidmix/handoff/`）を実装する。
- `console_tools.py` に MCP ツールを追加し、`server.py` に登録する。
- Console で Area 要求を実行前に拒否する（`UNSUPPORTED_AREA_CONSOLE`）。
- `feature-qc.tsv` と `sample-manifest.tsv` の生成（ARF から生成可）。
- テスト戦略: MS-DIAL.Console.exe が無い環境でも動くよう、実行層を mock 可能にする。

### Phase 3: 下流解析の `DatasetState` 統合

- `dataset_load(job_path)` が `analysis-job.json` を読み `DatasetState` を作る。
- `arf_preprocess`・`arf_pca_preprocessed`・`arf_differential` を `DatasetState` 経由に
  移行し、ARF tool は互換 wrapper にする。
- DCL/EIC の evidence index を `DatasetState` に接続する。

### Phase 4: メタボロミクス対応

- `project.omics = "metabolomics"` での動作検証。
- MS-DIAL パラメータテンプレートをメタボ用に追加（Console runner の拡張）。
- 脂質固有処理（クラス集計・FA 組成）を optional モジュールとして分離。
- 新規テスト fixture（メタボロミクスの実ファイルまたは最小化サンプル）。

### Phase 5: Web GUI

- `ui/app.py`（FastAPI）と `ui/frontend/`（SPA）を追加する。
- ジョブ一覧・進捗・ログ・図・sample manifest 承認 UI を実装する。
- **技術選定をここで確認する**（Phase 4 完了後）。

---

## 13. テスト戦略

前版 §20 を継承し、単一リポジトリ化に伴う追加項目を記す。

1. **console layer mock**: MS-DIAL.Console.exe が存在しない CI でも動作するよう、
   `runner.py` は `exe_path` を DI 可能にし、テストでは fixture から既製出力を返す
   fake を注入する。
2. **handoff round-trip**: `analysis-job.json` を書いて読んで内容が一致することを検証する。
3. **collector snapshot**: `output_collector` が差分でジョブ生成物を正しく特定することを、
   tmp ディレクトリを使ったテストで確認する。
4. 前版の 1–7 項（unit / contract / conformance / golden data / integration / restart /
   regression）は変更なし。

---

## 14. セキュリティとデータ境界

前版 §17 をそのまま継承する。変更点のみ:

- Console 実行時の `exe_path` は `MSDIAL_EXE` 環境変数から取得し、ユーザ入力を
  直接 `subprocess` に渡さない。`param_file` はランディレクトリ内のファイルに限定し、
  path traversal を検査する。
- MCP ツール `console_run` の承認は **MCP クライアント（Claude Desktop 等）の
  権限設定に委ねる**（確定）。ツール自体に `needs_confirm` ゲートを設けない。
  GUI から呼ぶ場合は UI の確認ダイアログがその役割を担う。
- ランディレクトリはデータフォルダ内にのみ作成する。リポジトリディレクトリへの
  書き込みを行わない（`run_dir` がリポジトリパス配下でないことを起動時に検証する）。

---

## 15. 非目標

前版 §21 を継承し、追加する。

- GUI を Phase 5 より前に実装すること。
- メタボロミクス対応を Phase 4 より前に実装すること。
- MCP server と GUI サーバを強制的に別プロセスにすること。
- リポジトリ名を本版で決定すること。
- `ui/` の技術スタックを Phase 5 前に決定すること。

---

## 16. 未決事項（ユーザ確認待ち）

| # | 内容 | 状態 | タイミング |
|---|---|---|---|
| 1 | MS-DIAL.Console.exe のパス管理方法 | **確定: 環境変数 `MSDIAL_EXE`** | — |
| 2 | `console_run` MCP ツールの承認方式 | **確定: MCP クライアントの権限設定に委ねる** | — |
| 3 | `ui/frontend/` の技術スタック | 未定（Phase 5 開始前に確認） | Phase 5 開始前 |
| 4 | リポジトリ名の変更有無 | **確定: `Omics_TalkAnalyzer` に改名** | 任意のタイミング |
| 5 | メタボロミクスで想定する omics 種別と MS-DIAL プロジェクトタイプ | 未定 | Phase 4 開始前 |

---

## 17. 開発優先順位（現時点）

1. Phase 2（Console 実行層）が次の開発対象。
2. Phase 3（解析統合）は Phase 2 完了を前提とするが、`dataset_load(job_path)` の
   インターフェース設計は Phase 2 と並行して決定する。
3. Phase 1 で積み残した項目を確認する:
   - `dataset_load` で mzTab-M を直接指定したとき（handoff なし）の
     複数候補選択ロジックが未定義。Phase 2 の `analysis-job.json` 実装で解決する。
   - `feature-qc.tsv` 生成は ARF サイドカーが存在しない場合の挙動が未定義。
     Phase 2 で output_collector が ARF を必ず収集することで解決する。
