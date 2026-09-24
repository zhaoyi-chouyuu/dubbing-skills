---
name: cms-jp-postdub-subtitle-formatting
description: Format, audit, and safely correct Japanese SRT/ASS subtitles after dubbing is complete. Use for CMS short-drama subtitle punctuation, spacing, numeric notation, semantic line breaks, character-width limits, simultaneous-dialogue formatting, CPS reporting, and delivery QC while preserving dialogue wording, subtitle entries, and timecodes.
---

# CMS 日语配音后字幕格式整理

## Default screen mode

This project uses vertical subtitles by default. Use the vertical limits—24 CPP per line and 48 CPP per cue—for every audit, correction, and QC report unless the user explicitly requests horizontal subtitles. Do not run a horizontal audit or apply horizontal limits based on assumption.

## Lock the scope

Work only from subtitle text. Do not inspect video or audio. Record any issue that cannot be resolved from text alone in the QC report.

Do not translate, localize, shorten, rewrite, add, or delete dialogue. Lock dialogue wording, word order, inflection, names, subtitle count, indices, and timecodes. Permit only punctuation normalization, half-width spacing, numeric-glyph normalization, line-break movement inside the same cue, simultaneous-dialogue markers, and unambiguous format repair.

Treat subtitle/audio mismatch, performance, audio quality, echo, and editing issues as out of scope. Report them without changing text.

Preserve the source. Write a sibling `_格式修正版.srt` or `_格式修正版.ass` unless the user explicitly requests in-place editing.

## Read the required references

Read [CMS format requirements](references/cms-format-requirements.md) before every job. Read [semantic line-break cases](references/semantic-linebreak-cases.md) before changing line breaks or spaces.

## Run the workflow

1. Discover `.srt` and `.ass` files and exclude `.DS_Store` and `._*` sidecars.
2. Map filenames to episodes and verify the intended scope.
3. Run `scripts/audit_subtitles.py` before editing. Save its CSV report.
4. Fix high-confidence format errors.
5. Review semantic spacing and line-break candidates from the current cue plus the preceding and following subtitle cues. Do not use video or audio.
6. Leave ambiguous text unchanged and record it as `人工复核` in the report.
7. Run the audit again on corrected files.
8. Compare source and output. Reject changes outside the allowed format-only categories.
9. Verify the output exists, is non-empty, reparses successfully, and preserves every subtitle index and timecode.

Example audit command:

```powershell
& 'E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe' 'C:\Users\zhao-\.claude\skills\cms-jp-postdub-subtitle-formatting\scripts\audit_subtitles.py' <字幕目录> --report <字幕格式质检报告.csv>
```

Do not use the bare `python3` (Microsoft Store launcher) or `python` (general Python 3.14) on this machine.

The audit script defaults to `--screen vertical`. Add `--screen horizontal` only when the user explicitly asks for horizontal subtitles.

## Apply deterministic rules

Apply these automatically when unambiguous:

- Remove `。`.
- Replace `、` with one half-width space.
- Normalize repeated spaces to one and remove leading/trailing spaces.
- Use one `…`; reject `……`, `...`, and `．．．`.
- Use `「」` for dialogue quotations; do not mix quote styles.
- Reject full-width Arabic digits `０-９`. Follow an approved project numeric baseline; otherwise follow the numeric guidance in the CMS reference.
- Keep question/exclamation glyph width consistent with the approved project baseline. Do not batch-convert `?`/`!` and `？`/`！` without such a baseline.
- Preserve `- ` at the start of each speaker line in a two-speaker cue.

## Measure CPP width

Count ASCII and half-width characters as 1 CPP unit. Count Japanese, Chinese, full-width characters, and full-width punctuation as 2 CPP units.

- Vertical: line <= 24; cue total <= 48.
- Horizontal: line <= 32; cue total <= 48.
- Keep ordinary dialogue to at most two lines.

Prefer one line when merged text fits the line limit. Preserve two lines only for distinct speakers, distinct display types, a project-confirmed intentional break, or a necessary semantic split. Without video/audio evidence, report uncertain intentional pauses instead of guessing.

## Protect semantic units

Never break inside:

- a word, inflection, word stem, or ending;
- a katakana word, name, organization, title, or parenthetical reading;
- a number-plus-counter unit;
- a modifier-plus-head unit when either line becomes misleading;
- a particle cluster such as `でも`;
- an auxiliary or fixed construction such as `ている`, `ていく`, `てくる`, `てしまう`, `てみる`, `て見せる`, `てください`, `ても`, `なければ`, or `なくなる`.

Do not treat particles or `て` forms as automatic breakpoints. Use them only after confirming that no protected construction is split and both lines remain natural.

## Choose semantic line breaks

When a cue exceeds one line, generate only safe candidates and rank them:

1. Boundary between complete independent utterances, responses, or commands.
2. Natural boundary between complete meaning groups.
3. End of a complete clause.
4. Conjunction boundary.
5. Particle or `て` boundary only when both sides remain independently readable.

For every candidate, reread the full cue and then each line separately. Confirm that:

- no word or fixed construction is split;
- the upper line does not create a false first reading;
- the lower line connects naturally;
- a modifier remains connected to what it modifies;
- neither line is an orphaned 1-2-character fragment, particle, ending, or auxiliary.

Use visual balance only after semantic safety. Prefer similar line lengths, then a slightly shorter upper line. If no safe candidate exists, leave the cue unchanged and report `人工复核`.

## Handle semantic spaces

Insert one half-width space between adjacent independent utterances only when both are complete, such as `わかった いいだろう`. Do not trigger on a keyword alone: do not split `わかったよ`, `わかったら`, `いい子`, `いい加減`, or `思い知るがいい`.

Treat `いい`, `わかった`, `なさい`, and similar forms only as review triggers. Determine their grammatical role from the whole cue and adjacent subtitle text.

## Use confidence levels

- `阻断`: structure failure, changed timecode/index, width overflow, word-internal break, protected-construction break, or unauthorized text change.
- `警告`: high CPS, possible unnecessary line break, severe imbalance, or likely missing semantic space.
- `人工复核`: semantic boundary, intentional pause, speaker identity, or display type cannot be decided reliably from subtitle text.

Auto-fix only high-confidence typography. Apply a semantic line-break change only after the text-only review passes. Never invent evidence from unavailable video or audio.

## Report and deliver

Deliver corrected subtitle files plus a CSV report with episode, filename, cue index, timecode, severity, rule ID, original text, corrected/proposed text, and reason.

Do not claim completion until the final audit has run and every remaining blocker is either fixed or explicitly listed in the report.
