---
type: reference
description: 脂質クラス略号→文献検索語（同義語/正式名）のマップ。クエリ起案とカバレッジ突合に使う
tags: [vocabulary, nomenclature, search]
last_reviewed: 2026-06-14
---

脂質クラスの MS-DIAL 略号を、文献検索で当たりやすい同義語・正式名へ対応づける表。
`knowledge_store.load_vocab` がこの本文を読み、既定語彙に上書き/追記する（`type: reference`
なのでカバレッジ突合の対象からは除外される）。

書式: 1行 `略号: 同義語1, 同義語2`（`→` 区切りも可。先頭の `-` や `` ` `` は無視される）。
未マッピングのクラスが解析で出たら、ユーザーに同義語を確認してこの表へ追記する。

- PE P-: plasmalogen, ethanolamine plasmalogen, ether phospholipid
- PC P-: plasmalogen, choline plasmalogen, ether phospholipid
- PE O-: plasmanyl, ether phospholipid
- PC O-: plasmanyl, ether phospholipid
- TG: triacylglycerol, triglyceride
- DG: diacylglycerol
- PC: phosphatidylcholine
- PE: phosphatidylethanolamine
- PS: phosphatidylserine
- PI: phosphatidylinositol
- PG: phosphatidylglycerol
- FA: fatty acid
- Cer: ceramide
- SM: sphingomyelin
- LPC: lysophosphatidylcholine
- ChE: cholesteryl ester, cholesterol ester
