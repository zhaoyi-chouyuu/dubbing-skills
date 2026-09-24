# CMS 配音后字幕格式要求

Use this reference only for post-dubbing text formatting. Do not apply translation, localization, rewriting, shortening, or retiming guidance.

## Locked content

- Preserve dialogue words, order, inflection, names, indices, cue count, and timecodes.
- Permit only punctuation, spaces, numeric glyphs, line breaks inside the same cue, dialogue markers, and unambiguous formatting repair.
- Report subtitle/audio mismatch or other non-format issues without correcting them.
- Do not inspect video or audio.

## Punctuation

- Omit `。`.
- Replace `、` with one half-width space.
- Use `「」` consistently for quotations.
- Do not use `（）` in dialogue or internal monologue. Permit them only for approved on-screen name readings.
- Use one `…`, not `……`, `...`, or `．．．`.
- Use question marks, exclamation marks, wave dashes, and em dashes sparingly and consistently with the approved project baseline.

## Numeric notation

- Never use full-width Arabic digits `０-９`.
- Follow project-specific approved feedback before general guidance.
- Without a project baseline, prefer Japanese kanji for 1-10 when natural and width allows; use half-width Arabic digits for values above 10 and for dates, years, times, IDs, UI values, and measurements.
- Under character pressure, use half-width digits if pronunciation and meaning remain unchanged.
- For project 6430, confirmed QC treats full-width Arabic digits as errors; use half-width Arabic digits for matching numeric contexts.

## Layout limits

CPP counts one Japanese/full-width character as 2 units and one ASCII/half-width character as 1 unit.

| Screen | Per line | Per cue |
| --- | ---: | ---: |
| Vertical | 24 | 48 |
| Horizontal | 32 | 48 |

- Prefer one complete Japanese sentence per cue.
- Do not preserve source-language line breaks mechanically.
- Break manually by meaning group only when needed.
- Use at most two lines for ordinary dialogue.
- In a two-speaker cue, prefix each line with `- `.
- If two or more speakers say the same words simultaneously, keep one line without a dash.

## CPS reporting

In post-dubbing mode, report CPS but do not shorten text or change timecodes.

- Actual CPS <= 10: pass.
- Actual CPS 10-14: warning.
- Actual CPS >= 15: blocker for human production review.

CPP may display roughly twice the actual Japanese CPS because one Japanese character counts as two CPP units.

## Precedence

Apply rules in this order:

1. Current project-specific approved QC feedback.
2. Client formatting requirements in this reference.
3. Semantic line-break safeguards.
4. Visual balance preferences.
