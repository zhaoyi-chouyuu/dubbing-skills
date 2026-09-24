---
name: dubbing-script-automation
description: "Create and revise Japanese short-drama dubbing scripts from the preparation-stage anonymous Speaker draft through one continuous beta1.5wsl workflow: first-episode manual confirmation, a CAM++/ECAPA512/ResNet34 voice ensemble, dialogue/context review, TalkNet supporting evidence, and three final Japanese Excel workbooks. After the first-episode gate, it pauses only at the end of an episode containing a new named speaking role or unresolved named-role identity; otherwise it continues through final delivery."
metadata:
  version: "beta1.5wsl"
---

# Dubbing Script Automation — beta1.5wsl

Use this skill only after the preparation package has explicit user approval. Claude Code (the main session) remains the final role reviewer. This skill has one workflow only: **beta1.5**. There is no separate default, experimental, or production branch.

## Highest-priority execution invariant — continuous run and only valid stopping points

This invariant governs the entire skill and overrides any phase-level wording such as "episode complete", "batch complete", "draft complete", or "review complete". Once the preparation package is approved, continue working without yielding the task merely because a local phase or group of episodes has finished.

Process episodes sequentially, one episode at a time, in numeric order. **One episode at a time is the processing unit, not a stopping cadence.** Do not combine routine review into fixed five-episode confirmation batches, and do not pause, yield a final response, or ask the user to say “continue” merely because one episode has finished.

The workflow may pause or end only in one of these states:

1. **Required whole-episode confirmation gate:** finish every source row in the affected episode and export the single-format `総合脚本` confirmation workbook before pausing. Episode 1 always opens this gate. After episode 1, open it only when a new named role first speaks or a named-role identity remains unresolved after the required evidence review. Aggregate all such roles into one gate for that episode. Generic, crowd, service, passerby, numbered, or silent visual roles do not open a gate.
2. **Actual external blocker:** a required source is missing or corrupt, a mandatory runtime/preflight fails, required access is unavailable, or another condition outside the workflow makes safe progress impossible. Report the exact blocker; never present it as normal completion.
3. **Full workflow completion:** every supplied episode and row has passed the required review and QC, the reconciled character data is complete, and exactly three formal Japanese Excel workbooks — `総合脚本`, `香盤表`, and `登場人物設定表` — exist under `03_script/03_final_delivery/` and have passed cross-workbook, portrait, layout, and Japanese-language validation.

No other condition is a stopping point. In particular, do not stop for a completed episode, completed batch, progress update, weak or missing voice profile, CAM++ failure, model disagreement or abstention, insufficient anchor duration, generic-role appearance, successful intermediate export, or completion of the last episode before formal delivery. Record non-blocking limitations internally and continue. Progress commentary is informational only and must not end the task.

At every episode boundary, apply this mandatory auto-advance rule:

- If episode-level source integrity, role review, continuity checks, and QC pass, and no required named-role confirmation gate is pending, immediately start the next numbered episode in the same task. Do not wait for user input.
- If a check fails for a reason that can be repaired inside the authorized workflow, repair it and rerun the check; a fixable validation failure is not permission to stop.
- Pause only when the episode has reached the required whole-episode human confirmation gate above or an actual external blocker exists. State the exact required user action or blocker.
- After the last episode passes, immediately continue through reconciliation, assembly, three-workbook finalization, and final delivery validation. The last episode passing is not completion by itself.

When the user confirms or corrects a gate, close it atomically, directly extract any eligible anchors under the standing authorization, rebuild/version the gallery when applicable, recheck the affected episode, and resume with the next episode in the same task. Continue until the next valid gate or full workflow completion. Do not wait for a separate "continue" instruction after a gate has been confirmed.

## Mandatory fail-closed confirmation and delivery route

These controls are non-optional and exist to prevent a user-corrected episode from being omitted during final assembly:

1. **One formal delivery route only.** Create formal client workbooks only with `dubbing_tool.py assemble-delivery` followed by `finalize_japanese_delivery.py`. Never create an ad-hoc assembler, read `*_話者確認.xlsx` directly for final roles, or write a substitute script into `03_final_delivery`. Missing `final_roles.tsv`, source-bound `qc_report.json`, approved scripts, character data, or another required input is a fail-closed blocker that must be repaired through the normal workflow; it is never permission to bypass the official commands.
2. **Every human confirmation has file-backed provenance and an explicit approval event.** Close a confirmation gate only by passing the exact user-approved `総合脚本` workbook to `confirm_episode.py --confirmation-xlsx`, plus `--approval-mode` and a non-empty `--approval-note`. If both `*_話者確認.xlsx` and `*_話者確認_用户红字回写.xlsx` exist, use the red-return workbook with `corrected_workbook`; its cell values are authoritative and red font is audit-only visual markup. If only the ordinary confirmation workbook exists, use `confirmed_unchanged` when the user explicitly confirms no changes **or instructs the workflow to continue to the next episode/continue the job after receiving that review file**; that continuation instruction is the confirmation event and must be recorded in `approval_note`. File existence alone, background processing, or an agent deciding to continue without the user's instruction is never approval. The command must match every workbook row to the complete episode TSV by timecode and exact dialogue, write the accepted role to all confirmation TSVs, store the workbook SHA-256 and row-level decisions in `user_confirmation_manifest.json`, and fail on missing, extra, reordered, changed source rows, a changed workbook declared unchanged, or a corrected workbook with no actual role correction.
3. **Human-approved roles are locked.** Every row imported from the approved workbook must carry `user_locked=true` and `user_locked_role=<approved role>`. Later voice, semantic, visual, normalization, review, QC, and delivery steps may not change that internal role. A different client-facing spelling is allowed only through the already approved Japanese name map; never shorten or generalize an already approved Japanese generic label.
4. **Formal delivery is atomic.** Build all three workbooks in a sibling staging directory, validate that staging contains exactly the three expected files, and only then replace `03_final_delivery` as one directory-level commit. On failure, preserve the previous delivery unchanged. Do not leave ordinary, corrected, or final variants side by side.

Run the bundled user-confirmation regression test after changing confirmation or delivery code. Its episode-0003 fixture locks `00:00:09,033` to `宦官（モブ）` and `00:00:24,380` to `皇后付き侍女`; either role being dropped or rewritten must fail.

## Single beta1.5 workflow

The preparation package supplies an episode-local no-name script whose rows contain `Speaker01`, `Speaker02`, and so on. The first episode is mapped to characters and drafted completely, then paused once for the user to confirm the full character-to-dialogue mapping. After that confirmation, directly extract eligible confirmed single-speaker intervals as named voice anchors; the user's standing workflow instruction authorizes extraction and no separate per-project extraction request is needed. Later episodes start from anonymous acoustic clusters and must attempt CAM++, ECAPA512, and ResNet34. Voice-gallery QC and consensus are evaluated per role: a role may proceed when at least two of the three attempted models have QC-usable profiles and agree on that role, even if the third model disagrees or abstains after role-level QC. Check every voice result against dialogue logic, and use TalkNet only when voice plus logic still cannot resolve the speaker and a reliably mapped speaking face is visible.

If first-episode confirmation or usable anchors are missing, finish that episode's draft, open the normal episode-boundary confirmation gate, and record `no_reliable_gallery` where needed. Do not create an alternate branch or silently weaken the evidence gates. Internal checkpoints use `workflow_stage=beta`; the three client workbooks do not contain internal beta1.5 labels. beta1.5 describes the workflow maturity, not permission to skip final QC or human confirmation.

> [!IMPORTANT]
> **Formal Japanese Delivery Contract**:
> Deliver exactly three independent Excel workbooks to the Japanese production company:
> 1. **`<作品名>_総合脚本.xlsx`**: one `総合脚本` sheet with `名前` | `タイムコード` | `台詞`; episode headers use `第0001話`, `第0002話`, and timecodes use `00:00:00,000`.
> 2. **`<作品名>_香盤表.xlsx`**: one `香盤表` sheet mapping every script role across every episode with `○` markers and `総出演話数`.
> 3. **`<作品名>_登場人物設定表.xlsx`**: one `登場人物設定` sheet in the production-company format with `名前` | `キャラクター画像` | `役割/身分` | `性別` | `年齢` | `キャラクター説明` | `声のトーン/声質`.
>
> Do not combine these into a three-sheet master workbook for formal delivery. An explicitly requested Japanese preliminary cast overview may coexist directly under `03_script/` as an earlier preparation-stage handoff; it is not part of the formal three-file delivery and must never be copied into `03_final_delivery`. The internal character-reference workbook, review evidence, QC, TSVs, and checkpoints are working artifacts and are not a fourth formal deliverable. Read [references/japanese-delivery-contract.md](references/japanese-delivery-contract.md) before building or validating a formal delivery.

> [!IMPORTANT]
> **100% Japanese client-facing content**:
> Chinese source-language role keys may exist only inside internal TSV/JSON evidence. Every user-visible confirmation script and all three formal deliverables must use Japanese throughout, including every role name, generic speaker label, identity/relationship term, description, heading, note, and status. This applies equally to `総合脚本`, `香盤表`, and `登場人物設定表`.
>
> - Treat `画面字.xlsx` column `trans_content` as the primary authority for an on-screen character's official Japanese spelling and reading. The workbook may carry an incorrect worksheet dimension such as `A1`; if ordinary read-only loading shows only a header, reopen without read-only/dimension truncation and inspect the actual populated rows before concluding that the data is absent.
> - Build and retain one explicit internal-to-Japanese name map before rendering any user-facing workbook. Use the official Japanese spelling consistently across confirmation scripts, the final combined script, cast sheet, and casting schedule. Do not expose a Chinese role key when a formal Japanese form exists.
> - For roles without an on-screen formal name, create a natural Japanese production label such as `システム音声`, `皇后付き侍女`, `男性音声01`, or `宦官（モブ）`; never leave a Chinese generic label in a client-facing workbook.
> - Run a final non-Japanese leakage audit across all populated text cells of all three deliverables. Any remaining Simplified Chinese form or untranslated Chinese description blocks delivery until corrected. Shared CJK characters are allowed only when they are also the approved Japanese spelling or normal Japanese text.

> [!IMPORTANT]
> **One script format only — confirmation and final**:
> Every script workbook shown to the user must use the same `総合脚本` format, whether it is the first-episode confirmation script, a later episode-boundary confirmation script, a corrected/resumed confirmation script, or the final combined script. There is no separate user-facing confirmation-table format.
>
> - Worksheet name: `総合脚本`.
> - Columns, in order: `名前` | `タイムコード` | `台詞` — no additional columns or user-facing evidence sheet.
> - Insert an episode section row such as `第0001話`; format timecodes as `00:00:00,000`.
> - Preserve every source dialogue row, order, line break, dialogue string, and start timecode. Show the role name at a speaker transition; consecutive rows by the same speaker may leave `名前` blank, matching the final script convention.
> - The user confirms or corrects the proposed mapping through the `名前` column or by stating the affected timecode/line and correct role.
> - Keep row indexes, proposed-role fields, confidence, semantic/voice/TalkNet evidence, conflict reasons, approval controls, and reviewer notes only in internal TSV/JSON/checkpoint artifacts under `02_script_work`; never add them to a script workbook shown for confirmation.
>
> A confirmation filename may identify its review state and the workbook remains under `02_script_work`, but its internal layout is still exactly the final `総合脚本` layout. Creating or opening the workbook does not count as user confirmation.

## Local runtime

This installed Claude Code skill directory (`C:\Users\zhao-\.claude\skills\dubbing-script-automation`) is the sole source of truth and the permitted runtime for this skill. Do not discover, read, execute, compare, copy, synchronize, or fall back to any similarly named copy outside this directory, including the Codex copy under `C:\Users\zhao-\.agents\skills`. If this installed skill is missing or invalid, stop with an actual runtime blocker instead of substituting another copy.

Use WSL2 Ubuntu and the RTX GPU for CAM++, ECAPA512, ResNet34, and TalkNet. Keep workbook, JSON, review, and delivery scripts on Windows unless they directly invoke those models. The PowerShell launcher translates Windows drive paths, runs the installed skill file through `/mnt/c`, and keeps large temporary frame/audio work in WSL `/tmp`:

```powershell
$PYTHON = 'E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe'
$SKILL_DIR = 'C:\Users\zhao-\.claude\skills\dubbing-script-automation'
$TOOL = "$SKILL_DIR\scripts\dubbing_tool.py"
$VOICE_TOOL = "$SKILL_DIR\scripts\voice_evidence.py"
$VOICE_ENSEMBLE = "$SKILL_DIR\scripts\voice_ensemble.py"
$TALKNET_RUNNER = "$SKILL_DIR\scripts\talknet_evidence.py"
$WSL_GPU = "$SKILL_DIR\scripts\run_wsl_gpu.ps1"
$VOICE_MODEL_CAMPLUS = 'E:\AI_Models\pyannote\wespeaker-voxceleb-campplus'
$VOICE_MODEL_ECAPA512 = 'E:\AI_Models\pyannote\wespeaker-voxceleb-ecapa-tdnn512'
$VOICE_MODEL_RESNET34 = 'E:\AI_Models\pyannote\wespeaker-voxceleb-resnet34-LM'
$TALKNET_ROOT = 'E:\AI_Models\tools\talknet-asd'
$FFMPEG = '/usr/bin/ffmpeg'
$FFMPEG_WINDOWS = 'E:\AI_Models\RVC\RVC20240604Nvidia50x0\ffmpeg.exe'
```

The WSL runtime is `/home/zhaoyi/miniconda/envs/stable-ai/bin/python`; `--device auto` selects the RTX 4070 and falls back to CPU only when CUDA is unavailable. Models remain on `E:` and are reused through `/mnt/e`. Run `$VOICE_TOOL` and `$TALKNET_RUNNER` through `$WSL_GPU`; use `$PYTHON` for the other scripts. The Windows runtime remains the direct CPU fallback. Do not use the Microsoft Store `python3` launcher or the general Python 3.14 installation. Run preflight before writing output.

Claude Code runs each PowerShell tool call in a fresh session, so variables do not persist between calls. Prepend the variable assignments each command needs to that same call. Run voice scoring, TalkNet, and other long GPU jobs with `run_in_background` (or a long `timeout`), and check their output when notified instead of polling. Inspect extracted frames and face crops directly with the Read tool, which displays images.

## Project output contract

The preparation package is always a direct child of the current script directory. Resolve `<package>/..` as the script root and `<package>/../..` as the current source-production root. The package position is authoritative; directory-name tokens such as `script` and `feature` are useful hints but are not required fixed strings. Numeric prefixes, suffix annotations, and project-specific names may vary. Keep every retained automation artifact under that resolved script directory:

```text
03_script/
├─ <project>.xlsx               # user source; unchanged
├─ 画面字.xlsx                  # user source; unchanged
├─ <project_id>_主要登場人物一覧_前期参考版.xlsx  # optional preparation-stage handoff; not formal delivery
├─ 01_preparation/               # approved preparation evidence
├─ 02_script_work/               # review_all.tsv, corrected role data, checkpoints, and intermediate renders
└─ 03_final_delivery/            # exactly three client-facing Japanese Excel workbooks
```

Use `03_script/02_script_work` for the approved internal character reference, row-review evidence, resumable work, QC, and optional secondary Word renders. The optional preliminary cast overview may remain at the `03_script` root and does not participate in script generation, approval, or formal-delivery validation. Use `03_script/03_final_delivery` only for the three formal Japanese Excel workbooks. Do not place the preliminary overview, internal evidence, reports, JSON, TSV, DOCX, or checkpoints in that client-facing folder. Do not write formal script outputs inside the preparation package, beside the project folder, under video/subtitle folders, or to a generic Desktop output directory. Never overwrite the two user-supplied workbooks.

## Start gate

Validate the approved package before generating anything:

```bash
& $PYTHON $TOOL continue-from-preparation --package <preparation_package> --require-ready
```

Stop and return to preparation if the manifest, source hashes, episode mapping, derived assets, review, or user approval is stale. Do not stop merely because the project or an ancestor directory was renamed. First resolve package-internal files relative to the package and external files inside the current feature root using, in order, the recorded feature-relative locator, a rebased legacy suffix, then basename/episode fallback; require the recorded SHA-256 and a unique fallback match. Never search the sibling `finish` area or another project. Report every path relocation in the validation result without rewriting the approved package.

Current schema-11 preparation packages intentionally have no `00_source_portal`: original material stays in the current source-production area and is validated by portable locators and hashes. Treat `video`, `subtitle`, `script`, `casting`, `sound`, `vocal`, and `fix` as common semantic hints, not a closed list. New folder types may be inspected and used when hierarchy, file type, content, episode identity, and hashes establish their role. Never substitute material merely from a folder name, and never search delivery/`finish` paths. Continue to accept older approved packages that contain the legacy portal or stale absolute paths; when their content inventory and hashes are unchanged, preserve the existing approval across a path-only rename. Never require or recreate the portal for a current package.

## Subagents

Use the Claude Code Agent tool (`general-purpose` subagent) for bounded parallel work such as per-episode evidence organization, subtitle coverage checks, role-whitelist validation, continuity-conflict collection, and preliminary QC. Pass `model: "sonnet"` for these bounded tasks (or `"haiku"` for purely mechanical checks). Launch independent subagents in one message so they run in parallel.

Use a separate subagent for the independent blind audit. Give it only the source SRT, frames, approved internal character reference, and relationship graph — never the current roles, `review_all.tsv`, or the main session's reasoning — so it cannot see what it is auditing. Record its auditor name as `claude-blind-audit` (or another name distinct from the generation reviewer); `dubbing_tool.py` rejects an audit whose auditor equals the `reviewed_by` / `audio_reviewed_by` value, which the main session records as `claude-code`.

If a model override is unavailable, use the default subagent model and continue without blocking the workflow. Give each subagent an explicit episode, row range, inputs, required output schema, and prohibition against modifying source material. Treat every subagent result as a draft. The main agent must consolidate conflicts, inspect ambiguous evidence, make every final speaker decision, apply reviews, and approve delivery.

## Claude Code role-review workflow

Use [references/role-labeling.md](references/role-labeling.md) for row-level review fields, the blind audit, and the error-prevention review tips. Those tips trigger closer inspection and prevent mechanical mistakes; they do not replace this workflow's evidence order or allow any single cue to assign a role automatically. When original-audio listening is required, use [references/coarse-audio-review.md](references/coarse-audio-review.md).

Claude Code (the main session) is the automatic first-pass reviewer in this workflow and records itself as `reviewed_by=claude-code`. Do not call `label-roles` or `refine-roles`, and do not configure an external model API or API key.

The tool keeps legacy identifiers from its Codex origin — the `prepare-codex-review` command, `codex_review` status, and `codex_review_queue.jsonl` / `codex_labels.tsv` / `codex_draft.tsv` files. These are schema names shared with existing packages; use them as-is and do not rename them.

The approved preparation package already contains the canonical SRT copy, episode-local anonymous Speaker draft, three-frame manifest, review queue, editable labels, approved internal character reference, relationship graph, and face references. Copy or reference those validated artifacts under `02_script_work/episodes/<episode>`; do not rerun diarization, extract the same frames, or rebuild the same queue unless `continue-from-preparation` reports them stale and the package is returned to preparation.

For each episode:

1. Consume `anonymous_script.tsv` and `subtitle_speaker_map.tsv` from preparation as the structural first draft. Preserve `SpeakerNN` as an episode-local acoustic cluster until evidence maps it to a character. A cue marked `MULTI_SPEAKER_REVIEW` or `UNRESOLVED` must be reviewed directly; do not force it into the nearest cluster.

2. Consume the validated `source.srt` and `frames_manifest.json` from preparation. The following command is repair-only when preparation evidence is missing or stale; after running it, refresh and reapprove preparation:

```bash
& $PYTHON $TOOL extract-frames --video <episode.mp4> --srt <episode.srt> --out-dir <frames_dir> --frames-per-subtitle 3
```

3. Consume the validated review queue from preparation. Regenerate it only as part of an explicit preparation refresh:

```bash
& $PYTHON $TOOL prepare-codex-review \
  --srt <episode.srt> --manifest <frames_manifest.json> \
  --roles <approved_internal_character_reference.xlsx_or_tsv> --episode <episode_id> \
  --out-jsonl <review_queue.jsonl> --out-tsv <codex_draft.tsv> \
  --out-relationships <relationship_graph.json> \
  --face-reference-manifest <face_reference_manifest.json>
```

4. After first-episode confirmation, attempt separate CAM++, ECAPA512, and ResNet34 galleries from the same user-confirmed intervals. Do not require an entire model gallery to pass for every role. Retain only that model's QC-usable role profiles, score all three models whenever each has a scoreable role subset, and merge identical reviewed acoustic turns with `voice_ensemble.py`. If one model has no scoreable role subset, retain its failed report as the required third-model attempt. `consensus_3_of_3` or `consensus_2_of_3` is strong supporting evidence, not an automatic role assignment. Two-model disagreement or fewer than two eligible votes for the same candidate role means abstention. WavLM is disabled.
5. Check the ensemble candidate against the whole semantic unit plus at least two rows before and after it: relationships, turn-taking, vocatives, self-reference, entrances/exits, question-answer structure, and scene continuity. Record agreement, conflict, or limitation explicitly.
6. Only when the voice ensemble and dialogue logic still cannot resolve the character, run TalkNet on the original episode if a speaking face is visible. Map face tracks only from approved face references and continuous scene identity. TalkNet answers which visible track is speaking, not who an off-screen voice belongs to; preserve abstention and conflicts.
7. For every row, write `review_all.tsv` with the anonymous cluster, final role or stable Japanese generic role, confidence, the three-model or documented two-model voice result, semantic evidence, visual class, identity evidence, face-match status, TalkNet status/candidate when used, and explicit resolution of unclear/reaction/off-screen/cutaway cases. Use `男性音声01` / `女性音声01` style labels and one stable `generic_speaker_key` per unnamed speaker.
8. When continuous video is required, inspect only the minimum surrounding interval. Coarse source listening may identify broad traits such as male/female/unclear, but it never establishes a named identity.
9. Apply the reviewed TSV. Use `--allow-legacy-review` only for deliberately limited first-version work; formal delivery must carry the normal review evidence fields.

```bash
& $PYTHON $TOOL apply-review --tsv <codex_draft.tsv> --review-tsv <review_all.tsv> \
  --roles <approved_internal_character_reference.xlsx_or_tsv> --out-tsv <reviewed_roles.tsv> --require-all
```

Do not use `merge-voice-evidence` to alter roles or QC with `--require-voice-audit`. Voice evidence is supporting evidence; leave voice fields empty or `not_run` when no reliable anchor exists.

## First-episode voice-anchor gate

> [!CAUTION]
> The entire workflow is beta1.5 and still requires human confirmation. Mark internal checkpoints `workflow_stage=beta`, `voice_evidence_authority=supporting`, and `automatic_identity_assignment=false`. Do not put these internal labels in the three client-facing Excel files. Client delivery is allowed only after every episode-review gate, semantic/video review, independent audit, and final QC pass. Do not claim 100% automatic accuracy or a validated replacement for human review.

The following gates are mandatory for every project:

- the user has confirmed every first-episode speaker assignment as correct;
- after that mapping confirmation, Claude Code directly extracts only eligible clean anchors from the confirmed rows under the user's standing authorization; do not ask for a separate extraction authorization unless the user has explicitly opted out for that project; and
- the user has accepted that a later episode containing a new or unresolved named role is completed in draft first, then pauses at the episode boundary for whole-episode manual confirmation.

Preserve the confirmed first episode unchanged as the identity baseline. Build the voice gallery according to [references/beta-voice-anchor-workflow.md](references/beta-voice-anchor-workflow.md). A role identity may enter the gallery only from a user-confirmed named row and an accepted continuous single-speaker interval.

### Evidence order

Use the anonymous segmentation as structure, confirmed voice identity as the first identity signal, and dialogue logic as the required cross-check:

1. Keep the preparation-stage `SpeakerNN` boundary and review any cue-level overlap warning.
2. Attempt a reviewed continuous single-speaker acoustic turn independently with CAM++, ECAPA512, and ResNet34. Keep each model's role-level usable-profile list, top candidate, second candidate, similarity, margin, quality status, and gallery ID, plus the failed report when a model has no scoreable role subset. Then compute per-role three-model or two-of-three consensus without overwriting a role.
3. Review the complete semantic unit, surrounding turns, vocatives, self-reference, relationships, question-answer structure, knowledge ownership, and scene continuity. Voice and logic must not be collapsed into one circular score.
4. If voice and logic remain unresolved, use picture and TalkNet only as supporting context for visible active speaking, presence, entrance/exit, reaction shots, off-screen speech, phone calls, flashbacks, and scene changes. A visible or centered face and a TalkNet score are never standalone character-identity proof.

Decision policy:

- reliable three-model ensemble or per-role two-of-three consensus and logic agree: retain both evidence records and confirm through the normal beta1.5 review;
- logic is clear but voice is weak or unavailable for a non-main role: keep the logic result and record the voice limit;
- the voice ensemble has three-of-three or per-role two-of-three consensus but logic is incomplete: keep it as a candidate and review the surrounding semantic unit before confirmation;
- fewer than two eligible models agree on the same role, or the two eligible models disagree: abstain from voice identity and continue with logic;
- logic and voice conflict: do not auto-resolve;
- both are unclear: do not force a named role.

### Episode-boundary named-role review gate

Define a main/named role from the user-approved internal character reference: the character has a complete personal name and is not a generic, crowd, service, passerby, or numbered voice label. Do not infer main-role status from string length alone.

After the confirmed first episode, a named role's first speaking appearance or unresolved identity opens a pending episode-review gate when any of these statuses occurs:

- the role first speaks but has no accepted gallery profile;
- `no_gallery`, `no_reliable_gallery`, weak/low-quality/short/mixed/overlapping audio, or failed scoring;
- the top candidates are too close or the calibrated similarity/margin gate is not passed;
- voice identity conflicts with dialogue logic;
- dialogue logic and voice evidence cannot uniquely identify the named role.

A silent visual appearance does not open the gate; the first speaking turn does. Generic and unnamed roles do not open this gate and must retain stable generic labels.

Do not interrupt the user at the triggering row. Mark the role decision as provisional, continue through every remaining source row in the same episode, and finish the complete episode draft. If several new or unresolved named roles appear in that episode, aggregate them into one gate. Do not process the next episode while the gate remains open.

At the episode boundary, export one complete internal review dataset containing every dialogue row in source order, including rows assigned to already-known roles. Use `export-review --all`; the `current_role` column is the proposed speaker mapping being reviewed. Keep this TSV and the checkpoint under that episode's `02_script_work` directory. From it, render the user-facing episode confirmation workbook in the single `総合脚本` format defined above, then pause once and ask the user to review the whole episode's character-to-dialogue mapping, not only the triggering rows. Never send the evidence-bearing TSV or an expanded confirmation table as the user's script.

```bash
& $PYTHON $TOOL export-review \
  --srt <episode.srt> --manifest <frames_manifest.json> \
  --tsv <complete_episode_draft.tsv> \
  --out-tsv <episode_work_dir/episode_role_confirmation.tsv> --all
```

The internal episode-boundary review dataset must retain:

- all episode rows with episode, source row/index, timecode, dialogue, and proposed role;
- every new named role and the row where that role first speaks;
- all unresolved or conflicting rows, with dialogue-logic candidate and concise evidence;
- voice top/second candidates, similarity, margin, quality status, and rejection/conflict reason where voice evidence was used;
- TalkNet visible-track status, mapped role candidate, and abstention/conflict reason where TalkNet evidence was used;
- reviewable source-audio intervals and pictures only where they materially help.

After the user corrects or confirms the complete episode mapping, import every accepted decision from that exact workbook into the normal review TSVs using the standardized confirmation script:

For this gate, a user instruction such as “继续往下作业” or “继续下一集” after the ordinary review workbook has been supplied means that the workbook is confirmed unchanged. Record that instruction as the approval note, close the current episode, and continue automatically. If a red-return workbook exists, it always takes precedence and must be imported as `corrected_workbook` before continuing.

```bash
& $PYTHON "$SKILL_DIR\scripts\confirm_episode.py" \
  --project-dir <project_01_feature_dir> \
  --episode <episode_id> \
  --confirmation-xlsx <exact_user_approved_総合脚本.xlsx> \
  --approval-mode <corrected_workbook|confirmed_unchanged> \
  --approval-note <explicit_user_confirmation_or_correction_statement> \
  [--staging-dir <staging_dir>]
```

This command guarantees atomic closure:
1. Matches the approved workbook to the complete episode TSV by row count, timecode, and exact dialogue; no source row may be added, removed, reordered, or rewritten.
2. Writes every approved role into the TSVs, sets `user_locked=true` and `user_locked_role`, and flips all rows to the QC-compatible human-reviewed state: `status=manual`, `identity_status=confirmed` for named/user-specific labels or `generic` for supported generic labels, and `confidence=1.00`.
3. Writes `user_confirmation_manifest.json` with the explicit approval mode and note, source path, SHA-256, approved rows, and role changes.
4. Sets `episodeXXXX_checkpoint.json` and `episode_checkpoint.json` to `status=full_episode_mapping_confirmed` and `user_mapping_confirmed=true`.
5. Sets `episodeXXXX_role_confirmation.json` item status to `confirmed`.
6. Performs an automated audit verifying the explicit approval, manifest, row locks, and zero leftover non-`manual` rows. A valid file-backed whole-episode user lock supersedes the internal blind audit for that locked row; all non-user-locked rows still require the independent audit.

Then directly extract only eligible clean anchor intervals from the newly confirmed named-role rows, and rebuild and version the gallery for later episodes. Do not request a separate extraction authorization unless the user has explicitly opted out for that project. Recheck the current episode against dialogue/video evidence and its pre-existing leakage-safe gallery only; never score it using its newly enrolled anchors. Continue according to the highest-priority execution invariant. Never manufacture user approval or treat the episode draft as final.

## TalkNet supporting evidence

Use local TalkNet directly inside the normal role-review workflow as supplementary evidence. Read [references/talknet-supporting-evidence.md](references/talknet-supporting-evidence.md) before running it. It answers which visible face track appears to be speaking; it does not identify an off-screen speaker or establish a character name by itself.

Start each processing session with:

```bash
& $WSL_GPU $TALKNET_RUNNER preflight --tool-root "$TALKNET_ROOT" --device auto
```

Run TalkNet on the same original episode used for script review and store the annotated video, scores, face crops, track mapping, and subtitle-level evidence under `03_script/02_script_work/episodes/<episode>/talknet_evidence`. Do not create a separate frozen branch or comparison experiment.

Map face tracks to approved characters only from approved face references and continuous scene identity. Treat `single_positive_track` with a reliable track-to-role mapping as positive supporting evidence. Treat `no_visible_track`, `no_positive_track`, `multiple_positive_tracks`, an unmapped face, reaction shots, cutaways, off-screen speech, overlap, occlusion, rapid cuts, and audio-video drift as abstention or review states. TalkNet may help rule out a visible non-speaking reaction face, but it cannot name the off-screen speaker.

Record the TalkNet status and candidate in the normal review evidence. Agreement may strengthen an otherwise supported decision; disagreement must return the semantic unit to context/video/voice review. TalkNet never fills or changes `final_role` automatically and never opens a separate TalkNet-only user-confirmation gate.

## Review and client delivery

Review every supplied episode and row, preserve an evidence-bearing `review_all.tsv` per episode, run continuity checks, and keep review copies under `02_script_work`. beta1.5 voice artifacts support review but never become the sole identity authority. Apply all corrections through review TSVs, run an independent blind audit that cannot see the current roles or reasoning, return disagreements to normal review, reconcile the approved internal character reference against the completed full-series script, then QC and render all three Japanese Excel workbooks.

## Render and verify

For each reviewed episode, keep the validated preparation `source.srt` alongside `final_roles.tsv`, or pass its canonical path explicitly. After applying the complete review and blind-audit agreement, run source-bound QC. The assembler creates its own internal Word renders from the approved TSVs:

```bash
& $PYTHON $TOOL qc --tsv <episode_work_dir/final_roles.tsv> \
  --srt <validated_preparation_source.srt> \
  --roles <approved_internal_character_reference.xlsx_or_tsv> \
  --report <episode_work_dir/qc_report.json> \
  --require-reviewed-all --fail-on-issues
```

A QC report predating beta1.5 lacks the source-SRT snapshot and must be regenerated. Source index, row order, exact dialogue (including line breaks), and both timecodes must match; passing a structurally valid TSV alone does not prove completeness.

Assemble and audit working artifacts under `02_script_work`; do not use the legacy three-sheet `render-excel` output as the formal delivery. `assemble-delivery` is now an internal work-package command and must never target `03_final_delivery`:

```bash
& $PYTHON $TOOL assemble-delivery \
  --input-root <feature_root/03_script/02_script_work/episodes> \
  --expected-episodes <feature_root/03_script/01_preparation/handoff_manifest.json> \
  --out-dir <feature_root/03_script/02_script_work/delivery_work> \
  --require-complete --overwrite
```

Then create the formal three-file Japanese delivery with separate work and output roots:

```bash
& $PYTHON "$SKILL_DIR\scripts\finalize_japanese_delivery.py" \
  --episodes-root <feature_root/03_script/02_script_work/episodes> \
  --work-dir <feature_root/03_script/02_script_work/delivery_work> \
  --out-dir <feature_root/03_script/03_final_delivery> \
  --name-map-xlsx <feature_root/03_script/02_script_work/japanese_name_map.xlsx> \
  --character-xlsx <feature_root/03_script/02_script_work/登場人物設定表_作業版.xlsx> \
  --out-script <作品名>_総合脚本.xlsx \
  --out-kouban <作品名>_香盤表.xlsx \
  --out-characters <作品名>_登場人物設定表.xlsx
```

`assemble-delivery --overwrite` performs a clean rebuild of its generated work directory. It removes only known generated children; unknown files block cleanup. Never pass `--include-review` for a client-delivery build. The finalizer accepts only `approved_scripts`, compares every Word row against the QC-approved TSV for role, start timecode, and exact dialogue, then exports from the TSV-authoritative values. It refuses extra files in `03_final_delivery`, validates the seven-column character workbook and embedded portraits, and writes its QC report under `02_script_work` unless an explicit internal report path is supplied. Build the three client workbooks according to [references/japanese-delivery-contract.md](references/japanese-delivery-contract.md).

The finalizer must also validate every human-confirmed episode's `user_confirmation_manifest.json` and locked roles, build into a sibling staging directory, and promote that directory only after all three files pass validation. Do not delete or partially overwrite the previous formal delivery before the staged replacement is ready.

Use the completed, QC-approved role TSVs as the single source for both the script workbook and Koubanhyo. Build the client-facing character-settings workbook from the reconciled internal character baseline plus verified character portraits. The character workbook must not expose confidence, evidence, internal IDs, conflict notes, or review status.

Before delivery, verify source-index coverage, source text/timing equality, role catalog validity, no duplicate or missing rows, script-to-Koubanhyo role equality, character-name consistency, embedded portrait visibility, workbook layout, and Japanese-language compliance. The three final filenames and worksheet names must be Japanese. Approved official proper names, trademarks, IDs, and timecodes may retain their required original spelling; all headings, descriptions, status terms, notes, and production-facing instructions must be Japanese. Resolve or remove Chinese/English internal workflow commentary before export.

## Boundaries

- Formal delivery consists of exactly three independent Japanese `.xlsx` files: `総合脚本`, `香盤表`, and `登場人物設定表`.
- Every script workbook shown to the user, including any confirmation script, uses only the `総合脚本` three-column layout; never create an alternate user-facing script or confirmation-table format.
- The optional Japanese preliminary cast overview is a preparation-stage handoff outside `03_final_delivery`; it may coexist under `03_script` but is not a fourth formal deliverable or an identity source.
- Never substitute the internal character reference for the client-facing `登場人物設定表`.
- Never combine the three formal outputs into one workbook unless the user explicitly changes the delivery contract for that project.
- Never leave Chinese/English internal workflow notes in a Japanese production-facing workbook; preserve approved official names and source-required Roman spelling.
- Keep all intermediate script work and formal delivery artifacts under the current feature root's `03_script` directory.
- Never modify source SRT dialogue, order, line breaks, or timing.
- Never use a centered or visible face as standalone speaker proof.
- Never treat a voice candidate as proof, use it for an automatic role change, or call it production verification.
- Attempt all three configured identity models and preserve all three outcomes. Evaluate gallery usability per role, not by invalidating an entire model because another role failed. A role may pass on two-of-three consensus only when two independently QC-usable model profiles vote for the same role on the identical reviewed turn and dialogue logic does not conflict; the third model may disagree or abstain after documented role-level QC. Never use one model, omit the third-model attempt, or describe a two-model result as three-of-three. Missing model files or failed runtime preflight still block the run; WavLM is not a fallback.
- Never interpret `Speaker01` in one episode as the same person as `Speaker01` in another episode.
- Never treat a TalkNet score as character identity or use it for an automatic role change. Use it only as supporting evidence within the normal semantic/video review, and record abstention whenever the speaking face is not reliably visible and mapped.
- Never skip rows or episodes silently, invent names, or call a generic speaker a named role.
- Never call the result 100% accurate or client-ready unless the semantic/video gates, independent audit, and final QC pass.
