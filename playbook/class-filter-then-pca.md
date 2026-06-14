---
type: playbook
when_to_use: クラス別に2群間のプロファイル差を見たいとき(ARF/サンプル別データ)
tools: [arf2_parser, arf_list_classes, arf_parser, arf_re_pca]
precondition:
  polarity: any
  group_structure: 2-group comparison
  objective_class: class-profile comparison
last_reviewed: 2026-06-13
---

## 手順（ツール間の接続のみ。各ツールの詳細は tool docstring が真実の源）
1. `arf2_parser` でデータセット全体像（スポット数・クラス構成・極性）を把握する。
2. `arf_list_classes` で利用可能な脂質クラス（Class ID）を列挙する。
3. 関心クラスに絞って `arf_parser` を実行し、サンプル別の値を取得する。
4. `arf_re_pca` で群構造が主成分に現れるかを見る。クラス絞り込みの前後で
   PCA の分離が変わるかを比較する。

## 分岐条件
- ステップ4で群分離が弱い → クラスをさらに絞るか、props（height など）を変える。
- 群分離が強い → 寄与の大きいクラス/スポットを次の解釈対象にする。

## 解釈の注意
エーテル脂質クラス（PE P-/PC P-）の群間増減を見るときは、酸化ストレス仮説
[[plasmalogen-oxidation]] と、表記の落とし穴 [[pe-p-vs-pe-o-annotation]] を必ず
併せて参照する。文献の示唆とデータが食い違う場合はデータを優先し、不一致を報告する。
