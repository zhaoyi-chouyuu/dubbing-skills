---
name: vocal-separation-ensemble
description: "Separate BGM or vocal stems from local audio or video. Use BS-Roformer for BGM and MelBand Roformer for vocals; when the requested stem is unspecified, output BGM with BS-Roformer."
---

# Vocal Separation

Use this skill for local vocal separation in dubbing workflows. Choose the model by the requested deliverable:

- **BGM / vocal-removed output:** use **BS-Roformer**.
- **Vocal stem:** use **MelBand Roformer**.
- If the user does not specify a stem or deliverable, treat the request as BGM output and use **BS-Roformer**.

The wrapper batches inputs in one process, loads the selected model once, and emits the requested stem.

## BGM / Vocal-removed Output (Default)

Run the PowerShell wrapper for one or more audio/video files, or a folder containing them:

~~~powershell
& 'C:\Users\zhao-\.claude\skills\vocal-separation-ensemble\scripts\run.ps1' -Input "F:\project\01_original" -OutDir "F:\project\03_BGM_video"
~~~

Batch separation is a long GPU job: run it with the PowerShell tool's `run_in_background: true` and check the output when notified.

- Audio input produces <source>_BGM.wav.
- Video input produces <source>.mp4 with the original video and BS-Roformer's instrumental stem as the new audio track.
- Input folders are scanned non-recursively for common audio/video formats.
- Sources are never changed. Existing outputs are refused unless -Overwrite is supplied.
- The wrapper creates temporary WAVs only while processing and removes them after a successful run. Use -KeepWork to retain them for review.

By default, the video stream is copied unchanged for speed. If the destination folder uses a required resolution, opt in to re-encoding:

~~~powershell
& 'C:\Users\zhao-\.claude\skills\vocal-separation-ensemble\scripts\run.ps1' -Input "F:\project\01_original\episode.mp4" -OutDir "F:\project\03_BGM_video" -Scale "1440:2560"
~~~

For CUDA systems, -UseAutocast is an optional speed setting; use it only after confirming that the output quality is acceptable for the project.

## Vocal Stem Instead

To output the vocal stem rather than BGM, pass `-Stem Vocals`; this selects MelBand Roformer. This applies to either audio or video input; video output retains the source video and replaces its audio with the vocal stem.

## Advanced Dual-Model Ensemble

`C:\Users\zhao-\.claude\skills\vocal-separation-ensemble\scripts\auto_ensemble_separator.py` remains available for an explicitly requested BS-Roformer + MelBand vocal ensemble. It is for clean vocal-reference preparation, not the normal BGM-video path. Do not use it by default, and do not invoke it once per file for a batch.

## Local Runtime

- WSL distribution: Ubuntu
- Audio-separator: /mnt/e/AI_Models/miniconda_envs/cosyvoice310/bin/audio-separator
- MelBand model: /mnt/e/AI_Models/UVR/models/roformer/vocal_melband/vocals_mel_band_roformer.ckpt
- WSL FFmpeg is used to extract audio from video and mux finished BGM video.

Before delivering video output, verify that each file has both video and audio streams and that its duration is close to the source.
