# Experimental track — preparation (实验方案 · 准备阶段)

Use this only when the user chose the experimental track for the project (`--workflow-track experimental`). Everything in SKILL.md still applies: immutable sources, the package location, preparation approval, and the first-episode gate. This file lists only what changes.

The experimental track assumes the delivered SRT is correct in both text and timing. That is the normal case for this production line. If the user says a project's SRT timing is unreliable, recommend the standard track.

## 1. Optional vocal-stem pre-separation (人声分离前置)

Short-drama audio usually has BGM under nearly every line. When the project has no `02_dry_video`, the standard track diarizes the mixed original audio. In the experimental track, create the speech-dominant media first:

1. Run the installed `vocal-separation-ensemble` wrapper with `-Stem Vocals` (MelBand Roformer) on the visual source folder (normally `01_original`). For video input it writes `<source>.mp4`, which keeps the original picture and uses the vocal stem as the audio track, so the episode suffix is preserved.
2. Write the outputs to a new sibling folder named `02_dry_video` under the same video folder. The wrapper never changes its sources. This folder is derived media, and it is the location discovery already recognizes:

   ```powershell
   & 'C:\Users\zhao-\.claude\skills\vocal-separation-ensemble\scripts\run.ps1' -Input "<video>\01_original" -OutDir "<video>\02_dry_video" -Stem Vocals
   ```

3. Separate every episode. Discovery reports `02_dry_video counterpart missing` when an episode is absent, and that blocks the package.
4. Run discovery again. It now reports `speech_dominant_audio` for each episode, and diarization and voice evidence use it automatically.

Separation is a long GPU job, so run it in the background. If separation fails or the user declines it, continue with the original audio and record the limitation.

## 2. Subtitle-guided anonymous segmentation (按字幕句切分)

`prepare_from_project.py --workflow-track experimental` passes `--segmentation subtitle` to `anonymous_speaker_draft.py diarize`. For each episode:

1. Silero VAD runs once over the episode. Each cue keeps only the voiced audio inside its own SRT interval.
2. The diarization encoder (WeSpeaker ECAPA512) embeds the voiced audio of each cue. Long cues are windowed, and the window embeddings are averaged into one cue embedding.
3. Clean cue embeddings are clustered with the same cosine agglomerative threshold as the standard pipeline (`speaker_similarity_threshold`, default 0.70). Clusters become episode-local `Speaker01`, `Speaker02`, … in order of first appearance.
4. Cues that may contain more than one voice are never forced into a cluster:

| `diarization_status` | Label | Meaning |
|---|---|---|
| `single_speaker` | `SpeakerNN` | Clean cue that is consistent with its cluster |
| `cluster_outlier_review` | `SpeakerNN` | Clustered, but far from the rest of its cluster (leave-one-out similarity below `--cluster-outlier-threshold`, default 0.45) |
| `dual_dialogue_cue` | `MULTI_SPEAKER_REVIEW` | Subtitle text uses the dash dual-dialogue form (two or more lines starting with `-`/`－`) |
| `intra_cue_change_suspected` | `MULTI_SPEAKER_REVIEW` | Voiced audio ≥ ~3 s whose first and second halves differ (similarity below `--intra-cue-change-threshold`, default 0.50) |
| `low_voiced_review` | `SpeakerNN` | VAD found under `--min-cue-voiced` seconds (default 0.30); the whole cue was embedded and matched the nearest cluster |
| `low_voiced_unresolved` | `UNRESOLVED` | As above, but no cluster was close enough |
| `no_speech_in_cue` | `UNRESOLVED` | No usable audio inside the cue |

Every status except `single_speaker` is marked `要確認` in the anonymous workbook. Only `single_speaker` rows can later join the cluster-level voice aggregate and light-tier review.

The output files, columns, label format, and `anonymous_source_issues` validation are the same as in the standard track. Each episode also gets `subtitle_segmentation_diagnostics.tsv` (voiced seconds, window count, VAD fallback, dual-dialogue flag, intra-cue similarity, cluster similarity, nearest speaker). The episode `diarization_report.json` records `segmentation_mode=subtitle_guided` and the thresholds used.

The thresholds are starting values, not calibrated constants. Tune them only through the backtest described in the automation skill's `references/experimental-track.md`, and record any change in the project notes.

## 3. What does not change

- Subtitle text, order, line breaks, and timecodes are never changed. Flagged cues are never split automatically.
- `SpeakerNN` is still episode-local and still is not an identity.
- The preparation review, user approval, and the first-episode confirmation gate are unchanged.
- Face references, frames, the internal character reference, and the optional preliminary cast overview are unchanged.
