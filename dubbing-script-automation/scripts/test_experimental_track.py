#!/usr/bin/env python3
"""Offline regression test for the experimental track (no models, GPU, or media needed).

Covers: exemplar gallery calibration, cluster scoring, three-model merge, cluster
decision expansion into light/full review rows, sampled blind audit, tiered QC,
and the pre-assembly batch gate.  Also proves the standard QC still rejects
light-tier rows.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

import dubbing_tool  # noqa: E402
import voice_evidence  # noqa: E402

EPISODE = "0002"
LINES = [
    ("00:00:01,000", "00:00:02,500", "林晚です。", "Speaker01", "single_speaker"),
    ("00:00:03,000", "00:00:04,500", "今日は来てくれてありがとう。", "Speaker01", "single_speaker"),
    ("00:00:05,000", "00:00:06,500", "当然だろう。", "Speaker02", "single_speaker"),
    ("00:00:07,000", "00:00:08,500", "君に会いに来た。", "Speaker02", "single_speaker"),
    ("00:00:09,000", "00:00:10,500", "-えっ？\n-何でもない。", "MULTI_SPEAKER_REVIEW", "dual_dialogue_cue"),
    ("00:00:11,000", "00:00:12,500", "もう行くわ。", "Speaker01", "single_speaker"),
    ("00:00:13,000", "00:00:14,500", "お気をつけて。", "Speaker03", "single_speaker"),
]
TRUE_ROLES = ["林晚", "林晚", "沈昭", "沈昭", "林晚", "林晚", "女性音声01"]


def write_tsv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess:
    completed = subprocess.run([sys.executable, *args], capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != expect:
        raise AssertionError(f"{args[:2]} exited {completed.returncode}, expected {expect}\n{completed.stdout}\n{completed.stderr}")
    return completed


def check_exemplar_calibration() -> None:
    rng = np.random.default_rng(7)
    grouped = {}
    for role, axis in (("林晚", 0), ("沈昭", 1)):
        base = np.zeros(8)
        base[axis] = 1.0
        grouped[role] = [
            {"vector": voice_evidence.normalize(np, base + rng.normal(0, 0.15, 8)), "episode": "1", "duration": 3.0, "audit_index": i}
            for i in range(4)
        ]
    base_args = dict(
        min_intra_similarity=0.3, min_gallery_margin=0.08, min_total_duration_per_role=8.0, min_clips_per_role=3,
        min_episodes_per_role=1, min_similarity=0.35, min_margin=0.08, threshold_safety_margin=0.03,
        max_calibrated_margin=0.25,
    )
    centroid = voice_evidence.build_gallery_arrays(np, grouped, argparse.Namespace(**base_args))
    legacy = voice_evidence.build_gallery_arrays(np, grouped, argparse.Namespace(**base_args, scoring_mode="centroid"))
    assert centroid[0] == legacy[0] and centroid[2] == legacy[2], "centroid mode must match the standard calibration"
    exemplar = voice_evidence.build_gallery_arrays(np, grouped, argparse.Namespace(**base_args, scoring_mode="exemplar_max"))
    assert exemplar[0] == ["林晚", "沈昭"] and not exemplar[6], f"exemplar calibration issues: {exemplar[6]}"


def build_cluster_evidence(root: Path) -> tuple[Path, list[Path], Path]:
    anonymous = [
        {"episode": EPISODE, "source_index": str(i + 1), "start_timecode": start, "end_timecode": end,
         "anonymous_speaker": speaker, "diarization_status": status, "text": text}
        for i, (start, end, text, speaker, status) in enumerate(LINES)
    ]
    voices = {"00:00:01,000": [1, 0.05, 0], "00:00:03,000": [0.95, 0.1, 0.05], "00:00:05,000": [0.05, 1, 0],
              "00:00:07,000": [0.1, 0.95, 0], "00:00:11,000": [1, 0.1, 0], "00:00:13,000": [0, 0.1, 1]}
    cluster_args = argparse.Namespace(min_cue_duration=0.5, min_cluster_seconds=3.0, min_row_agreement=0.6,
                                      row_outlier_threshold=0.4, min_similarity=0.35, min_margin=0.08)
    evidence_paths, row_paths = [], []
    for kind in ("campplus", "ecapa512", "resnet34"):
        gallery = {
            "roles": ["林晚", "沈昭"], "enrollment_episodes": ["1"], "gallery_id": f"g-{kind}", "model_kind": kind,
            "model_fingerprint": f"fp-{kind}", "centroids": np.stack([voice_evidence.normalize(np, [1, 0, 0]), voice_evidence.normalize(np, [0, 1, 0])]),
            "role_similarity_thresholds": {"林晚": 0.5, "沈昭": 0.5}, "role_margin_thresholds": {"林晚": 0.1, "沈昭": 0.1},
        }

        def embed(clip, kind=kind):
            vector = voices[clip["start"]]
            if kind == "campplus" and clip["start"] == "00:00:11,000":
                vector = [0.2, 1, 0]  # this model hears row 6 as the other role
            return voice_evidence.normalize(np, vector), 1.5, {}

        clusters, details = voice_evidence.score_cluster_rows(anonymous, root / "ep.mp4", EPISODE, gallery, np, embed, cluster_args)
        evidence = root / f"{kind}_clusters.tsv"
        rows_path = root / f"{kind}_cluster_rows.tsv"
        write_tsv(evidence, clusters, voice_evidence.VOICE_OUTPUT_FIELDS)
        write_tsv(rows_path, details, voice_evidence.CLUSTER_ROW_FIELDS)
        evidence_paths.append(evidence)
        row_paths.append(rows_path)
    anonymous_path = root / "anonymous_script.tsv"
    write_tsv(anonymous_path, anonymous, ["episode", "source_index", "start_timecode", "end_timecode", "anonymous_speaker", "diarization_status", "text"])
    ensemble = root / "cluster_ensemble.tsv"
    run(str(HERE / "voice_ensemble.py"),
        *[arg for kind, path in zip(("campplus", "ecapa512", "resnet34"), evidence_paths) for arg in ("--evidence", f"{kind}={path}")],
        "--out-tsv", str(ensemble))
    statuses = {row["acoustic_turn_id"]: (row["ensemble_status"], row["ensemble_candidate"]) for row in read_tsv(ensemble)}
    assert statuses[f"cluster:{EPISODE}:Speaker01"] == ("consensus_3_of_3", "林晚"), statuses
    assert statuses[f"cluster:{EPISODE}:Speaker02"] == ("consensus_3_of_3", "沈昭"), statuses
    assert statuses[f"cluster:{EPISODE}:Speaker03"][0] == "insufficient_models", statuses
    return anonymous_path, row_paths, ensemble


def full_review_row(label: dict[str, str], role: str, note: str) -> dict[str, str]:
    generic = dubbing_tool.is_generic_role(role)
    return {
        "episode": label["episode"], "source_index": label["source_index"], "start": label["start"],
        "end": label["end"], "text": label["text"], "final_role": role,
        "review_confidence": "0.85" if generic else "0.95", "semantic_unit": f"U{label['source_index']}",
        "visual_class": "not_reviewed", "semantic_evidence": note,
        "identity_status": "generic" if generic else "confirmed",
        "identity_evidence": "" if generic else f"{note}（行{label['source_index']}）",
        "generic_speaker_key": "E0002-F01" if generic else "",
        "coarse_audio_status": "not_required", "audio_conflict": "false",
        "reviewed_by": "claude-code", "reviewer_note": f"full-tier row {label['source_index']}",
        "evidence_tier": "full",
    }


def main() -> int:
    check_exemplar_calibration()
    with tempfile.TemporaryDirectory(prefix="experimental-track-") as temp:
        root = Path(temp)
        episode_dir = root / "episodes" / EPISODE
        episode_dir.mkdir(parents=True)
        srt = episode_dir / "source.srt"
        srt.write_text("\n\n".join(f"{i + 1}\n{start} --> {end}\n{text}" for i, (start, end, text, _, _) in enumerate(LINES)) + "\n", encoding="utf-8")
        roles = root / "roles.txt"
        roles.write_text("林晚\n沈昭\n", encoding="utf-8")
        labels = root / "codex_draft.tsv"
        label_rows = [
            {"episode": EPISODE, "source_index": str(i + 1), "start": start, "end": end, "role": "", "confidence": "0.00",
             "status": "codex_review", "frame": "", "text": text, "provider": "", "model": ""}
            for i, (start, end, text, _, _) in enumerate(LINES)
        ]
        write_tsv(labels, label_rows, dubbing_tool.TSV_FIELDS)
        anonymous, row_paths, ensemble = build_cluster_evidence(root)

        decisions = root / "cluster_decisions.tsv"
        write_tsv(decisions, [
            {"cluster_decision_id": f"{EPISODE}:Speaker01", "episode": EPISODE, "anonymous_speaker": "Speaker01",
             "ensemble_status": "consensus_3_of_3", "ensemble_candidate": "林晚", "decision": "accept", "decided_role": "林晚",
             "semantic_crosscheck": "行1で林晚と名乗り、行2で来訪への礼を述べる流れと一致", "reviewed_by": "claude-code"},
            {"cluster_decision_id": f"{EPISODE}:Speaker02", "episode": EPISODE, "anonymous_speaker": "Speaker02",
             "ensemble_status": "consensus_3_of_3", "ensemble_candidate": "沈昭", "decision": "accept", "decided_role": "沈昭",
             "semantic_crosscheck": "行3-4は林晚の礼への応答で、訪ねてきた側の沈昭の発話", "reviewed_by": "claude-code"},
            {"cluster_decision_id": f"{EPISODE}:Speaker03", "episode": EPISODE, "anonymous_speaker": "Speaker03",
             "ensemble_status": "insufficient_models", "ensemble_candidate": "", "decision": "reject", "decided_role": "",
             "semantic_crosscheck": "声紋ギャラリー外の汎用話者のため行単位で確認する", "reviewed_by": "claude-code"},
        ], dubbing_tool.CLUSTER_DECISION_FIELDS)

        tool = str(HERE / "dubbing_tool.py")
        expand_args = [tool, "expand-cluster-decisions", "--labels", str(labels), "--anonymous-script", str(anonymous),
                       "--ensemble-tsv", str(ensemble), "--cluster-decisions", str(decisions), "--roles", str(roles),
                       *[arg for path in row_paths for arg in ("--cluster-rows-tsv", str(path))]]
        light_only = root / "light_only.tsv"
        pending = root / "pending.tsv"
        summary = json.loads(run(*expand_args, "--out-review-tsv", str(light_only), "--out-pending-tsv", str(pending)).stdout)
        assert summary["light_rows"] == 4, summary
        pending_rows = {row["source_index"]: row["pending_reason"] for row in read_tsv(pending)}
        assert set(pending_rows) == {"5", "6", "7"}, pending_rows
        assert pending_rows["6"] == "row is a cluster outlier or was not scored by every model", pending_rows

        full = root / "full_review.tsv"
        by_index = {row["source_index"]: row for row in label_rows}
        write_tsv(full, [
            full_review_row(by_index["5"], "林晚", "二人の掛け合い字幕だが台詞の主体は林晚の反応"),
            full_review_row(by_index["6"], "林晚", "立ち去りを告げる台詞で直前の林晚の会話を締めくくる"),
            full_review_row(by_index["7"], "女性音声01", "見送りの一言で人物表外の女性使用人の声"),
        ], dubbing_tool.REVIEW_FIELDS)
        review_all = root / "review_all.tsv"
        run(*expand_args, "--out-review-tsv", str(review_all), "--out-pending-tsv", str(pending), "--full-review-tsv", str(full), "--overwrite")
        assert len(read_tsv(review_all)) == 7

        reviewed = root / "reviewed.tsv"
        run(tool, "apply-review", "--tsv", str(labels), "--review-tsv", str(review_all), "--roles", str(roles),
            "--out-tsv", str(reviewed), "--require-all", "--evidence-tiering", "--cluster-decisions", str(decisions))
        run(tool, "apply-review", "--tsv", str(labels), "--review-tsv", str(review_all), "--roles", str(roles),
            "--out-tsv", str(root / "standard_reject.tsv"), "--require-all", expect=2)

        audit = root / "blind_audit.tsv"
        run(tool, "export-blind-audit", "--tsv", str(reviewed), "--out-tsv", str(audit), "--evidence-tiering")
        audit_rows = read_tsv(audit)
        exported = {row["source_index"] for row in audit_rows}
        assert {"5", "6", "7"} <= exported and len(exported) == 5, exported
        assert "role" not in audit_rows[0] and "final_role" not in audit_rows[0]
        for row in audit_rows:
            row.update(audit_role=TRUE_ROLES[int(row["source_index"]) - 1], auditor="claude-blind-audit",
                       audit_note="台詞内容と前後関係から独立に判断した")
        write_tsv(audit, audit_rows, dubbing_tool.BLIND_AUDIT_FIELDS)
        final_roles = episode_dir / "final_roles.tsv"
        run(tool, "apply-independent-audit", "--tsv", str(reviewed), "--audit-tsv", str(audit), "--out-tsv", str(final_roles), "--evidence-tiering")

        qc_report = episode_dir / "qc_report.json"
        run(tool, "qc", "--tsv", str(final_roles), "--srt", str(srt), "--roles", str(roles), "--report", str(qc_report),
            "--require-reviewed-all", "--fail-on-issues", "--evidence-tiering", "--cluster-decisions", str(decisions))
        report = json.loads(qc_report.read_text(encoding="utf-8"))
        assert report["production_ready"] is True and report["light_tier_rows"] == 4, report

        standard = json.loads(run(tool, "qc", "--tsv", str(final_roles), "--srt", str(srt), "--roles", str(roles), "--fail-on-issues", expect=2).stdout)
        assert any(issue["issue"] == "light_evidence_tier_requires_tiering_mode" for issue in standard["issue_rows"])

        status = dubbing_tool.collect_batch_status(root / "episodes", [EPISODE])
        assert status["complete"] is True, json.dumps(status, ensure_ascii=False, indent=2)
        decisions.write_text(decisions.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        tampered = dubbing_tool.collect_batch_status(root / "episodes", [EPISODE])
        assert tampered["complete"] is False and tampered["failed_qc"] == [EPISODE]
    print("PASS: experimental track (exemplar calibration, cluster scoring, light/full review, sampled audit, tiered QC, batch gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
