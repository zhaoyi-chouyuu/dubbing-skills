"""Regression checks for cue-content and source-waveform completeness gates."""

import csv
import argparse
import importlib.util
import unittest
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assemble_stems.py"
SPEC = importlib.util.spec_from_file_location("assemble_stems_content_gate", SCRIPT)
assembly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembly)


class ContentBoundaryGateTest(unittest.TestCase):
    def setUp(self):
        self.source = Path.cwd() / f"source_activity_{uuid.uuid4().hex}.wav"
        self.review = Path.cwd() / f"source_activity_{uuid.uuid4().hex}.tsv"
        self.source.write_bytes(b"source hash fixture")
        self.addCleanup(self.source.unlink, missing_ok=True)
        self.addCleanup(self.review.unlink, missing_ok=True)

    def match(self):
        return {
            "status": "accepted",
            "source_active_start": "0.31",
            "source_active_end": "2.73",
            "source_transcript": "ユシュー、ようやくお前に会えた",
            "content_start_evidence": "",
            "content_end_evidence": "",
            "asr_alignment_evidence": "",
            "waveform_evidence": "",
            "match_method": "asr_plus_multimodal",
            "match_confidence": "0.95",
            "source_evidence": "ASR词级时间戳加实际听辨核对",
            "reviewed_by": "reviewer",
        }

    def test_silent_cut_without_content_evidence_is_blocked(self):
        _, problems = assembly.validate_source_match(self.match(), "EP27-001", 5.0, 0.8)
        self.assertTrue(any("content_start_evidence" in problem for problem in problems))
        self.assertTrue(any("content_end_evidence" in problem for problem in problems))

    def test_named_prefix_and_tail_evidence_pass(self):
        match = self.match()
        match["content_start_evidence"] = "试听确认句首雲舒读作ユシュー完整"
        match["content_end_evidence"] = "试听确认句尾会えた及尾音完整"
        match["asr_alignment_evidence"] = "ASR词级时间戳0.31至2.73并核对人名音译"
        match["waveform_evidence"] = "波形活动0.31至2.73完整覆盖且前后无遗漏"
        data, problems = assembly.validate_source_match(match, "EP27-001", 5.0, 0.8)
        self.assertEqual(problems, [])
        self.assertEqual(data["active_start"], 0.31)

    def test_plan_row_requires_both_content_edges(self):
        row = {
            "content_start_evidence": "试听确认句首雲舒完整",
            "content_end_evidence": "",
        }
        with self.assertRaisesRegex(assembly.AssemblyError, "content_end_evidence"):
            assembly.require_content_boundary_evidence(row, "EP27-001")

    def test_plan_row_requires_asr_and_waveform_evidence(self):
        row = {
            "asr_alignment_evidence": "ASR词级时间戳核对完整",
            "waveform_evidence": "",
        }
        with self.assertRaisesRegex(assembly.AssemblyError, "waveform_evidence"):
            assembly.require_joint_detection_evidence(row, "EP27-001")

    def test_waveform_prefix_before_asr_start_is_blocked(self):
        rate = 1000
        data = np.zeros((3000, 2), dtype=np.float32)
        data[310:930] = 0.1
        data[1750:2730] = 0.1
        source = str(self.source.resolve())
        rows = [{
            "cue_id": "EP27-001",
            "source_path": source,
            "source_active_start": "1.75",
            "source_active_end": "2.73",
        }]
        result = assembly.audit_unassigned_source_activity(rows, {source: data}, rate)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(any(item["end"] <= 1.0 for item in result["unreviewed"]))

    def test_waveform_prefix_passes_when_included_in_cue(self):
        rate = 1000
        data = np.zeros((3000, 2), dtype=np.float32)
        data[310:930] = 0.1
        data[1750:2730] = 0.1
        source = str(self.source.resolve())
        rows = [{
            "cue_id": "EP27-001",
            "source_path": source,
            "source_active_start": "0.31",
            "source_active_end": "2.73",
        }]
        result = assembly.audit_unassigned_source_activity(rows, {source: data}, rate)
        self.assertEqual(result["status"], "passed")

    def test_verified_ng_activity_can_be_exempted_by_source_hash(self):
        rate = 1000
        data = np.zeros((3000, 2), dtype=np.float32)
        data[310:930] = 0.1
        data[1750:2730] = 0.1
        source = str(self.source.resolve())
        rows = [{
            "cue_id": "EP27-001",
            "source_path": source,
            "source_active_start": "1.75",
            "source_active_end": "2.73",
        }]
        fields = [
            "source_path", "source_sha256", "start", "end", "decision",
            "source_evidence", "reviewed_by",
        ]
        with self.review.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerow({
                "source_path": source,
                "source_sha256": assembly.sha256_file(self.source),
                "start": "0.30",
                "end": "0.95",
                "decision": "ng_or_extra",
                "source_evidence": "试听与台本确认该段为演员NG废句",
                "reviewed_by": "reviewer",
            })
        result = assembly.audit_unassigned_source_activity(
            rows, {source: data}, rate, self.review,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["accepted_ng_or_extra_count"], 1)

    def test_plan_reel_blocks_asr_match_that_misses_waveform_prefix(self):
        rate = 48000
        wav = Path.cwd() / f"joint_match_{uuid.uuid4().hex}.wav"
        cues = Path.cwd() / f"joint_match_{uuid.uuid4().hex}_cues.tsv"
        matches = Path.cwd() / f"joint_match_{uuid.uuid4().hex}_matches.tsv"
        plan = Path.cwd() / f"joint_match_{uuid.uuid4().hex}_plan.tsv"
        report = Path.cwd() / f"joint_match_{uuid.uuid4().hex}_activity.json"
        for path in (wav, cues, matches, plan, report):
            self.addCleanup(path.unlink, missing_ok=True)
        data = np.zeros((rate * 3, 2), dtype=np.float32)
        data[round(0.31 * rate):round(0.93 * rate)] = 0.1
        data[round(1.75 * rate):round(2.73 * rate)] = 0.1
        sf.write(wav, data, rate, subtype="PCM_24")
        with cues.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["source_index", "episode", "role", "start", "end", "text"],
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerow({
                "source_index": "EP27-001", "episode": "0027", "role": "燕玄昭",
                "start": "0.50", "end": "3.00", "text": "雲舒 ようやくお前に会えた",
            })
        row = self.match()
        row.update({
            "cue_id": "EP27-001",
            "role": "燕玄昭",
            "text": "雲舒 ようやくお前に会えた",
            "source_active_start": "1.75",
            "source_active_end": "2.73",
            "source_transcript": "ようやくお前に会えた",
            "content_start_evidence": "试听误认为句首从ようやく开始",
            "content_end_evidence": "试听确认句尾会えた及尾音完整",
            "asr_alignment_evidence": "ASR只命中1.75至2.73的ようやく以后",
            "waveform_evidence": "候选范围1.75至2.73待覆盖审计",
        })
        with matches.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=assembly.MATCH_COLUMNS, delimiter="\t")
            writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in assembly.MATCH_COLUMNS})
        args = argparse.Namespace(
            audio=str(wav), cue_tsv=str(cues), srt=None, episode="0027", role=None,
            group_semantic_units=False, source_match_tsv=str(matches),
            min_match_confidence=0.75, plan=str(plan), sample_rate=rate,
            threshold_dbfs=-42.0, frame_ms=10.0, bridge_ms=250.0,
            min_active_ms=120.0, pad_ms=100.0, ffmpeg="ffmpeg",
            source_activity_review=None, source_activity_report=str(report),
            source_activity_lookaround_ms=2000.0,
            source_activity_threshold_dbfs=-42.0, source_activity_frame_ms=10.0,
            source_activity_bridge_ms=80.0, source_activity_min_ms=60.0,
            source_activity_coverage_tolerance_ms=40.0,
        )
        self.assertEqual(assembly.plan_reel(args), 2)
        with plan.open("r", encoding="utf-8-sig", newline="") as handle:
            planned = next(csv.DictReader(handle, delimiter="\t"))
        self.assertEqual(planned["status"], "review")
        self.assertIn("unassigned waveform activity", planned["notes"])


if __name__ == "__main__":
    unittest.main()
