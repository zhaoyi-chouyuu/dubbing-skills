"""Regression checks for the beta1.5 pre-mix overlap gate."""

import csv
import importlib.util
import unittest
import uuid
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "assemble_stems.py"
SPEC = importlib.util.spec_from_file_location("assemble_stems", SCRIPT)
assembly = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembly)


class OverlapGateTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path.cwd()
        self.prefix = f"overlap_gate_{uuid.uuid4().hex}"
        self.rate = 48000
        self.first = np.zeros((self.rate, 2), dtype=np.float32)
        self.second = np.zeros_like(self.first)
        self.first[4800:24000] = 0.1
        self.second[12000:28800] = 0.1
        self.paths = [self.folder / f"{self.prefix}_a.wav", self.folder / f"{self.prefix}_b.wav"]
        self.review = self.folder / f"{self.prefix}_review.tsv"
        for path in [*self.paths, self.review]:
            self.addCleanup(path.unlink, missing_ok=True)

    def audit(self, review=None):
        for path in self.paths:
            path.write_bytes(path.name.encode("utf-8"))
        return assembly.aligned_overlap_audit(
            self.paths, [self.first, self.second], self.rate,
            20.0, -42.0, 80.0, 80.0, review,
        )

    def test_unreviewed_overlap_is_blocked(self):
        result = self.audit()
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["unreviewed_count"], 1)

    def test_nonoverlapping_stems_pass(self):
        self.second[:] = 0
        self.second[30000:40000] = 0.1
        result = self.audit()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["detected_count"], 0)

    def test_review_requires_matching_source_hash(self):
        detected = self.audit()["detections"][0]
        review = self.review
        fields = ["stem_a", "stem_b", "stem_a_sha256", "stem_b_sha256", "start",
                  "end", "decision", "source_evidence", "reviewed_by"]
        row = {key: detected[key] for key in fields[:6]}
        row.update(decision="intentional_overlap",
                   source_evidence="episode 1 original audio 0.25s: both speakers audible",
                   reviewed_by="human reviewer")
        with review.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerow(row)
        self.assertEqual(self.audit(review)["status"], "passed")
        self.paths[1].write_bytes(b"changed source bytes")
        result = assembly.aligned_overlap_audit(
            self.paths, [self.first, self.second], self.rate,
            20.0, -42.0, 80.0, 80.0, review,
        )
        self.assertEqual(result["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
