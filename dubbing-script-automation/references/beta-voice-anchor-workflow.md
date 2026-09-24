# beta1.5 voice-anchor workflow

This is the only voice workflow used by `dubbing-script-automation`. Voice identity is supporting evidence inside a human-confirmed beta1.5 process; it never assigns or changes a speaker automatically.

Attempt all three independent speaker-identity models. Evaluate QC per role. Two matching eligible votes out of the three attempted models are sufficient for that role; the third model may disagree or abstain after documented role-level QC. WavLM is not part of this workflow:

- Python: `E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe`
- CAM++ model: `E:\AI_Models\pyannote\wespeaker-voxceleb-campplus`
- ECAPA512 model: `E:\AI_Models\pyannote\wespeaker-voxceleb-ecapa-tdnn512`
- ResNet34 model: `E:\AI_Models\pyannote\wespeaker-voxceleb-resnet34-LM`
- FFmpeg: `E:\AI_Models\RVC\RVC20240604Nvidia50x0\ffmpeg.exe`
- voice tool: `C:\Users\zhao-\.claude\skills\dubbing-script-automation\scripts\voice_evidence.py`
- ensemble tool: `C:\Users\zhao-\.claude\skills\dubbing-script-automation\scripts\voice_ensemble.py`

Run a real preflight for all three models before scoring. Missing any model directory or a failed runtime preflight blocks the run. Two-of-three consensus is permitted only after all three model pipelines were attempted; an unavailable model is not an abstaining vote. Do not silently reduce the ensemble to the installed subset and do not replace a missing model with WavLM.

## Local model backends

`voice_evidence.py --model-kind` selects `resnet34`, `campplus`, or `ecapa512`; a path alone cannot select an encoder. ResNet34 uses the installed pyannote model. CAM++ and ECAPA512 use official WeSpeaker ONNX exports with an 80-bin Kaldi FBank frontend and local weights. The deployed CAM++ export produces 512-dimensional embeddings. The deployed ECAPA512 export is `ECAPA_TDNN_GLOB_c512` and produces 192-dimensional embeddings; an ECAPA1024 model is not interchangeable.

Each CAM++ / ECAPA512 model directory needs its matching ONNX export and `backend_config.json`:

```json
{"backend":"wespeaker_onnx","model_kind":"campplus","onnx_model":"voxceleb_CAM++.onnx","embedding_size":512}
```

```json
{"backend":"wespeaker_onnx","model_kind":"ecapa512","onnx_model":"voxceleb_ECAPA512.onnx","embedding_size":192}
```

Missing `onnxruntime`, incompatible exports, dimension mismatches, and absent configuration are hard stops; no download or model substitution occurs during a production run. The files were deployed from the official WeSpeaker pretrained model repositories. See the [official pretrained model table](https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md) and [WeSpeaker repository](https://github.com/wenet-e2e/wespeaker).

For each model, run `preflight --model-kind <kind> --model <local_directory>` before enrollment. Use the same `--model-kind` and model directory when building and scoring that model's gallery. The gallery is bound to the model fingerprint and cannot be reused with another encoder.

## Mandatory first-episode gate

Complete every row of episode 1 first, then pause once for the user to check the whole character-to-dialogue mapping. Do not extract any episode-derived voice anchor before the user confirms that mapping. Once the mapping is confirmed, directly extract eligible clean intervals under the user's standing workflow authorization; do not ask for a separate per-project extraction authorization unless the user explicitly opts out for that project. The confirmed mapping is the identity authority; pictures, TalkNet, clustering, and nearest-voice results cannot name an enrollment clip.

Create a UTF-8 TSV manifest with:

```text
role	episode	media_path	start	end	verified	overlap	mixed_speaker	music_dominant	processing_domain
```

Use exact confirmed role names and source-media intervals. Set `verified=true` only for user-confirmed, continuous, single-speaker intervals. Reject mixed speakers, overlap, dominant music/effects, phone processing, severe distortion, uncertain boundaries, and clips outside the duration gate. Keep enrollment anchors and held-out consistency clips disjoint.

A practical beta1.5 gate is at least three accepted clips and eight accepted seconds per role, with each clip between two and twelve seconds. Apply it separately to each role in each model. A failed or confusable role profile is excluded only from that model's candidate set; it does not invalidate other QC-usable roles in the same model. If fewer than two models have a usable profile for a character, record `no_reliable_gallery`; never weaken the gate or borrow another role's voice.

Attempt three separate galleries from the exact same confirmed manifest, one per model. Each report must list `usable_roles` and role-level rejection reasons. Score the exact same reviewed acoustic-turn manifest against all scoreable role subsets. If a model has fewer than two QC-usable roles and therefore cannot score, preserve its failed report as the third-model attempt. Never mix embeddings from different models in one gallery.

Build each gallery after mapping confirmation, for example:

```powershell
& $PYTHON $VOICE_TOOL build-gallery --model-kind campplus --model "$VOICE_MODEL_CAMPLUS" \
  --manifest <confirmed_intervals.tsv> --out-gallery <campplus_gallery.npz> \
  --audit-tsv <campplus_enrollment_audit.tsv> --report <campplus_gallery.report.json> --beta
& $PYTHON $VOICE_TOOL score --model-kind campplus --model "$VOICE_MODEL_CAMPLUS" \
  --manifest <reviewed_acoustic_turns.tsv> --gallery <campplus_gallery.npz> \
  --out-tsv <campplus_voice_evidence.tsv>
```

Prepare the shared scoring manifest before character assignment with:

```powershell
& $PYTHON $VOICE_TOOL prepare-manifest --anonymous-turns \
  --labels <reviewed_anonymous_rows.tsv> --media <speech_media> --episode <episode_id> \
  --gallery <campplus_gallery.npz> --out-tsv <reviewed_acoustic_turns.tsv>
```

The input preserves source rows and anonymous labels, with `source_index`, subtitle `start`/`end`, `semantic_unit`, `acoustic_turn_id`, reviewed `acoustic_turn_start`/`acoustic_turn_end`, `acoustic_turn_status=single_speaker`, and `acoustic_reviewed_by` on every row. Use one consistent reviewed audio interval per turn; this is acoustic-boundary confirmation, not named-role confirmation. The command leaves `expected_role` empty and rejects unreviewed/mixed turns. Without `--anonymous-turns`, the command is the legacy post-role-review helper and is not the later-episode first-pass route.

Repeat with `ecapa512` / `$VOICE_MODEL_ECAPA512` and `resnet34` / `$VOICE_MODEL_RESNET34`, using separate gallery/output paths. Defaults match the first-episode gate: one confirmed episode, at least three clips and eight seconds per role. Every enrollment row must contain its actual episode ID. A gallery may score when it contains at least two QC-usable role profiles, even if other roles in that model failed. A model with fewer than two usable roles remains unscoreable and may be documented as the failed third-model attempt only after successful preflight and gallery construction. Regenerate legacy galleries lacking model provenance or role-level usability metadata.

Merge the resulting evidence tables:

```powershell
& $PYTHON "$SKILL_DIR\scripts\voice_ensemble.py" \
  --evidence campplus=<campplus_voice_evidence.tsv> \
  --evidence ecapa512=<ecapa512_voice_evidence.tsv> \
  --evidence resnet34=<resnet34_voice_evidence.tsv> \
  --out-tsv <three_model_voice_ensemble.tsv>
```

The merger accepts either three distinct evidence files, or two distinct evidence files plus `--failed-model <kind>=<gallery.report.json>` when the attempted third model has no scoreable role subset. It checks declared model kinds, independent fingerprints, identical acoustic-turn keys, and that the failed report declares `gallery_ready=false` without `role_scoring_ready=true`; duplicated or relabeled evidence is rejected. Each usable model's role set, second candidate, quality fields, rejection reason and gallery ID remain in `model_candidates_json`, and the failed report is retained in `failed_models_json`.

`consensus_3_of_3` and `consensus_2_of_3` are strong supporting evidence. Two-of-three includes the case where the third attempted model has no scoreable role subset. Two matching eligible votes are sufficient even if the third eligible model votes differently; fewer than two matching eligible votes is an abstention. `model_disagreement` and `insufficient_models` are abstentions. The ensemble table never writes or replaces a final role.

Internal reports must state:

```text
workflow_stage=beta
workflow_version=beta1.5
voice_evidence_authority=supporting
automatic_identity_assignment=false
```

Do not score episode 1 against a gallery enrolled from episode 1.

## Later episodes

Start from the preparation-stage episode-local `SpeakerNN` draft. Score only a reviewed continuous single-speaker acoustic turn. Preserve each model's top and second candidates, similarity, margin, quality status, gallery ID, plus the merged ensemble status and any rejection or conflict.

- Confirmed three-of-three voice identity or documented per-role two-of-three consensus is the first identity signal; dialogue logic is the required independent cross-check.
- Use TalkNet only when voice and logic remain unresolved and a reliably mapped speaking face is visible. Pictures and TalkNet otherwise support presence, lip activity, reaction/off-screen classification, entrances/exits, phone/flashback context, and scene boundaries.
- Weak, close, short, mixed, overlapping, unavailable, or conflicting voice evidence never fills or changes a role.
- When a new named role appears or identity remains unresolved, finish every row of that episode and pause once for whole-episode confirmation before starting the next episode.
- Never claim leakage safety for a same-episode gallery, use `--require-voice-audit` to alter roles, or call voice scoring production verification.
