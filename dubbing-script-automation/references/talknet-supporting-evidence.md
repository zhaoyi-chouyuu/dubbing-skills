# TalkNet supporting evidence

Use TalkNet as a normal supplementary evidence source during speaker-role review. It estimates which visible face track is speaking. It does not identify character names, determine an off-screen speaker, or replace dialogue logic, scene continuity, voice evidence, or whole-episode user confirmation.

## Local deployment

- Tool root: `E:\AI_Models\tools\talknet-asd`
- Official source: TaoRuijie/TalkNet-ASD, pinned in `installation.json`
- Runtime: `E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe`, CPU inference
- Runner: `scripts/talknet_evidence.py`

Run preflight once at the start of a processing session:

```powershell
& $PYTHON $TALKNET_RUNNER preflight --tool-root "$TALKNET_ROOT"
```

The deployment is ready only when the repository, Python runtime, TalkNet model, S3FD face model, FFmpeg, and imports all pass. If TalkNet is technically unavailable, record that limit and continue the normal semantic/video review; do not fabricate evidence or block an otherwise reviewable episode.

## Generate episode evidence

Run TalkNet on the same original video and SRT used for role review:

```powershell
& $PYTHON $TALKNET_RUNNER run --tool-root "$TALKNET_ROOT" \
  --video <episode.mp4> --srt <episode.srt> --episode <episode_id> \
  --out-dir <episode_work_dir/talknet_evidence>
```

The runner writes:

- `talknet_annotated.mp4`: continuous video with speaking scores;
- `talknet_frame_scores.tsv`: per-frame, per-face-track scores;
- `talknet_subtitle_candidates.tsv`: subtitle-row top and second visible tracks;
- `track_faces/`: representative face crops for track-to-role mapping;
- `talknet_evidence.json`: source/model hashes and evidence metadata.

Map tracks to approved characters using approved face references and continuous scene identity:

```powershell
& $PYTHON $TALKNET_RUNNER apply-track-map \
  --candidates <talknet_subtitle_candidates.tsv> \
  --track-map <track_role_map.tsv> \
  --out-tsv <talknet_role_evidence.tsv>
```

The map requires `track_id` and `role` columns. Leave `role` empty when identity is not reliable. Do not infer the mapping from a proposed or user-corrected speaker row. The mapped output keeps `mapped_top_track_role` for visual/reaction context, but fills `talknet_role` only for `single_positive_track`. A TalkNet score never writes or changes `final_role` automatically.

## Interpretation

- `single_positive_track` plus a reliable track-to-role map is positive supporting evidence that the mapped visible character is speaking.
- `single_positive_track` with no reliable role map shows only that an unknown visible face appears to be speaking.
- `no_visible_track` is abstention. It is common for off-screen speech, reaction shots, cutaways, back views, and obscured faces.
- `no_positive_track` may support the conclusion that the visible face is only reacting, but it cannot identify the actual speaker.
- `multiple_positive_tracks` requires review for overlap, tracking errors, rapid cuts, or audio-video drift.

Short-drama editing often shows the listener while another character speaks. In these rows, TalkNet may help reject the visible reaction face but cannot name the off-screen speaker. Use dialogue ownership, turn-taking, scene continuity, and authorized voice evidence to resolve the role.

## Integration into role review

Keep TalkNet evidence under `03_script/02_script_work/episodes/<episode>/talknet_evidence`. Record its status, mapped candidate, and any abstention or conflict reason in the normal `review_all.tsv` evidence fields.

Agreement may strengthen a decision already supported by context. A disagreement with dialogue logic, scene continuity, or reliable voice evidence returns the complete semantic unit to review. Do not average conflicting evidence into a forced role, do not create a separate TalkNet branch, and do not open a TalkNet-only user-confirmation gate.

TalkNet artifacts are internal working evidence and never belong in `03_final_delivery`.
