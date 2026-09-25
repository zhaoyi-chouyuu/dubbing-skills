#!/usr/bin/env python3
"""Offline test for experimental subtitle-guided anonymous segmentation (no models needed)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

import anonymous_speaker_draft as draft  # noqa: E402

SRT = """1
00:00:01,000 --> 00:00:02,500
林晚です。

2
00:00:03,000 --> 00:00:04,500
今日はありがとう。

3
00:00:05,000 --> 00:00:06,500
当然だろう。

4
00:00:07,000 --> 00:00:09,500
-えっ？
-何でもない。

5
00:00:10,000 --> 00:00:14,000
待って、話があるの。いいから聞け。

6
00:00:15,000 --> 00:00:15,400
あっ

7
00:00:16,000 --> 00:00:17,500
もう行くわ。

8
00:00:18,000 --> 00:00:19,500
君に会いに来た。

9
00:00:20,000 --> 00:00:21,500
行かないで。

10
00:00:22,000 --> 00:00:23,500
また明日。
"""


def unit(values):
    return draft.unit_vector(np, values)


def feature(vector, voiced=(0.0, 1.0), halves=None, fallback=False):
    item = {"voiced": [voiced], "voiced_seconds": voiced[1] - voiced[0], "vad_fallback": fallback,
            "window_count": 3 if halves else 1, "embedding": unit(vector)}
    if halves:
        item["halves"] = (unit(halves[0]), unit(halves[1]))
    return item


def main() -> int:
    assert draft.is_dual_dialogue_text("-えっ？\n-何でもない。")
    assert draft.is_dual_dialogue_text("－えっ？\n－何？")
    assert not draft.is_dual_dialogue_text("えっ？\n何でもない。")
    assert not draft.is_dual_dialogue_text("-えっ？")
    assert draft.voiced_intervals(1.0, 2.0, [(0.5, 1.2), (1.5, 1.7), (2.5, 3.0)]) == [(1.0, 1.2), (1.5, 1.7)]

    with tempfile.TemporaryDirectory(prefix="subtitle-seg-") as temp:
        root = Path(temp)
        srt = root / "source.srt"
        srt.write_text(SRT, encoding="utf-8")
        cues = draft.parse_srt(srt)
        a, b = [1.0, 0.05, 0.0], [0.05, 1.0, 0.0]
        features = [
            feature(a, (1.0, 2.4)),
            feature([0.97, 0.1, 0.02], (3.0, 4.4)),
            feature(b, (5.0, 6.4)),
            feature([0.6, 0.6, 0.0], (7.0, 9.4), halves=(a, b)),       # dual-dialogue text
            feature([0.6, 0.6, 0.0], (10.0, 13.9), halves=(a, b)),     # voice changes inside one cue
            None,                                                      # no speech found
            feature([0.9, 0.1, 0.4], (16.0, 17.4)),                    # A, but far from the A cluster
            feature([0.1, 0.97, 0.0], (18.0, 19.4)),
            feature([0.98, 0.0, 0.1], (20.0, 21.4)),
            feature([0.6, 0.0, 0.8], (22.0, 23.4), fallback=True),     # too little VAD speech, weakly A-like
        ]
        options = {"similarity_threshold": 0.7, "num_speakers": None, "min_speakers": 1, "max_speakers": 20,
                   "intra_change_threshold": 0.5, "outlier_threshold": 0.95}
        result = draft.assign_subtitle_speakers("0001", cues, features, options)
        by_index = {row["source_index"]: row for row in result["mapped"]}
        assert by_index[1]["anonymous_speaker"] == "Speaker01" and by_index[1]["diarization_status"] == "single_speaker"
        assert by_index[2]["anonymous_speaker"] == "Speaker01"
        assert by_index[3]["anonymous_speaker"] == "Speaker02"
        assert by_index[4]["anonymous_speaker"] == "MULTI_SPEAKER_REVIEW" and by_index[4]["diarization_status"] == "dual_dialogue_cue"
        assert by_index[4]["speaker_candidates"] == "Speaker01|Speaker02"
        assert by_index[5]["diarization_status"] == "intra_cue_change_suspected"
        assert by_index[6]["anonymous_speaker"] == "UNRESOLVED" and by_index[6]["diarization_status"] == "no_speech_in_cue"
        assert by_index[7]["anonymous_speaker"] == "Speaker01" and by_index[7]["diarization_status"] == "cluster_outlier_review"
        assert by_index[8]["anonymous_speaker"] == "Speaker02"
        assert by_index[10]["diarization_status"] == "low_voiced_unresolved"
        assert [row["source_index"] for row in result["mapped"]] == list(range(1, 11))
        assert all(row["text"] == cue["text"] for row, cue in zip(result["mapped"], cues)), "subtitle text must be untouched"

        relaxed = draft.assign_subtitle_speakers("0001", cues, features, dict(options, outlier_threshold=0.5))
        assert {row["source_index"]: row for row in relaxed["mapped"]}[10]["diarization_status"] == "low_voiced_review"

        out_dir = root / "0001"
        report = draft.build_outputs("0001", srt, [], out_dir, 0.7, subtitle_result=result)
        assert report["ready"] and report["segmentation_mode"] == "subtitle_guided"
        assert (out_dir / "subtitle_segmentation_diagnostics.tsv").is_file()
        with (out_dir / "anonymous_script.tsv").open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle, delimiter="\t"))
        assert header == ["episode", "source_index", "start_timecode", "end_timecode", "anonymous_speaker",
                          "speaker_candidates", "dominant_overlap_ratio", "speaker_change_inside_cue",
                          "diarization_status", "text"], "downstream column contract changed"

        tool_path = HERE.parents[1] / "dubbing-script-automation" / "scripts" / "dubbing_tool.py"
        if tool_path.is_file():
            spec = importlib.util.spec_from_file_location("dubbing_tool_for_prep_test", tool_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            issues = module.anonymous_source_issues(
                {"anonymous_speaker_map": str(out_dir / "subtitle_speaker_map.tsv"),
                 "anonymous_script_tsv": str(out_dir / "anonymous_script.tsv")},
                "0001", srt,
            )
            assert not issues, issues
        json.loads((out_dir / "diarization_report.json").read_text(encoding="utf-8"))
    print("PASS: subtitle-guided segmentation (cue clustering, dual-dialogue/intra-cue flags, outliers, handoff contract)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
