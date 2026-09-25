---
name: dubbing-script-preparation
description: Discover, prepare, and validate Japanese short-drama dubbing materials for the beta1.6 workflow (standard track, or the opt-in experimental track with subtitle-guided speaker segmentation) from semantically organized project folders whose names and contents may vary. It finds video, subtitle, script, casting, and related source areas by meaning and evidence, creates episode-local anonymous Speaker drafts, and optionally delivers a Japanese preliminary cast overview before speaker-role review.
metadata:
  version: "beta1.6"
---

# Dubbing Script Preparation — beta1.6

Use this skill before character attribution. It discovers source material in place, generates review evidence, creates the internal character-reference draft, and diarizes each episode into episode-local `Speaker01`, `Speaker02`, and so on. It does not require the user to reorganize files or assign final character names. It normally creates no client-facing deliverable, except for the optional Japanese preliminary cast overview described below when the user asks for it.

Preparation feeds one beta1.6 workflow only. Anonymous speaker labels are acoustic clusters, not people: `Speaker01` in episode 1 has no guaranteed identity relationship to `Speaker01` in episode 2. Preparation may segment speech before confirmation, but it does not create named voice identities before the user confirms the complete first-episode mapping and authorizes anchor extraction.

## Workflow track selection (标准方案 / 实验方案)

beta1.6 has two tracks. Every rule in this file applies to both unless the experimental reference says otherwise.

- **standard (标准方案)** — the unchanged beta1.5 behavior: sliding-window anonymous diarization, per-turn voice scoring, and full row-level review evidence.
- **experimental (实验方案)** — opt-in: optional vocal-stem pre-separation, subtitle-guided anonymous segmentation that treats each trusted SRT cue as one unit, and downstream cluster-level voice scoring, multi-exemplar galleries, and tiered review evidence. Read [references/experimental-track.md](references/experimental-track.md) before running it.

At the start of every new project, before discovery writes anything, ask the user which track to use with one AskUserQuestion prompt, standard first. Use standard when the user does not choose. Pass the choice as `--workflow-track standard|experimental`. It is recorded as `workflow_track` in `handoff_manifest.json`, and the automation skill reads it from there. Keep one track for the whole project. Switching tracks means rebuilding the preparation package and getting it approved again.

## Required project contract

Accept either the project root or its source-production folder. Infer directory purpose from the combination of name semantics, parent/child position, file extensions, episode naming, workbook headers, and actual contents. Known hints include `feature`, `finish`, `video`, `subtitle`, `script`, `casting`, `sound`, `vocal`, and `fix`, but they are examples rather than a whitelist. Numeric prefixes, suffix notes, and additional project-specific folders are allowed. Inventory unknown folders and inspect them for relevant material instead of ignoring them merely because their names are new. If several folders remain equally plausible after semantic and content inspection, stop and request an explicit choice rather than guessing.

`feature` normally identifies the source-production area and `finish` normally identifies delivery material. Never use `finish` as preparation source. `casting` commonly contains cast/role workbooks or character references and must be searched alongside the script area when its contents support that meaning. `sound`, `vocal`, `fix`, and newly encountered categories may be selected when their actual contents and the current task make them relevant; their names alone do not authorize substitution for the original video, subtitle, or approved role source.

The canonical layout is:

```text
<feature_root>/
├─ 01_video/                    # one or more video variants
├─ 02_subtitle/                 # Japanese SRT files
└─ 03_script/
   ├─ <project>.xlsx            # client role/settings workbook; unverified by default
   └─ 画面字.xlsx               # screen-text workbook
```

Treat every selected source video, subtitle, casting/role workbook, and screen-text workbook as immutable source material. Never modify, rename, or relocate them. Ignore `._*`, hidden files, existing generated packages, script-work outputs, final-delivery outputs, and prior delivery folders during discovery.

Discover and reference source material directly in the inferred source-production area. Prefer semantic `video`, `subtitle`, `script`, and `casting` locations, but inspect other project folders when file type and content indicate they hold required material. Do not create or require a separate `00_source_portal`. If the user later supplies another video, subtitle, workbook, image, or explicitly selected audio reference, keep it in the appropriate existing source directory or accept its explicit current path; do not ask the user to duplicate it into the preparation package.

Do not ask the user to create an episode manifest, role subfolders, character screenshots, frame directories, review queues, voice folders, QC reports, or handoff files. Generate those from the supplied source. Missing optional material becomes a known limitation instead of a repeated request.

## Canonical output location

Keep every retained preparation and script artifact under the inferred source-production folder's script directory. The preparation package must be a direct child of that script directory. Use this canonical naming when creating a new standard project:

```text
03_script/
├─ <project>.xlsx               # user source; unchanged
├─ 画面字.xlsx                  # user source; unchanged
├─ <project_id>_主要登場人物一覧_前期参考版.xlsx  # optional early production handoff
├─ 01_preparation/               # generated by this skill
├─ 02_script_work/               # downstream row review and intermediate script work
└─ 03_final_delivery/            # downstream three Japanese Excel deliverables only
```

Do not write retained preparation or formal script outputs beside the project, under video/subtitle folders, in `finish`, or outside the current script directory. A temporary-named smoke-test package may be created as a direct child of the script directory and discarded after validation; it is not a deliverable.

Treat the package location as the path anchor: `<package>/..` is the current script root and `<package>/../..` is the current feature root. Record internal artifacts relative to the package. Record external source locators as feature-relative path, basename, episode identity, and SHA-256; an absolute path is only a human-readable legacy hint. A project or ancestor-folder rename must not invalidate evidence when the uniquely relocated file has the same hash. Zero matches, a hash mismatch, or multiple fallback matches is a blocker.

The generated `01_preparation` package begins with `01_video_frames` and contains only derived evidence, review work, character data, voice evidence, QC/approval, and handoff metadata. It does not contain a source-material drop zone.

## Three different character workbooks

Keep the internal role-identification workbook, the optional production-facing preliminary cast overview, and the final client-facing character-settings workbook separate.

### Internal character reference

The preparation package owns an internal working file such as `internal_character_reference.xlsx`. It is evidence for speaker attribution and may contain stable IDs, source names, approved names, aliases, relationships, first appearances, episode/timecode evidence, portrait candidates, confidence, conflicts, and review status. It may change during transcription and is never a formal delivery.

Treat the supplied client role workbook as unverified evidence by default. Preserve it unchanged and build the internal draft from the whole-series subtitles, video continuity, screen text, embedded images, and dialogue/relationship logic. If the user explicitly states that the client workbook is correct and authoritative, use it as the starting baseline but still check structural integrity and cross-episode consistency. Never promote an unverified client statement or an inferred relationship directly to approved identity.

Use `confirmed`, `candidate`, and `unknown` states internally. Every non-obvious identity or relationship decision must retain episode/timecode evidence and a concise conflict note when evidence disagrees. Only the user-approved internal baseline may be handed to the script automation skill.

Keep these logical layers under `03_script/01_preparation/03_character_data/` when the package layout supports them:

```text
03_character_data/
├─ 00_source_role_workbook/
├─ 01_internal_character_draft/
├─ 02_face_gallery/
├─ 03_relationship_evidence/
├─ 04_review/
└─ 05_approved_internal_baseline/
```

### Optional production-facing preliminary cast overview

Create `<project_id>_主要登場人物一覧_前期参考版.xlsx` only when the user explicitly requests an early role-count or casting overview for the production side. It is optional, is not a preparation-readiness requirement, and must not be generated by default.

When requested as part of a preparation run, make it the first handoff: perform enough whole-series discovery and rapid character review to avoid a first-episode-only count, deliver the Japanese workbook before the slower full evidence extraction, diarization, and preparation approval work, and then continue the package unless the user asks to pause. Do not wait for the final character reconciliation, preparation approval, or downstream script completion. If the user requests only this workbook, it may be produced without building the full preparation package.

Build it with the same evidence discipline as the internal reference, but present only the production-facing summary needed to estimate cast size. Clearly label every uncertain item and keep it provisional. It is neither identity authority nor an approved internal baseline. Read [references/early-cast-overview.md](references/early-cast-overview.md) whenever this optional workbook is requested.

### Client-facing character settings

Do not create the formal client-facing `登場人物設定表.xlsx` during preparation. The downstream automation skill creates it after full-series role reconciliation, because its relationships, character descriptions, and voice direction must agree with the completed script. It is one of three independent Japanese Excel deliverables; it is not the internal reference workbook.

## Local runtime

Use WSL2 Ubuntu and the RTX GPU for anonymous diarization and voice-model work. Keep lightweight discovery, workbook, JSON, and subtitle operations on Windows when they do not invoke acoustic models. The PowerShell launcher translates Windows drive paths to `/mnt/<drive>/...`, keeps temporary model work under WSL `/tmp`, and invokes the installed skill files through their mounted Windows paths:

```powershell
$PYTHON = 'E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe'
$SKILLS_ROOT = 'C:\Users\zhao-\.claude\skills'
$DISCOVER = "$SKILLS_ROOT\dubbing-script-preparation\scripts\prepare_from_project.py"
$PREPARE = "$SKILLS_ROOT\dubbing-script-preparation\scripts\prepare_package.py"
$ANONYMOUS_DRAFT = "$SKILLS_ROOT\dubbing-script-preparation\scripts\anonymous_speaker_draft.py"
$WSL_GPU = "$SKILLS_ROOT\dubbing-script-preparation\scripts\run_wsl_gpu.ps1"
$AUTOMATION = "$SKILLS_ROOT\dubbing-script-automation"
$VOICE_MODEL_CAMPLUS = 'E:\AI_Models\pyannote\wespeaker-voxceleb-campplus'
$VOICE_MODEL_ECAPA512 = 'E:\AI_Models\pyannote\wespeaker-voxceleb-ecapa-tdnn512'
$VOICE_MODEL_RESNET34 = 'E:\AI_Models\pyannote\wespeaker-voxceleb-resnet34-LM'
$DIARIZATION_PIPELINE = 'E:\AI_Models\pyannote\wespeaker-anonymous-diarization'
$FFMPEG = '/usr/bin/ffmpeg'
$FFMPEG_WINDOWS = 'E:\AI_Models\RVC\RVC20240604Nvidia50x0\ffmpeg.exe'
```

The WSL runtime is `/home/zhaoyi/miniconda/envs/stable-ai/bin/python`; `--device auto` selects the RTX 4070 and falls back to CPU only when CUDA is unavailable. Models stay on `E:` and are reused through `/mnt/e`. Do not copy them into WSL. Do not use the Microsoft Store `python3` launcher or the general Python 3.14 installation for acoustic work. The Windows Python above remains the direct fallback. Run preflight before writing output.

Claude Code runs each PowerShell tool call in a fresh session, so variables do not persist between calls. Prepend the variable assignments each command needs to that same call. Run diarization, package builds, and other long GPU jobs with `run_in_background` (or a long `timeout`), and check their output when notified instead of polling.

## Discovery and source selection

Start with a read-only discovery pass:

```bash
& $PYTHON $DISCOVER --project-root <project_or_feature_root> --discover-only
```

The discovery pass must:

- inventory every visible top-level source folder, its name tokens, bounded file-type signals, inferred roles, and still-unknown status; known role names are hints rather than an allowlist;
- map every video and SRT by the final four-digit episode suffix;
- stop on duplicate or unpaired episode files instead of guessing;
- identify the client project workbook by filename plus the expected role headers (`ID`, `性别`, `年龄`, `身份`, `人物描述`, `声线描述`) without treating it as identity authority;
- exclude existing preliminary cast-overview workbooks such as `*主要登場人物一覧*` and `*主役登場人物*` from source-workbook candidates; they are generated production handoffs, not role-identity authority;
- require exactly one `画面字.xlsx`, unless an explicit override is supplied;
- prefer `01_original` for visual frames and continuous-video review;
- use the matching `02_dry_video` episode as `speech_dominant_audio` when available; this is model-separated media, not assumed original dry voice;
- search semantically identified `casting` locations and other workbook-bearing source folders for the client role workbook while retaining the script directory as the preparation-package anchor;
- record all selected source paths, workbook candidates, directory-role inferences, and unresolved directory ambiguities in the discovery report.

If the project workbook has no explicit character-name column, generate a normalized internal draft catalog with stable labels such as `角色ID_14543`. Preserve the ID, gender, age, identity, character description, and voice description as evidence. Do not convert names mentioned inside a description into the row's identity. Replace a stable ID label with a named role only through the normal semantic/video review and user-approval process.

Other video variants remain available as fallbacks but are not silently substituted. If the project has an unusual layout, pass explicit `--roles` or `--screen-text-xlsx`; do not move the source to satisfy the tool.

For the first real use of a new layout or naming convention, run a one-episode package smoke test in a temporary-named direct child of the script directory and verify video/SRT pairing, workbook parsing, frame extraction, review-queue generation, and `handoff_manifest.json` before creating the canonical full package.

Build the canonical package only after discovery is unambiguous:

```bash
& $WSL_GPU $DISCOVER --project-root <project_or_feature_root> \
  --anonymous-diarization-pipeline "$DIARIZATION_PIPELINE" \
  --identity-model "campplus=$VOICE_MODEL_CAMPLUS" \
  --identity-model "ecapa512=$VOICE_MODEL_ECAPA512" \
  --identity-model "resnet34=$VOICE_MODEL_RESNET34" \
  --anonymous-device auto --workflow-track standard
```

Use `--workflow-track experimental` for the experimental track. This command defaults to `03_script/01_preparation` and creates `03_script/02_script_work` plus `03_script/03_final_delivery`. Use `--overwrite` only when replacing that exact generated package is intended; never overwrite the two user workbooks.

## Generated preparation package

Generate and validate:

- the episode manifest and complete source inventory;
- immutable SRT copies used by the review queue;
- three in-cue video frames per subtitle row and `frames_manifest.json`;
- the internal character-reference draft, role metadata, relationship graph, and any images embedded in the project workbook;
- when explicitly requested, the Japanese preliminary cast overview as the first separately deliverable workbook;
- the copied `画面字.xlsx` inside generated character evidence;
- per-episode review queues and editable label TSVs;
- a mandatory per-episode anonymous speaker turn table, subtitle-to-Speaker map, internal TSV, and reviewable Excel draft;
- one merged whole-series anonymous Speaker workbook under `04_voice_evidence/01_anonymous_diarization/`;
- optional speech-dominant media references and voice-enrollment templates;
- per-episode QC, package layout, preparation review, approval record, and `handoff_manifest.json`.

A generated or visible face is supporting evidence only; it never proves who is speaking. Generated candidate character screenshots require role confirmation before they can act as identity anchors.

Reference source videos and optional dry/speech-dominant media in place by feature-relative locator plus SHA-256, retaining an absolute path only for legacy readability. Do not duplicate full video files inside the preparation package. Frame manifests must also carry manifest-relative frame paths. Copies of SRTs, workbooks, extracted frames, manifests, and review evidence are permitted because they are small, reviewable preparation artifacts.

## Anonymous speaker draft

Anonymous diarization is a required preparation artifact. Use a local diarization pipeline to find speaker turns, then align those turns to the trusted SRT without changing any subtitle text, index, line break, or timecode. Write:

```text
04_voice_evidence/01_anonymous_diarization/
├─ 0001/
│  ├─ speaker_turns.tsv
│  ├─ subtitle_speaker_map.tsv
│  ├─ anonymous_script.tsv
│  ├─ anonymous_speaker_script.xlsx
│  └─ diarization_report.json
├─ ...
├─ whole_series_anonymous_script.tsv
└─ whole_series_anonymous_speaker_script.xlsx
```

Keep labels episode-local and ordered by first acoustic appearance. Do not attach character names during preparation. When one subtitle cue overlaps multiple clusters and no speaker owns at least 70% of the detected speech overlap, write `MULTI_SPEAKER_REVIEW`; when no turn overlaps, write `UNRESOLVED`. Never split or rewrite subtitle dialogue merely to make it fit diarization.

In the experimental track, the same output files are built from one embedding per trusted SRT cue instead of sliding windows. The track adds its own review statuses and a `subtitle_segmentation_diagnostics.tsv`. The column contract, labels, and validation are unchanged. See [references/experimental-track.md](references/experimental-track.md).

The diarization backend and the identity encoders have separate jobs. Diarization uses Silero VAD, the official WeSpeaker ECAPA512 ONNX encoder, and local cosine agglomerative clustering to create reviewable anonymous turns. The downstream identity ensemble uses CAM++, ECAPA512, and ResNet34 only after confirmed role anchors exist. WavLM is not part of this workflow. Before a real run, load the local pipeline and all three identity runtimes through preflight; a non-empty directory alone is not readiness. Both build entry points run this gate before writing output. Read-only discovery remains usable when models are unavailable. Run it separately to inspect blockers:

```bash
& $WSL_GPU $ANONYMOUS_DRAFT preflight --pipeline "$DIARIZATION_PIPELINE" \
  --voice-model "campplus=$VOICE_MODEL_CAMPLUS" \
  --voice-model "ecapa512=$VOICE_MODEL_ECAPA512" \
  --voice-model "resnet34=$VOICE_MODEL_RESNET34" \
  --main-skill-dir "$AUTOMATION" --ffmpeg "$FFMPEG" --device auto
```

Stop if any required resource fails. Never download models implicitly or describe a missing model as installed. The three named identity models must use distinct local resources. Local pipeline configurations must reference local model resources, not a remote model identifier.

The manual compatibility entry point remains available for unusual projects:

```bash
& $WSL_GPU $PREPARE \
  --episode-manifest <episode_manifest.tsv> \
  --roles <internal_character_reference_draft.xlsx_or_tsv> \
  --source-role-workbook <project.xlsx> \
  --screen-text-xlsx <画面字.xlsx> \
  --out-dir <feature_root/03_script/01_preparation> \
  --anonymous-diarization-pipeline "$DIARIZATION_PIPELINE" \
  --identity-model "campplus=$VOICE_MODEL_CAMPLUS" \
  --identity-model "ecapa512=$VOICE_MODEL_ECAPA512" \
  --identity-model "resnet34=$VOICE_MODEL_RESNET34" \
  --main-skill-dir "$AUTOMATION" --ffmpeg "$FFMPEG" \
  --anonymous-device auto --voice-device auto
```

## beta1.6 first-episode voice anchors

The preparation approval gate and the downstream first-episode speaker confirmation are separate: preparation approval accepts the evidence and internal character baseline, not a character-to-dialogue mapping. The downstream script workflow always completes the first episode and pauses once for whole-episode speaker confirmation. After the user confirms every assignment and authorizes extraction, mark internal gallery reports and checkpoints:

Read [references/beta-voice-anchor-workflow.md](references/beta-voice-anchor-workflow.md) for the preparation handoff contract; the detailed extraction and scoring rules live in the automation skill.

```text
workflow_stage=beta
workflow_version=beta1.6
voice_evidence_authority=supporting
automatic_identity_assignment=false
```

These flags describe the internal workflow, not the eventual client-facing Excel files. Client delivery remains blocked until every identity gate, semantic/video review, independent audit, and final QC pass.

Preserve the confirmed first episode and its hashes as the identity baseline. Enroll only user-confirmed named-role intervals that are continuous and single-speaker. Reject overlap, mixed speakers, dominant music/effects, phone processing, severe distortion, uncertain identity, and clips outside the quality gate. A practical starting gate is at least three accepted clips and eight accepted seconds per role, with each clip between two and twelve seconds. Never score the first episode against a gallery built from that episode.

Treat rejected or insufficient roles as `no_reliable_gallery`. Do not invent identities, borrow another role's voice, or weaken thresholds for coverage.

## Review and approval gate

If semantic/video review changes the internal character-reference draft, refresh its context before reviewing again:

```bash
& $PYTHON $PREPARE --refresh-internal-reference <feature_root/03_script/01_preparation>
```

This updates its hash, relationship graph, and review queues, while keeping source SRTs, anonymous drafts, and existing labels. It invalidates the previous review/approval; create and finalize a new preparation review, then record the user's renewed approval of the revised baseline. `--refresh-package` is only a legacy gallery operation and does not perform this update.

Verify UTF-8 JSON, current source hashes, complete episode mapping, every required frame, every supplied workbook, and the reviewed internal character baseline before approval. Then create the preparation review and wait for explicit user approval:

```bash
& $PYTHON "$AUTOMATION\scripts\dubbing_tool.py" create-preparation-review-draft --package <feature_root/03_script/01_preparation>
& $PYTHON "$AUTOMATION\scripts\dubbing_tool.py" finalize-preparation-review --package <feature_root/03_script/01_preparation> --review-json <completed_review.json> --reviewed-by claude-code
& $PYTHON "$AUTOMATION\scripts\dubbing_tool.py" record-preparation-approval --package <feature_root/03_script/01_preparation> --decision approved --approved-by user --note <explicit_user_decision>
```

For current schema packages, approval creates a hash-bound snapshot at `03_character_data/05_approved_internal_baseline/approved_internal_character_reference.*`. The automation skill must consume that approved snapshot, never the draft path or the client source workbook.

Any source or derived-artifact content change invalidates the prior review and approval. A path-only project rename does not invalidate approval when the complete reviewed inventory is uniquely relocated inside the current feature root and every recorded hash still matches. Return the package path, readiness, blockers, known limits, and this continuation command:

```bash
& $PYTHON "$AUTOMATION\scripts\dubbing_tool.py" continue-from-preparation --package <feature_root/03_script/01_preparation> --require-ready
```

Edits to the optional preliminary cast overview do not by themselves invalidate preparation approval. If an edit reveals a real identity, relationship, or evidence change, update the internal character reference and follow the normal refresh and renewed-approval process.

## Boundaries

- Never modify source videos, SRT dialogue, SRT ordering, line breaks, timings, or the two supplied workbooks.
- Never treat the client role workbook as authoritative unless the user explicitly says it is correct.
- Never substitute the internal character reference for the client-facing Japanese character-settings deliverable.
- Never create the optional preliminary cast overview unless the user asks for it, and never treat it as the formal `登場人物設定表.xlsx` or as identity approval.
- Never delay the requested preliminary overview until the whole preparation package is complete; deliver it first once the whole-series rapid review is sufficient for an honest cast estimate.
- Never silently omit or choose between duplicate videos, SRTs, or role workbooks.
- Never treat a visible face or voice candidate as automatic speaker proof.
- Never treat an episode-local `SpeakerNN` cluster as a character identity or carry its number into another episode as identity evidence.
- Never approve a current-schema preparation package without the anonymous speaker draft.
- Never enroll episode-derived identities without explicit first-episode confirmation and extraction authorization.
- Never create user approval or describe the beta1.6 workflow as fully validated production automation.
