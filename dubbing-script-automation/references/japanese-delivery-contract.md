# Japanese Production Delivery Contract

Read this reference before creating or validating a formal delivery.

## Delivery boundary

Deliver exactly three independent `.xlsx` files under `03_script/03_final_delivery/`. Keep review TSVs, JSON, QC, evidence, checkpoints, internal character references, and optional Word renders under `03_script/02_script_work/`.

When explicitly requested during preparation, `<project_id>_主要登場人物一覧_前期参考版.xlsx` may remain directly under `03_script/`. It is an earlier Japanese production-facing estimate, not a finalized character-settings workbook, not an identity source, and not part of the formal delivery directory.

```text
03_final_delivery/
├─ <作品名>_総合脚本.xlsx
├─ <作品名>_香盤表.xlsx
└─ <作品名>_登場人物設定表.xlsx
```

Do not add a fourth workbook inside `03_final_delivery` and do not combine the three formal workbooks into one multi-sheet file unless the user explicitly changes the project contract. Never copy the preliminary cast overview into `03_final_delivery`.

## 1. 総合脚本

- Worksheet name: `総合脚本`
- Columns: `名前` | `タイムコード` | `台詞`
- Separate episodes with `第0001話`, `第0002話`, and so on.
- Format timecodes as `00:00:00,000`.
- Show the role name on a speaker transition; consecutive lines by the same speaker may leave `名前` blank when that is the established script format.
- Preserve every source subtitle row, order, line break, dialogue string, and timecode unless the user separately authorizes text correction.

## 2. 香盤表

- Worksheet name: `香盤表`
- First column: `役名`
- Episode columns: `第0001話`, `第0002話`, and so on.
- Mark an appearance with `○` when the role has at least one spoken line in that episode.
- Final column: `総出演話数`.
- Include every role used in the final script, including stable Japanese generic roles when they speak.
- Derive the matrix from the same final role data as the script. Do not maintain it manually as a separate source of truth.

## 3. 登場人物設定表

- Worksheet name: `登場人物設定`
- Columns, in order: `名前` | `キャラクター画像` | `役割/身分` | `性別` | `年齢` | `キャラクター説明` | `声のトーン/声質`
- Use one row per approved delivery character.
- Insert a verified, clear portrait in `キャラクター画像`; keep crop, scale, row height, and visual treatment consistent.
- Write `役割/身分`, relationship wording, character description, and voice direction in natural Japanese for a production company.
- Include only finalized outward-facing facts. Do not expose internal IDs, confidence, evidence paths, episode/timecodes, conflict notes, approval status, model output, or QC commentary.
- Reconcile the workbook after the full-series script is complete so names, relationships, arcs, and voice direction match the delivered dialogue.

## Japanese-language requirement

All production-facing headings, descriptions, labels, notes, status terms, and instructions must be Japanese. Approved official character names, company names, trademarks, IDs, codes, and source-required Roman spelling may remain in their official form. Do not mechanically translate or transliterate approved proper nouns.

Before export, inspect every visible cell and text object for Chinese or English internal workflow commentary. An internal note such as `candidate`, `unknown`, `confidence`, `review`, or an evidence path belongs in `02_script_work`, not in a delivery workbook.

## Cross-workbook checks

- The final script and Koubanhyo use the same normalized role catalog.
- Every script role appears in the Koubanhyo, and the `○` matrix matches actual episode dialogue.
- Every character row uses the approved display name and a verified portrait.
- Character relationships and descriptions do not contradict the full-series script.
- All three workbooks open without formula errors, clipped headers, broken images, unreadable wrapping, or hidden internal sheets.
- `03_final_delivery` contains only the three requested `.xlsx` files.
