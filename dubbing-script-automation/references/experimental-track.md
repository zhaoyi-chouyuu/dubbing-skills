# Experimental track — script automation (实验方案 · 台本阶段)

## 概览

| 改进项 | 状态 | 作用 |
|---|---|---|
| E1 按字幕句切分说话人 | 已实装（准备阶段） | 以正确的 SRT 为切分单位，避免 1.5 秒滑窗混入两人声音 |
| E2 按簇比对声纹 | 已实装 | 一集中同一 Speaker 的所有句子合并后与声纹库比对，更稳，复查量更少 |
| E3 多样本声纹库 | 已实装 | 每个角色保留全部参考片段，取最接近的一段，适应哭喊、耳语等状态 |
| E4 证据分级 + 抽样盲审 | 已实装 | 簇级判断明确的行只留简短记录，详细证据和盲审集中在有问题的行 |
| E5 人声分离前置 | 已接入（使用现有人声分离 skill） | 去掉 BGM 后再做说话人分离和声纹 |
| E6–E10 | 候选，尚未实装 | 见第 7 节 |

Use this file only when `handoff_manifest.json` has `workflow_track=experimental`. Every rule in SKILL.md still applies unless this file explicitly replaces it. The goal is the same accuracy with far less row-by-row writing. On an 80–100 minute title, most rows belong to a few stable clusters per episode. Reviewing each cluster once, instead of every row separately, is where the time is saved.

## 1. First episode

Episode 1 runs as in the standard track, and every row is full-tier. There is no gallery yet, so no voice consensus exists. After the user confirms the episode, extract anchors exactly as in the standard track, then build all three galleries in exemplar mode:

```powershell
& $WSL_GPU $VOICE_TOOL build-gallery --model-kind campplus --model "$VOICE_MODEL_CAMPLUS" `
  --manifest <confirmed_intervals.tsv> --out-gallery <campplus_gallery.npz> `
  --audit-tsv <campplus_enrollment_audit.tsv> --report <campplus_gallery.report.json> --beta `
  --scoring-mode exemplar_max
```

Repeat for `ecapa512` and `resnet34`. In exemplar mode the gallery stores every accepted enrollment clip. A turn's score for a role is its similarity to that role's nearest clip. Thresholds are calibrated leave-one-out in the same way, so the role-level QC and consensus rules are unchanged. Rebuild the galleries in the same mode whenever a later episode gate adds anchors.

## 2. Later episodes, step by step

For each episode after the first, with `<ep>` = the four-digit episode ID:

1. **Cluster scoring, per model.** Use the preparation `anonymous_script.tsv`, which was built with subtitle-guided segmentation, and the same speech media used for diarization:

   ```powershell
   & $WSL_GPU $VOICE_TOOL score-clusters --model-kind campplus --model "$VOICE_MODEL_CAMPLUS" `
     --gallery <campplus_gallery.npz> --anonymous-script <prep>/04_voice_evidence/01_anonymous_diarization/<ep>/anonymous_script.tsv `
     --media <speech_media> --episode <ep> `
     --out-tsv <work>/campplus_clusters.tsv --out-rows-tsv <work>/campplus_cluster_rows.tsv
   ```

   Only `single_speaker` rows enter a cluster aggregate. The per-cue minimum is `--min-cue-duration` (0.5 s) and the per-cluster minimum is `--min-cluster-seconds` (3.0 s). A cluster is `eligible` only when the duration-weighted aggregate passes the role's similarity and margin gates, and at least `--min-row-agreement` (0.6) of its scored rows prefer the same role on their own. A row is flagged `row_outlier=true` when its own voice strongly prefers another role, or when it sits far from the rest of its cluster (`--row-outlier-threshold`, 0.40). Rows that were too short, low quality, or not scored get `row_outlier=unknown`.

2. **Three-model merge.** Pass the three `*_clusters.tsv` files to `voice_ensemble.py`, or two plus `--failed-model`, exactly as in the standard track. Keys are `cluster:<ep>:SpeakerNN`, so the standard consensus rules apply per cluster.

3. **Cluster decisions (Claude's review, once per cluster).** For every `SpeakerNN` in the episode, read all of its rows in context, including at least two rows before and after each run, and check the ensemble candidate against dialogue logic: vocatives, self-reference, relationships, question-answer structure, and scene continuity. Write `<work>/cluster_decisions.tsv`:

   | column | content |
   |---|---|
   | `cluster_decision_id` | `<ep>:SpeakerNN` |
   | `episode` | `<ep>` |
   | `anonymous_speaker` | `SpeakerNN` |
   | `ensemble_status`, `ensemble_candidate` | copied from the merged cluster evidence |
   | `decision` | `accept` only when voice consensus and dialogue logic agree on a named role; otherwise `reject` |
   | `decided_role` | the approved internal role name (empty when rejected) |
   | `semantic_crosscheck` | the concrete logic evidence for the whole cluster, e.g. the self-introduction row, the vocatives, who answers whom |
   | `reviewed_by` | `claude-code` |

   Reject a cluster when logic conflicts with voice, when the cluster seems to hold two people, when the candidate is a generic voice, or when the role first speaks in this episode. A reject is not a failure: its rows simply go to full review.

4. **Expand.** This writes light-tier rows for accepted clusters and lists every other row in a pending TSV with a reason:

   ```powershell
   & $PYTHON $TOOL expand-cluster-decisions --labels <codex_draft.tsv> --anonymous-script <anonymous_script.tsv> `
     --ensemble-tsv <cluster_ensemble.tsv> --cluster-decisions <cluster_decisions.tsv> --roles <approved_roles> `
     --cluster-rows-tsv <campplus_cluster_rows.tsv> --cluster-rows-tsv <ecapa512_cluster_rows.tsv> --cluster-rows-tsv <resnet34_cluster_rows.tsv> `
     --out-review-tsv <work>/light_rows.tsv --out-pending-tsv <work>/pending_rows.tsv
   ```

   A row becomes light only if all of the following hold: its cue is `single_speaker`; its cluster decision is `accept`; the cluster has `consensus_3_of_3` or `consensus_2_of_3` for the same named, non-generic, catalogued role; the decision has a semantic cross-check; and every scored model marked the row `row_outlier=false`.

5. **Full review of pending rows.** Write normal full-evidence review rows (SKILL.md and `role-labeling.md`) for every row in `pending_rows.tsv`: `MULTI_SPEAKER_REVIEW`, `UNRESOLVED`, outliers, short interjections, generic voices, rejected clusters, and new named roles. Use row-level voice probes, video, and TalkNet as in the standard track. You may also full-review a light-eligible row if you doubt it; a full row always overrides the light row.

6. **Merge**: run the same `expand-cluster-decisions` command with `--full-review-tsv <work>/full_rows.tsv --out-review-tsv <work>/review_all.tsv --overwrite`. It fails if any pending row is missing.

7. **Apply, audit, QC**, each with the tiering flags:

   ```powershell
   & $PYTHON $TOOL apply-review --tsv <codex_draft.tsv> --review-tsv <review_all.tsv> --roles <approved_roles> `
     --out-tsv <reviewed_roles.tsv> --require-all --evidence-tiering --cluster-decisions <cluster_decisions.tsv>
   & $PYTHON $TOOL export-blind-audit --tsv <reviewed_roles.tsv> --out-tsv <blind_audit.tsv> --evidence-tiering
   # blind-audit subagent fills blind_audit.tsv exactly as in the standard track
   & $PYTHON $TOOL apply-independent-audit --tsv <reviewed_roles.tsv> --audit-tsv <blind_audit.tsv> `
     --out-tsv <final_roles.tsv> --evidence-tiering
   & $PYTHON $TOOL qc --tsv <final_roles.tsv> --srt <source.srt> --roles <approved_roles> --report <qc_report.json> `
     --require-reviewed-all --fail-on-issues --evidence-tiering --cluster-decisions <cluster_decisions.tsv>
   ```

8. **Episode-boundary gate**: unchanged. A new named role, or a named role that stays unresolved, opens the whole-episode confirmation in the single `総合脚本` format. Rows the user confirms become user-locked, and the user decision supersedes cluster decisions and the audit for those rows.

## 3. Light-tier rules

- A light row carries `evidence_tier=light`, `cluster_decision_id`, `cluster_row_outlier=false`, the cluster ensemble fields, `identity_status=confirmed`, a generated `identity_evidence`, `review_confidence` 0.92 (3 of 3) or 0.90 (2 of 3), `visual_class=not_reviewed`, `coarse_audio_status=not_required`, and `audio_conflict=false`. It does not need row-specific semantic prose, because the cluster decision holds the cross-check.
- QC in tiering mode re-checks every light row against `cluster_decisions.tsv`: the decision must be `accept`, for the same role and episode, with a cross-check of at least 8 characters, and with no open relationship, audio, or voice conflict. Light rows are excluded from the boilerplate check. Full rows keep every standard check.
- Standard-mode commands (without `--evidence-tiering`) reject light rows with `light_evidence_tier_requires_tiering_mode`, so the two tracks cannot be mixed by accident.
- Semantic-continuity QC still applies to every row. If a light row sits in a role switch inside a likely semantic unit, move that row to full review and add `turn_change_evidence`.

## 4. Sampled blind audit

`export-blind-audit --evidence-tiering` exports every full row, plus a deterministic sample of light rows: at least one per cluster decision, and `--audit-sample-ratio` (default 0.2) of the cluster's light rows. `apply-independent-audit --evidence-tiering` marks the sampled light rows `audit_sample=true`. QC requires each cluster's sample to be confirmed. The auditor still sees only source text, timing, and frames. Any disagreement blocks exactly as in the standard track: return the rows to review, and if a sampled light row disagrees, reject its whole cluster decision and full-review that cluster.

## 5. Delivery safety

QC records `evidence_tiering`, the `cluster_decisions.tsv` path and SHA-256, and the sample ratio in `qc_report.json`. `batch-status` and `assemble-delivery --require-complete` re-apply the tiered checks only when that file still has the recorded hash. Editing cluster decisions after QC therefore blocks assembly until QC is run again. The rest of the formal delivery route is unchanged.

## 6. Honest limits of the current track

- Claude cannot listen to audio. In this track, fill `audible_gender_age` only from a measurement or from the user. Otherwise record `unclear_voice`, and resolve the row with semantic and visual evidence or at the episode gate.
- All thresholds (segmentation 0.70 / 0.50 / 0.45 / 0.30 s; clusters 0.5 s / 3.0 s / 0.6 / 0.40; audit ratio 0.2) are starting values. **Before the first real delivery on this track, backtest it on one title that was already delivered with the standard track.** Compare `final_roles.tsv` with the delivered `総合脚本` by timecode and dialogue, and record: row accuracy, how many rows were light vs. pending, cluster rejects, audit disagreements, and wall-clock time per episode. Change a threshold only on the evidence of such a comparison, and note the change in the project notes.
- The encoders are still the VoxCeleb-trained WeSpeaker/pyannote models. This track does not yet fix the language mismatch (see E6).

## 7. Candidate improvements not yet implemented (候选，尚未实装)

These were proposed together with E1–E5, but they need new models or tools deployed on the GPU machine first. Do not claim or simulate them.

- **E6 中文说话人模型** — add Chinese-trained encoders (e.g. 3D-Speaker CAM++ / ERes2NetV2 zh-cn, CN-Celeb ResNet) as additional `--model-kind`s, with ONNX export and `backend_config.json`. Compare against the VoxCeleb models in the backtest before replacing any of them.
- **E7 中文原声识别** — ASR of the original Chinese audio (SenseVoice / Paraformer / Whisper) aligned per cue. It gives the original vocatives, self-reference, and names that the Japanese translation may drop, plus audio-event tags (crying, laughter, BGM) for the voice quality gate.
- **E8 可测量的性别/年龄** — an F0-statistics or classifier tool that writes `audible_gender_age` as a measured value, replacing the "listening" step Claude cannot actually perform.
- **E9 全剧人脸聚类 + TalkNet 共现** — cluster faces across the series into a recurring-person list that can be named once, run TalkNet on every row, and count voice-cluster × speaking-face co-occurrence to link voices to faces automatically.
- **E10 画面字可选 + OCR 人名条** — make `画面字.xlsx` optional, and read on-screen name cards from frames by OCR when it is missing.
