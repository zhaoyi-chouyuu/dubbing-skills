# Japanese preliminary cast overview

Use this reference only when the user requests an early production-facing estimate of the principal cast or likely voice count.

## Purpose and timing

The workbook is a Japanese pre-production handoff, not the final `登場人物設定表.xlsx`. Its purpose is to let the production side understand the approximate number of major roles, important supporting roles, minor roles, group roles, and likely voice assignments before the script workflow is finished.

Create it alongside preparation but hand it over first. Before delivery, scan the whole series at least at the level available from all SRTs, the source role workbook, `画面字.xlsx`, and targeted video or frame checks. Do not estimate the cast from episode 1 alone. The workbook may remain provisional where evidence is incomplete; visible uncertainty is preferable to a false merge or invented identity.

## Evidence rules

- Use the same source hierarchy as the internal character reference: whole-series subtitles, screen text, source workbook evidence, video continuity, embedded images, dialogue, and relationship logic.
- Treat the source role workbook as unverified unless the user explicitly declares it authoritative.
- Keep distinct story roles on separate rows. Do not merge ancient and modern roles, or visually similar actors, unless story evidence proves they are the same character.
- Record possible dual casting as `兼役候補`; it is a casting suggestion, not identity evidence.
- Split rows that hide multiple speaking roles. A group row is acceptable only when it describes a coherent group and `想定ボイス数` reflects the likely number of distinguishable voices.
- Include obvious speaking roles even when no reliable portrait is available. A blank image cell is better than an uncertain face.
- Use Japanese-visible text throughout. Add katakana readings only when supported; do not guess a reading merely to fill the field.

## Workbook contract

Filename:

```text
<project_id>_主要登場人物一覧_前期参考版.xlsx
```

Place it directly under the feature folder's `03_script/` so it can be handed to the production side independently of the internal package. Preserve any earlier user or agent workbook; create or replace this named generated file only when that exact update is intended.

On later preparation discovery runs, treat this workbook and equivalent `主要登場人物一覧` / `主役登場人物` files as generated outputs and exclude them from source role-workbook candidates. Never feed the preliminary overview back into the internal identity baseline.

Use one reader-facing sheet named `主要登場人物一覧`. The title and note must make the provisional purpose explicit, for example:

```text
<project_id> 主要登場人物一覧（前期参考版）
制作準備用の概算資料です。主要人物数、配役規模、声の方向性を早期共有するためのもので、最終確定版ではありません。
```

Show a compact summary above the table with counts for `主要役`, `重要脇役`, `主要・重要役 合計`, and `端役・群衆候補`. Use these columns in this order:

| 列 | 内容 |
|---|---|
| 役名 | Japanese display name; optional supported reading |
| キャラクター画像 | Reliable representative image for major roles when available |
| 重要度 | `主要`, `重要脇役`, `端役`, or `群衆役` |
| 役割・関係 | Concise story role and relationships |
| 性別 | Production reference; use a neutral/unknown label when unresolved |
| 参考年齢 | Approximate casting range, visibly provisional when needed |
| 人物概要 | Short production-use description |
| 声の方向性 | Broad performance and vocal direction only |
| 想定ボイス数 | `1`, `2`, `1～2`, and so on, based on distinguishable speaking roles |
| 兼役・確認メモ | Dual-cast candidates, unresolved evidence, episode checks, or separation warnings |

Prioritize clean production readability: a clear Japanese title, compact summary, dark header row, wrapped text, readable row heights, frozen headers, restrained category colors, and embedded portraits that do not obscure text. Use the spreadsheet artifact workflow and visually inspect the exported workbook before delivery.

## Relationship to other artifacts

- Internal character reference: evidence-heavy, mutable, and used for attribution. The overview may summarize it but cannot approve it.
- Preliminary cast overview: optional, Japanese, production-facing, early, and approximate.
- Final `登場人物設定表.xlsx`: downstream formal Japanese deliverable after full-series role reconciliation.

Corrections to the overview should be reflected in the internal character reference only when they represent a real evidence or identity change. Cosmetic wording, category presentation, and provisional casting notes do not alter the preparation approval state.
