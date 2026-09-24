#!/usr/bin/env python3
"""Safe short-drama dialogue assembly.

Modes:
  audit-reference  Inspect delivered mixes/stems without changing them.
  audit-plan-boundaries
                   Refuse source cuts that are likely inside active speech.
  mix-aligned      Mix stems that are already aligned to the episode timeline.
  match-template   Create a spoken-content matching sheet for an unaligned reel.
  plan-reel        Match an unaligned role/combined reel to ordered dialogue cues.
  render-plan      Render a reviewed plan, or automatically split unresolved timing rows.
  render-manual-stems
                   Experimentally split safe and manual-timing dialogue.

The tool deliberately refuses ambiguous automatic alignment. It never overwrites
source material and writes a machine-readable QC report beside each output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf


class AssemblyError(RuntimeError):
    pass


def nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def parse_timecode(value: object) -> float:
    text = nfc(value).replace(",", ".")
    if not text:
        raise AssemblyError("empty timecode")
    parts = text.split(":")
    try:
        if len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1:
            seconds = float(parts[0])
        else:
            raise ValueError
    except ValueError as exc:
        raise AssemblyError(f"invalid timecode: {value!r}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise AssemblyError(f"invalid timecode: {value!r}")
    return seconds


def dbfs(amplitude: float) -> float:
    return 20.0 * math.log10(max(float(amplitude), 1e-12))


def exact_episode_from_name(path: Path) -> str | None:
    match = re.search(r"(?:^|_)(\d{4})(?=(?:\D|$))", nfc(path.stem))
    return match.group(1) if match else None


def list_audio(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise AssemblyError(f"audio directory not found: {directory}")
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and not p.name.startswith((".", "._"))
        and p.suffix.lower() in {".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a"}
    )


def find_ffmpeg(explicit: str | None) -> str:
    candidates = [
        explicit,
        "/opt/homebrew/bin/ffmpeg",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        r"D:\JianyingPro\11.5.0.14471\ffmpeg.exe",
        r"D:\FormatFactory\ffmpeg.exe",
        r"D:\JianyingPro\10.7.0.14095\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise AssemblyError("ffmpeg not found; pass --ffmpeg")


def read_audio(path: Path, target_sr: int, ffmpeg: str) -> np.ndarray:
    info = sf.info(path)
    if info.samplerate == target_sr and info.channels in {1, 2}:
        data, _ = sf.read(path, dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        return data
    command = [
        ffmpeg, "-v", "error", "-i", str(path), "-f", "f32le",
        "-acodec", "pcm_f32le", "-ar", str(target_sr), "-ac", "2", "-",
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise AssemblyError(f"ffmpeg conversion failed for {path}: {message}")
    raw = np.frombuffer(result.stdout, dtype="<f4")
    if raw.size % 2:
        raise AssemblyError(f"invalid converted stereo stream: {path}")
    return raw.reshape(-1, 2).copy()


def write_audio(path: Path, data: np.ndarray, sample_rate: int, subtype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise AssemblyError(f"refusing to overwrite existing output: {path}")
    sf.write(path, np.clip(data, -1.0, 1.0), sample_rate, subtype=subtype)
    if not path.is_file() or path.stat().st_size == 0:
        raise AssemblyError(f"output was not written: {path}")


def normalize_peak(data: np.ndarray, target_dbfs: float | None) -> tuple[np.ndarray, float]:
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak <= 1e-9:
        raise AssemblyError("rendered audio is silent")
    if target_dbfs is None:
        if peak > 1.0:
            data = data / peak
        return data, dbfs(float(np.max(np.abs(data))))
    target = 10.0 ** (target_dbfs / 20.0)
    return data * (target / peak), target_dbfs


def audio_metrics(data: np.ndarray) -> dict[str, float]:
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if data.size else 0.0
    return {
        "peak_dbfs": dbfs(peak),
        "rms_dbfs": dbfs(rms),
        "clipping_ratio": float(np.mean(np.abs(data) >= 1.0)) if data.size else 0.0,
        "near_silence_ratio": float(np.mean(np.max(np.abs(data), axis=1) < 1e-5)) if len(data) else 1.0,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def preflight_aligned_stems(
    paths: list[Path], arrays: list[np.ndarray], sample_rate: int,
    srt_end: float | None,
    max_duration_diff: float = 5.0,
    max_srt_overshoot: float = 10.0,
    activity_threshold_dbfs: float = -42.0,
    activity_frame_ms: float = 20.0,
    max_activity_ratio: float = 0.60,
    max_overlap_ratio: float = 0.10,
) -> dict:
    """beta2.0 pre-flight gates for mix-aligned.

    Gate 1: Duration consistency — all stems within max_duration_diff seconds.
    Gate 2: Activity density — no single stem exceeds max_activity_ratio.
    Gate 3: Overlap area — pairwise simultaneous activity below max_overlap_ratio.

    Returns a dict with status='passed' or status='failed' plus gate details.
    """
    durations = [len(a) / sample_rate for a in arrays]
    frame_size = max(1, round(sample_rate * activity_frame_ms / 1000))
    max_frames = max(math.ceil(len(a) / frame_size) for a in arrays)

    # --- Gate 1: duration consistency ---
    dur_diff = max(durations) - min(durations)
    gate1 = {"gate": "duration_consistency", "max_diff_s": round(dur_diff, 3),
             "threshold_s": max_duration_diff}
    if dur_diff > max_duration_diff:
        gate1["status"] = "failed"
        gate1["detail"] = (
            f"stem durations differ by {dur_diff:.1f}s (max allowed {max_duration_diff:.1f}s); "
            f"likely not single-episode aligned stems"
        )
    elif srt_end is not None and max(durations) > srt_end + max_srt_overshoot:
        gate1["status"] = "failed"
        gate1["detail"] = (
            f"longest stem ({max(durations):.1f}s) exceeds SRT end ({srt_end:.1f}s) "
            f"by more than {max_srt_overshoot:.1f}s; likely multi-episode reel"
        )
    else:
        gate1["status"] = "passed"

    # --- compute per-stem activity arrays (reused by gates 2 and 3) ---
    threshold_amp = 10.0 ** (activity_threshold_dbfs / 20.0)
    activity = np.zeros((len(arrays), max_frames), dtype=bool)
    activity_ratios: list[float] = []
    for track_idx, data in enumerate(arrays):
        n_frames = math.ceil(len(data) / frame_size)
        active_count = 0
        for fi in range(n_frames):
            chunk = data[fi * frame_size:(fi + 1) * frame_size]
            rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
            is_active = rms >= threshold_amp
            activity[track_idx, fi] = is_active
            if is_active:
                active_count += 1
        ratio = active_count / max(n_frames, 1)
        activity_ratios.append(ratio)

    # --- Gate 2: activity density ---
    gate2_details: list[dict] = []
    gate2_status = "passed"
    for i, (path, ratio) in enumerate(zip(paths, activity_ratios)):
        entry = {"stem": path.name, "activity_ratio": round(ratio, 4)}
        if ratio > max_activity_ratio:
            entry["status"] = "failed"
            entry["detail"] = (
                f"activity ratio {ratio:.1%} exceeds {max_activity_ratio:.0%}; "
                f"too dense for single-role aligned stem — use plan-reel instead"
            )
            gate2_status = "failed"
        else:
            entry["status"] = "passed"
        gate2_details.append(entry)
    gate2 = {"gate": "activity_density", "status": gate2_status,
             "threshold": max_activity_ratio, "stems": gate2_details}

    # --- Gate 3: pairwise overlap area ---
    total_overlap_frames = 0
    pair_details: list[dict] = []
    for first in range(len(paths)):
        for second in range(first + 1, len(paths)):
            together = activity[first] & activity[second]
            overlap_count = int(np.count_nonzero(together))
            total_overlap_frames += overlap_count
            pair_details.append({
                "stem_a": paths[first].name, "stem_b": paths[second].name,
                "overlap_frames": overlap_count,
                "overlap_ratio": round(overlap_count / max(max_frames, 1), 4),
            })
    # use the max pairwise overlap ratio as the gate value
    max_pair_ratio = max((p["overlap_ratio"] for p in pair_details), default=0.0)
    gate3 = {"gate": "overlap_area", "status": "passed",
             "max_pairwise_ratio": round(max_pair_ratio, 4),
             "threshold": max_overlap_ratio, "pairs": pair_details}
    if max_pair_ratio > max_overlap_ratio:
        gate3["status"] = "failed"
        gate3["detail"] = (
            f"max pairwise simultaneous activity {max_pair_ratio:.1%} exceeds "
            f"{max_overlap_ratio:.0%}; these are NOT precision-aligned stems"
        )

    overall = "passed" if all(
        g["status"] == "passed" for g in [gate1, gate2, gate3]
    ) else "failed"
    return {
        "preflight_status": overall,
        "gates": [gate1, gate2, gate3],
        "stem_durations": {p.name: round(d, 3) for p, d in zip(paths, durations)},
        "stem_activity_ratios": {p.name: round(r, 4) for p, r in zip(paths, activity_ratios)},
    }


def preflight_script_timing(
    paths: list[Path], arrays: list[np.ndarray], sample_rate: int,
    cue_tsv: Path | None, srt: Path | None,
    frame_ms: float = 20.0, threshold_dbfs: float = -42.0,
    tolerance_s: float = 0.5, min_activity_ms: float = 60.0,
    max_off_cue_ratio: float = 0.10,
) -> dict:
    """Check that speech activity falls near reviewed dialogue timecodes.

    A cue TSV must identify the exact source filename in ``stem_file``.
    SRT is a weaker aggregate check because it has no speaker identities.
    Neither check proves the spoken words match the script.
    """
    if cue_tsv is None and srt is None:
        return {"gate": "script_timing", "status": "failed",
                "detail": "provide --cue-tsv with stem_file mapping or --srt"}
    names = {path.name for path in paths}
    if cue_tsv is not None:
        with cue_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            required = {"cue_id", "start", "end", "text", "stem_file"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                return {"gate": "script_timing", "status": "failed",
                        "detail": "cue TSV requires cue_id,start,end,text,stem_file"}
            cues = []
            for row in reader:
                stem = nfc(row.get("stem_file"))
                if stem not in names:
                    return {"gate": "script_timing", "status": "failed",
                            "detail": f"cue {row.get('cue_id')} has unknown stem_file {stem!r}"}
                if not nfc(row.get("text")):
                    return {"gate": "script_timing", "status": "failed",
                            "detail": f"cue {row.get('cue_id')} has no spoken text"}
                start, end = parse_timecode(row.get("start")), parse_timecode(row.get("end"))
                if end <= start:
                    return {"gate": "script_timing", "status": "failed",
                            "detail": f"cue {row.get('cue_id')} has invalid time range"}
                cues.append({"cue_id": nfc(row.get("cue_id")), "start": start,
                             "end": end, "stem_file": stem})
        scope = "per_stem"
        source = cue_tsv
    else:
        cues = parse_srt(srt)
        scope = "aggregate_only"
        source = srt
    if not cues:
        return {"gate": "script_timing", "status": "failed", "detail": "no dialogue cues"}
    if len({cue["cue_id"] for cue in cues}) != len(cues):
        return {"gate": "script_timing", "status": "failed", "detail": "duplicate cue_id"}

    frame_size = max(1, round(sample_rate * frame_ms / 1000))
    max_frames = max(math.ceil(len(a) / frame_size) for a in arrays)
    masks = []
    threshold = 10 ** (threshold_dbfs / 20)
    for data in arrays:
        mask = np.zeros(max_frames, dtype=bool)
        for index in range(math.ceil(len(data) / frame_size)):
            chunk = data[index * frame_size:(index + 1) * frame_size]
            mask[index] = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64)))) >= threshold
        masks.append(mask)

    expected = {path.name: np.zeros(max_frames, dtype=bool) for path in paths}
    misses = []
    min_frames = max(1, math.ceil(min_activity_ms / frame_ms))
    aggregate = np.logical_or.reduce(masks)
    for cue in cues:
        begin = max(0, math.floor((cue["start"] - tolerance_s) * sample_rate / frame_size))
        finish = min(max_frames, math.ceil((cue["end"] + tolerance_s) * sample_rate / frame_size))
        if finish <= begin:
            misses.append(cue["cue_id"])
            continue
        if scope == "per_stem":
            stem = cue["stem_file"]
            expected[stem][begin:finish] = True
            activity = masks[next(i for i, path in enumerate(paths) if path.name == stem)]
        else:
            activity = aggregate
        if int(np.count_nonzero(activity[begin:finish])) < min_frames:
            misses.append(cue["cue_id"])

    if scope == "aggregate_only":
        covered = np.zeros(max_frames, dtype=bool)
        for cue in cues:
            begin = max(0, math.floor((cue["start"] - tolerance_s) * sample_rate / frame_size))
            finish = min(max_frames, math.ceil((cue["end"] + tolerance_s) * sample_rate / frame_size))
            covered[begin:finish] = True
        off_cue = {"all_stems": round(float(np.count_nonzero(aggregate & ~covered)) /
                                    max(int(np.count_nonzero(aggregate)), 1), 4)}
    else:
        off_cue = {path.name: round(float(np.count_nonzero(mask & ~expected[path.name])) /
                                    max(int(np.count_nonzero(mask)), 1), 4)
                   for path, mask in zip(paths, masks)}
    excessive = {stem: ratio for stem, ratio in off_cue.items()
                 if ratio > max_off_cue_ratio}
    # Consecutive same-speaker subtitle lines form one acoustic turn. Only the
    # start of each turn needs a fresh onset; internal lines may be continuous.
    turns = []
    for cue in sorted(cues, key=lambda item: item["start"]):
        stem = cue.get("stem_file", "all_stems")
        if (turns and turns[-1]["stem"] == stem
                and cue["start"] <= turns[-1]["end"] + 0.25):
            turns[-1]["end"] = max(turns[-1]["end"], cue["end"])
        else:
            turns.append({"cue_id": cue["cue_id"], "stem": stem,
                          "start": cue["start"], "end": cue["end"]})
    onset_errors = []
    for turn in turns:
        activity = (aggregate if scope == "aggregate_only" else
                    masks[next(i for i, path in enumerate(paths)
                               if path.name == turn["stem"])])
        begin = max(0, math.floor((turn["start"] - tolerance_s) * sample_rate / frame_size))
        finish = min(max_frames, math.ceil((turn["start"] + tolerance_s) * sample_rate / frame_size))
        hits = np.flatnonzero(activity[begin:finish])
        if len(hits) == 0:
            onset_errors.append({"cue_id": turn["cue_id"], "stem": turn["stem"],
                                 "target_start": turn["start"], "reason": "no activity near turn start"})
        elif begin > 0 and activity[begin - 1] and hits[0] == 0:
            onset_errors.append({"cue_id": turn["cue_id"], "stem": turn["stem"],
                                 "target_start": turn["start"], "reason": "speech already active before tolerance window"})
    status = "passed" if not misses and not excessive and not onset_errors else "failed"
    return {"gate": "script_timing", "status": status, "scope": scope,
            "source": str(source), "source_sha256": sha256_file(source),
            "cue_count": len(cues), "missing_activity_cue_ids": misses,
            "turn_count": len(turns), "turn_onset_errors": onset_errors,
            "off_cue_activity_ratio": off_cue, "excessive_off_cue": excessive,
            "tolerance_s": tolerance_s, "max_off_cue_ratio": max_off_cue_ratio,
            "detail": f"{len(misses)} cues without matching activity; "
                      f"{len(onset_errors)} turn starts outside tolerance; "
                      f"{len(excessive)} stems with excessive off-cue activity"}


def aligned_overlap_audit(
    paths: list[Path], arrays: list[np.ndarray], sample_rate: int,
    frame_ms: float, threshold_dbfs: float, min_overlap_ms: float,
    bridge_gap_ms: float, review_path: Path | None,
) -> dict:
    """Find concurrent activity across stems before mixing any output."""
    if frame_ms <= 0 or min_overlap_ms <= 0 or bridge_gap_ms < 0:
        raise AssemblyError("overlap frame/minimum must be positive and bridge gap nonnegative")
    frame_size = max(1, round(sample_rate * frame_ms / 1000))
    frame_seconds = frame_size / sample_rate
    frame_count = math.ceil(max(map(len, arrays)) / frame_size)
    activity = np.zeros((len(arrays), frame_count), dtype=bool)
    hashes = {path.name: sha256_file(path) for path in paths}
    for track_index, data in enumerate(arrays):
        for frame_index in range(math.ceil(len(data) / frame_size)):
            frame = data[frame_index * frame_size:(frame_index + 1) * frame_size]
            rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
            activity[track_index, frame_index] = dbfs(rms) >= threshold_dbfs

    detections = []
    max_gap_frames = math.floor(bridge_gap_ms / (frame_seconds * 1000))
    for first in range(len(paths)):
        for second in range(first + 1, len(paths)):
            together = activity[first] & activity[second]
            active_indices = np.flatnonzero(together)
            if not len(active_indices):
                continue
            group_start = previous = int(active_indices[0])
            groups = []
            for raw_index in active_indices[1:]:
                index = int(raw_index)
                if index - previous - 1 > max_gap_frames:
                    groups.append((group_start, previous + 1))
                    group_start = index
                previous = index
            groups.append((group_start, previous + 1))
            for start_frame, end_frame in groups:
                active_frames = int(np.count_nonzero(together[start_frame:end_frame]))
                if active_frames * frame_seconds * 1000 < min_overlap_ms:
                    continue
                detections.append({
                    "stem_a": paths[first].name,
                    "stem_b": paths[second].name,
                    "stem_a_sha256": hashes[paths[first].name],
                    "stem_b_sha256": hashes[paths[second].name],
                    "start": round(start_frame * frame_seconds, 3),
                    "end": round(end_frame * frame_seconds, 3),
                    "simultaneous_activity_ms": round(active_frames * frame_seconds * 1000, 1),
                })

    reviews = []
    if review_path:
        if not review_path.is_file():
            raise AssemblyError(f"overlap review file not found: {review_path}")
        required = {
            "stem_a", "stem_b", "stem_a_sha256", "stem_b_sha256", "start", "end",
            "decision", "source_evidence", "reviewed_by",
        }
        with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise AssemblyError("overlap review TSV is missing required columns")
            reviews = list(reader)

    unreviewed = []
    accepted = []
    for detected in detections:
        match = None
        for row in reviews:
            same_pair = {
                (row["stem_a"], row["stem_a_sha256"].lower()),
                (row["stem_b"], row["stem_b_sha256"].lower()),
            } == {
                (detected["stem_a"], detected["stem_a_sha256"]),
                (detected["stem_b"], detected["stem_b_sha256"]),
            }
            try:
                reviewed_start = parse_timecode(row["start"])
                reviewed_end = parse_timecode(row["end"])
            except AssemblyError:
                continue
            if (
                same_pair and reviewed_start <= detected["start"] + frame_seconds
                and reviewed_end >= detected["end"] - frame_seconds
                and reviewed_start < reviewed_end
                and nfc(row["decision"]) == "intentional_overlap"
                and len(nfc(row["source_evidence"])) >= 12
                and nfc(row["reviewed_by"])
            ):
                match = row
                break
        if match:
            accepted.append({**detected, "source_evidence": nfc(match["source_evidence"]),
                             "reviewed_by": nfc(match["reviewed_by"])})
        else:
            unreviewed.append(detected)
    return {
        "status": "needs_review" if unreviewed else "passed",
        "frame_ms": frame_ms,
        "threshold_dbfs": threshold_dbfs,
        "minimum_simultaneous_ms": min_overlap_ms,
        "bridge_gap_ms": bridge_gap_ms,
        "detected_count": len(detections),
        "accepted_intentional_count": len(accepted),
        "unreviewed_count": len(unreviewed),
        "detections": detections,
        "accepted_intentional": accepted,
        "unreviewed": unreviewed,
        "review_file": str(review_path) if review_path else "",
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def measure_loudness(path: Path, ffmpeg: str) -> dict[str, float | bool | None]:
    """Measure final-program loudness without changing the source file."""
    command = [
        ffmpeg, "-nostats", "-v", "info", "-i", str(path), "-map", "0:a:0",
        "-af", "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-",
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log = result.stderr.decode("utf-8", errors="replace")
    matches = re.findall(r"\{\s*\"input_i\".*?\}", log, flags=re.DOTALL)
    if result.returncode != 0 or not matches:
        return {"integrated_lufs": None, "true_peak_dbtp": None, "measurement_ok": False}
    try:
        report = json.loads(matches[-1])
        integrated = float(report["input_i"])
        true_peak = float(report["input_tp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"integrated_lufs": None, "true_peak_dbtp": None, "measurement_ok": False}
    if not math.isfinite(integrated) or not math.isfinite(true_peak):
        return {"integrated_lufs": None, "true_peak_dbtp": None, "measurement_ok": False}
    return {
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "within_lufs_range": -27.0 <= integrated <= -14.0,
        "target_delta_lu": integrated + 14.0,
        "measurement_ok": True,
    }


def audit_reference(args: argparse.Namespace) -> int:
    mix_dir = Path(args.mix_dir).resolve()
    stem_root = Path(args.stem_root).resolve() if args.stem_root else None
    mixes = [p for p in list_audio(mix_dir) if p.suffix.lower() == ".wav"]
    rows = []
    warnings = []
    mix_episodes = set()
    for path in mixes:
        episode = exact_episode_from_name(path)
        if not episode:
            warnings.append(f"cannot identify episode from mix filename: {path.name}")
            continue
        mix_episodes.add(episode)
        info = sf.info(path)
        peak = 0.0
        sum_sq = 0.0
        count = 0
        with sf.SoundFile(path) as handle:
            while True:
                block = handle.read(262144, dtype="float32", always_2d=True)
                if not len(block):
                    break
                peak = max(peak, float(np.max(np.abs(block))))
                sum_sq += float(np.sum(np.square(block, dtype=np.float64)))
                count += block.size
        rows.append({
            "episode": episode,
            "path": str(path),
            "sample_rate": info.samplerate,
            "channels": info.channels,
            "subtype": info.subtype,
            "duration": info.duration,
            "peak_dbfs": dbfs(peak),
            "rms_dbfs": dbfs(math.sqrt(sum_sq / max(count, 1))),
        })
    stem_episodes = set()
    if stem_root:
        stem_episodes = {
            p.name for p in stem_root.rglob("[0-9][0-9][0-9][0-9]") if p.is_dir()
        }
        missing_stems = sorted(mix_episodes - stem_episodes)
        missing_mixes = sorted(stem_episodes - mix_episodes)
        if missing_stems:
            warnings.append(f"mix episodes without stem folders: {','.join(missing_stems)}")
        if missing_mixes:
            warnings.append(f"stem episodes without mixes: {','.join(missing_mixes)}")
    median_peak = float(np.median([r["peak_dbfs"] for r in rows])) if rows else None
    peak_outliers = [
        r["episode"] for r in rows
        if median_peak is not None and (r["peak_dbfs"] > -0.5 or abs(r["peak_dbfs"] - median_peak) > 0.75)
    ]
    if peak_outliers:
        warnings.append(f"peak outlier episodes: {','.join(peak_outliers)}")
    summary = {
        "status": "ok" if rows else "failed",
        "mix_count": len(rows),
        "stem_episode_count": len(stem_episodes),
        "sample_rates": sorted({r["sample_rate"] for r in rows}),
        "channels": sorted({r["channels"] for r in rows}),
        "subtypes": sorted({r["subtype"] for r in rows}),
        "peak_median_dbfs": median_peak,
        "peak_min_dbfs": min((r["peak_dbfs"] for r in rows), default=None),
        "peak_max_dbfs": max((r["peak_dbfs"] for r in rows), default=None),
        "warnings": warnings,
        "episodes": rows,
    }
    write_json(Path(args.report).resolve(), summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "episodes"}, ensure_ascii=False))
    return 0 if rows else 2


def mix_aligned(args: argparse.Namespace) -> int:
    stem_dir = Path(args.stem_dir).resolve()
    output = Path(args.output).resolve()
    ffmpeg = find_ffmpeg(args.ffmpeg)
    paths = list_audio(stem_dir)
    if not paths:
        raise AssemblyError(f"no audio stems found: {stem_dir}")
    arrays = []
    source_specs = []
    for path in paths:
        info = sf.info(path)
        source_specs.append({
            "path": str(path), "sample_rate": info.samplerate,
            "channels": info.channels, "subtype": info.subtype,
            "duration": info.duration,
        })
        arrays.append(read_audio(path, args.sample_rate, ffmpeg))
    max_len = max(map(len, arrays))

    # --- beta2.0: pre-flight gates (before overlap audit) ---
    srt_end_for_preflight: float | None = None
    if args.srt:
        srt_path_pre = Path(args.srt).resolve()
        cues_pre = parse_srt(srt_path_pre)
        if cues_pre:
            srt_end_for_preflight = max(c["end"] for c in cues_pre)
    elif args.cue_tsv:
        with Path(args.cue_tsv).open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        if rows:
            srt_end_for_preflight = max(parse_timecode(row.get("end")) for row in rows)

    skip_preflight = getattr(args, "skip_preflight", False)
    preflight = preflight_aligned_stems(
        paths, arrays, args.sample_rate, srt_end_for_preflight,
    )
    script_gate = preflight_script_timing(
        paths, arrays, args.sample_rate,
        Path(args.cue_tsv).resolve() if args.cue_tsv else None,
        Path(args.srt).resolve() if args.srt else None,
    )
    preflight["gates"].append(script_gate)
    if script_gate["status"] != "passed":
        preflight["preflight_status"] = "failed"
    preflight_report = Path(args.overlap_report).resolve() if args.overlap_report else Path(f"{output}.preflight.json")
    if not skip_preflight:
        if preflight["preflight_status"] != "passed":
            # write the preflight report so the user can inspect what failed
            write_json(preflight_report, preflight)
            failed_gates = [g for g in preflight["gates"] if g["status"] == "failed"]
            details = "; ".join(g.get("detail", g["gate"]) for g in failed_gates)
            raise AssemblyError(
                f"mix-aligned preflight BLOCKED (beta2.0): {details}. "
                f"These stems are NOT precision-aligned single-episode stems. "
                f"Use plan-reel + merge-plans + render-plan instead. "
                f"Inspect {preflight_report}"
            )
    else:
        preflight["skip_preflight_override"] = True
        print("WARNING: --skip-preflight used; pre-flight gates bypassed", file=sys.stderr)

    # --- existing overlap audit ---
    audit = aligned_overlap_audit(
        paths, arrays, args.sample_rate, 20.0, -42.0, 80.0, 80.0,
        Path(args.overlap_review).resolve() if args.overlap_review else None,
    )
    audit["stem_directory"] = str(stem_dir)
    audit["output_requested"] = str(output)
    audit["source_sha256"] = {path.name: sha256_file(path) for path in paths}
    audit["preflight"] = preflight  # embed preflight results in audit
    if args.srt:
        srt_path = Path(args.srt).resolve()
        cues = parse_srt(srt_path)
        if not cues:
            raise AssemblyError(f"SRT has no cues: {srt_path}")
        audit["srt"] = {"path": str(srt_path), "sha256": sha256_file(srt_path),
                        "cue_count": len(cues), "last_cue_end": max(c["end"] for c in cues)}
        if audit["srt"]["last_cue_end"] > max_len / args.sample_rate + 0.05:
            audit["status"] = "needs_review"
            audit["timing_error"] = "SRT ends after the supplied stem timeline"
    report = Path(args.overlap_report).resolve() if args.overlap_report else Path(f"{output}.overlap_audit.json")
    write_json(report, audit)
    if audit["status"] != "passed":
        raise AssemblyError(
            f"mix-aligned preflight failed: {audit['unreviewed_count']} unreviewed "
            f"cross-stem overlap(s); inspect {report}. No mix was written"
        )
    mixed = np.zeros((max_len, 2), dtype=np.float32)
    for data in arrays:
        mixed[: len(data)] += data
    mixed, _ = normalize_peak(mixed, args.target_peak)
    write_audio(output, mixed, args.sample_rate, args.subtype)
    loudness = measure_loudness(output, ffmpeg)
    qc = {
        "status": "ok",
        "mode": "mix-aligned",
        "output": str(output),
        "sample_rate": args.sample_rate,
        "channels": 2,
        "subtype": args.subtype,
        "duration": len(mixed) / args.sample_rate,
        "source_count": len(paths),
        "sources": source_specs,
        "metrics": audio_metrics(mixed),
        "loudness": loudness,
        "preflight": {"status": preflight["preflight_status"],
                      "skip_override": skip_preflight,
                      "gates": {g["gate"]: g["status"] for g in preflight["gates"]},
                      "script_timing": {
                          "scope": script_gate.get("scope"),
                          "source": script_gate.get("source"),
                          "source_sha256": script_gate.get("source_sha256"),
                          "cue_count": script_gate.get("cue_count"),
                          "missing_activity_cue_ids": script_gate.get("missing_activity_cue_ids"),
                          "turn_onset_errors": script_gate.get("turn_onset_errors"),
                          "excessive_off_cue": script_gate.get("excessive_off_cue"),
                      }},
        "overlap_audit": {"status": audit["status"], "report": str(report),
                          "detected_count": audit["detected_count"],
                          "unreviewed_count": audit["unreviewed_count"],
                          "accepted_intentional_count": audit["accepted_intentional_count"]},
        "warnings": [
            "No timing correction was performed; use only for timeline-aligned stems.",
            "No noise gate was applied; preserve source breaths, tails, and room tone.",
        ] + (["SRT-only timing check cannot verify which role spoke each cue."]
             if script_gate.get("scope") == "aggregate_only" else []),
    }
    write_json(Path(args.qc_report or f"{output}.qc.json"), qc)
    print(json.dumps({k: v for k, v in qc.items() if k != "sources"}, ensure_ascii=False))
    return 0


def parse_srt(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="strict").strip()
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        parts = [part.strip() for part in lines[timing_index].split("-->")]
        if len(parts) != 2:
            raise AssemblyError(f"invalid SRT timing line: {lines[timing_index]}")
        cues.append({
            "cue_id": str(len(cues) + 1),
            "start": parse_timecode(parts[0]),
            "end": parse_timecode(parts[1]),
            "role": "",
            "semantic_unit": "",
            "text": " ".join(lines[timing_index + 1 :]),
        })
    if not cues:
        raise AssemblyError(f"no cues parsed from SRT: {path}")
    return cues


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_coverage_source(path: Path) -> list[dict]:
    """Read the immutable cue inventory used to prove whole-episode coverage."""
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return parse_srt(path)
    if suffix not in {".tsv", ".csv"}:
        raise AssemblyError("coverage source must be .srt, .tsv, or .csv")
    delimiter = "\t" if suffix == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fields = set(reader.fieldnames or [])
        id_column = "cue_id" if "cue_id" in fields else "source_index" if "source_index" in fields else None
        if not id_column or not {"start", "end", "text"}.issubset(fields):
            raise AssemblyError(
                "coverage table requires cue_id (or source_index), start, end, and text columns"
            )
        cues = []
        for row_number, row in enumerate(reader, start=2):
            cue_id = nfc(row.get(id_column))
            if not cue_id:
                raise AssemblyError(f"empty coverage cue_id at row {row_number}")
            cues.append({
                "cue_id": cue_id,
                "start": parse_timecode(row.get("start")),
                "end": parse_timecode(row.get("end")),
                "text": nfc(row.get("text")),
            })
    if not cues:
        raise AssemblyError(f"coverage source has no cues: {path}")
    return cues


def expand_cue_ids(value: object) -> list[str]:
    """Expand comma lists and integer ranges such as 1,2,5-7."""
    result = []
    for token in re.split(r"[,、;\s]+", nfc(value)):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if match:
            start, end = map(int, match.groups())
            if end < start:
                raise AssemblyError(f"descending cue_id range is not allowed: {token}")
            result.extend(str(number) for number in range(start, end + 1))
        else:
            result.append(token)
    return result


def audit_plan_coverage(rows: list[dict], source: Path) -> dict:
    cues = parse_coverage_source(source)
    expected = [nfc(cue["cue_id"]) for cue in cues]
    if len(expected) != len(set(expected)):
        raise AssemblyError("coverage source contains duplicate cue_id values")
    covered = []
    for row_number, row in enumerate(rows, start=2):
        ids = expand_cue_ids(row.get("cue_id"))
        if not ids:
            raise AssemblyError(f"plan row {row_number} has no cue_id")
        covered.extend(ids)
    counts = defaultdict(int)
    for cue_id in covered:
        counts[cue_id] += 1
    expected_set = set(expected)
    missing = [cue_id for cue_id in expected if counts[cue_id] == 0]
    duplicates = sorted(cue_id for cue_id, count in counts.items() if count > 1)
    unknown = sorted(cue_id for cue_id in counts if cue_id not in expected_set)
    audit = {
        "status": "passed" if not (missing or duplicates or unknown) else "failed",
        "source": str(source),
        "source_sha256": file_sha256(source),
        "expected_cue_count": len(expected),
        "covered_cue_count": len(set(covered) & expected_set),
        "missing_cue_ids": missing,
        "duplicate_cue_ids": duplicates,
        "unknown_cue_ids": unknown,
        "source_last_end": max(cue["end"] for cue in cues),
    }
    if audit["status"] != "passed":
        raise AssemblyError(
            "coverage gate failed: "
            f"missing={missing or 'none'}, duplicates={duplicates or 'none'}, unknown={unknown or 'none'}"
        )
    return audit


def parse_cue_tsv(path: Path, episode: str | None, role: str | None) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"start", "end", "text"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise AssemblyError(f"cue TSV requires columns: {','.join(sorted(required))}")
        rows = []
        for row in reader:
            if episode and nfc(row.get("episode")) != episode:
                continue
            if role and nfc(row.get("role")) != nfc(role):
                continue
            rows.append({
                "cue_id": nfc(row.get("source_index") or len(rows) + 1),
                "start": parse_timecode(row["start"]),
                "end": parse_timecode(row["end"]),
                "role": nfc(row.get("role")),
                "semantic_unit": nfc(row.get("semantic_unit")),
                "text": nfc(row.get("text")),
            })
    if not rows:
        raise AssemblyError("no cue rows matched episode/role filters")
    return rows


def group_cues(cues: list[dict], use_semantic_units: bool) -> list[dict]:
    if not use_semantic_units:
        return cues
    grouped = []
    for cue in cues:
        key = (cue["semantic_unit"], cue["role"])
        if key[0] and grouped and grouped[-1]["group_key"] == key:
            grouped[-1]["end"] = max(grouped[-1]["end"], cue["end"])
            grouped[-1]["cue_id"] += f",{cue['cue_id']}"
            grouped[-1]["text"] += " / " + cue["text"]
        else:
            item = dict(cue)
            item["group_key"] = key
            grouped.append(item)
    for item in grouped:
        item.pop("group_key", None)
    return grouped


MATCH_COLUMNS = [
    "status", "cue_id", "role", "text", "source_active_start",
    "source_active_end", "source_transcript", "content_start_evidence",
    "content_end_evidence", "asr_alignment_evidence", "waveform_evidence",
    "match_method", "match_confidence", "source_evidence", "reviewed_by", "notes",
]

CONTENT_MATCH_METHODS = {
    "multimodal_listen",
    "asr_forced_alignment",
    "asr_plus_multimodal",
    "manual_review",
}


def write_match_template(args: argparse.Namespace) -> int:
    cues = parse_cue_tsv(Path(args.cue_tsv).resolve(), args.episode, args.role)
    cues = group_cues(cues, args.group_semantic_units)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise AssemblyError(f"refusing to overwrite existing match sheet: {output}")
    rows = []
    for cue in cues:
        rows.append({
            "status": "review",
            "cue_id": cue["cue_id"],
            "role": cue["role"],
            "text": cue["text"],
            "source_active_start": "",
            "source_active_end": "",
            "source_transcript": "",
            "content_start_evidence": "",
            "content_end_evidence": "",
            "asr_alignment_evidence": "",
            "waveform_evidence": "",
            "match_method": "",
            "match_confidence": "",
            "source_evidence": "",
            "reviewed_by": "",
            "notes": (
                "listen to the source reel and match spoken content; record the first and last "
                "audible token/nonverbal in content_start_evidence/content_end_evidence; "
                "record ASR/forced-alignment and waveform-region evidence separately; "
                "neither text matching nor a silent cut point alone proves complete content"
            ),
        })
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "status": "review_required",
        "cue_count": len(rows),
        "output": str(output),
    }, ensure_ascii=False))
    return 0


def read_source_matches(path: Path) -> dict[str, dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "status", "cue_id", "source_active_start", "source_active_end",
            "source_transcript", "content_start_evidence", "content_end_evidence",
            "asr_alignment_evidence", "waveform_evidence", "match_method",
            "match_confidence", "source_evidence", "reviewed_by",
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise AssemblyError(
                "source match TSV is missing required columns; create it with match-template"
            )
        matches = {}
        for row_number, row in enumerate(reader, start=2):
            cue_id = nfc(row.get("cue_id"))
            if not cue_id:
                raise AssemblyError(f"empty cue_id in source match TSV row {row_number}")
            if cue_id in matches:
                raise AssemblyError(f"duplicate cue_id in source match TSV: {cue_id}")
            matches[cue_id] = {key: nfc(value) for key, value in row.items() if key is not None}
    if not matches:
        raise AssemblyError(f"source match TSV has no rows: {path}")
    return matches


def validate_source_match(
    match: dict,
    cue_id: str,
    audio_duration: float,
    min_confidence: float,
) -> tuple[dict | None, list[str]]:
    problems = []
    try:
        active_start = parse_timecode(match.get("source_active_start"))
        active_end = parse_timecode(match.get("source_active_end"))
    except AssemblyError as exc:
        return None, [str(exc)]
    if active_end <= active_start:
        problems.append("source_active_end must be later than source_active_start")
    if active_end > audio_duration + 1e-6:
        problems.append("source range exceeds audio duration")
    method = nfc(match.get("match_method"))
    if method not in CONTENT_MATCH_METHODS:
        problems.append(f"unsupported match_method: {method or '<blank>'}")
    try:
        confidence = float(nfc(match.get("match_confidence")))
    except ValueError:
        confidence = -1.0
        problems.append("match_confidence must be a number from 0 to 1")
    if not 0.0 <= confidence <= 1.0:
        if confidence != -1.0:
            problems.append("match_confidence must be between 0 and 1")
    elif confidence < min_confidence:
        problems.append(
            f"match_confidence {confidence:.3f} is below threshold {min_confidence:.3f}"
        )
    if nfc(match.get("status")).lower() not in {"planned", "accepted"}:
        problems.append("source match status is not accepted/planned")
    if not nfc(match.get("source_transcript")):
        problems.append("source_transcript is required; use [nonverbal] for reactions")
    start_evidence = nfc(match.get("content_start_evidence"))
    end_evidence = nfc(match.get("content_end_evidence"))
    if len(start_evidence) < 8:
        problems.append(
            "content_start_evidence must identify the earliest audible token/nonverbal "
            "and verification method; a silent cut point is insufficient"
        )
    if len(end_evidence) < 8:
        problems.append(
            "content_end_evidence must identify the latest audible token/nonverbal "
            "and verification method; a silent cut point is insufficient"
        )
    asr_evidence = nfc(match.get("asr_alignment_evidence"))
    waveform_evidence = nfc(match.get("waveform_evidence"))
    if len(asr_evidence) < 8:
        problems.append(
            "asr_alignment_evidence must record Japanese ASR/forced-alignment words, "
            "timestamps, and any omitted/uncertain prefix or suffix"
        )
    if len(waveform_evidence) < 8:
        problems.append(
            "waveform_evidence must record the complete waveform activity range and confirm "
            "that no adjacent activity was left unassigned"
        )
    if not nfc(match.get("source_evidence")):
        problems.append("source_evidence is required")
    if not nfc(match.get("reviewed_by")):
        problems.append("reviewed_by is required")
    return {
        "active_start": active_start,
        "active_end": active_end,
        "confidence": confidence,
        "method": method,
        "transcript": nfc(match.get("source_transcript")),
        "content_start_evidence": start_evidence,
        "content_end_evidence": end_evidence,
        "asr_alignment_evidence": asr_evidence,
        "waveform_evidence": waveform_evidence,
        "evidence": nfc(match.get("source_evidence")),
        "reviewed_by": nfc(match.get("reviewed_by")),
        "notes": nfc(match.get("notes")),
    }, problems


def detect_energy_regions(
    data: np.ndarray,
    sample_rate: int,
    threshold_dbfs: float,
    frame_ms: float,
    bridge_ms: float,
    min_active_ms: float,
    pad_ms: float,
) -> list[dict]:
    mono = np.sqrt(np.mean(np.square(data, dtype=np.float64), axis=1))
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    frame_count = int(math.ceil(len(mono) / frame))
    padded = np.pad(mono, (0, frame_count * frame - len(mono)))
    frame_rms = np.sqrt(np.mean(np.square(padded.reshape(frame_count, frame)), axis=1))
    active = frame_rms >= 10.0 ** (threshold_dbfs / 20.0)
    starts = np.where(np.diff(np.pad(active.astype(np.int8), (1, 1))) == 1)[0]
    ends = np.where(np.diff(np.pad(active.astype(np.int8), (1, 1))) == -1)[0]
    bridge_frames = max(0, int(round(bridge_ms / frame_ms)))
    merged = []
    for start, end in zip(starts, ends):
        if merged and start - merged[-1][1] <= bridge_frames:
            merged[-1][1] = end
        else:
            merged.append([int(start), int(end)])
    minimum_frames = max(1, int(round(min_active_ms / frame_ms)))
    pad_samples = int(sample_rate * pad_ms / 1000.0)
    regions = []
    for start_frame, end_frame in merged:
        if end_frame - start_frame < minimum_frames:
            continue
        active_start = start_frame * frame
        active_end = min(len(data), end_frame * frame)
        regions.append({
            "cut_start": max(0, active_start - pad_samples) / sample_rate,
            "active_start": active_start / sample_rate,
            "active_end": active_end / sample_rate,
            "cut_end": min(len(data), active_end + pad_samples) / sample_rate,
        })
    return regions


def read_source_activity_reviews(path: Path | None) -> list[dict]:
    if path is None:
        return []
    if not path.is_file():
        raise AssemblyError(f"source activity review file not found: {path}")
    required = {
        "source_path", "source_sha256", "start", "end", "decision",
        "source_evidence", "reviewed_by",
    }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise AssemblyError(
                "source activity review TSV requires source_path, source_sha256, start, end, "
                "decision, source_evidence, and reviewed_by"
            )
        reviews = []
        for row_number, row in enumerate(reader, start=2):
            source_value = nfc(row.get("source_path"))
            if not source_value:
                raise AssemblyError(f"empty source_path in source activity review row {row_number}")
            source_path = Path(source_value)
            if not source_path.is_absolute():
                source_path = path.parent / source_path
            try:
                start = parse_timecode(row.get("start"))
                end = parse_timecode(row.get("end"))
            except AssemblyError as exc:
                raise AssemblyError(
                    f"invalid source activity review range at row {row_number}: {exc}"
                ) from exc
            if end <= start:
                raise AssemblyError(
                    f"source activity review end must be later than start at row {row_number}"
                )
            reviews.append({
                "source": str(source_path.resolve()),
                "source_sha256": nfc(row.get("source_sha256")).lower(),
                "start": start,
                "end": end,
                "decision": nfc(row.get("decision")),
                "source_evidence": nfc(row.get("source_evidence")),
                "reviewed_by": nfc(row.get("reviewed_by")),
            })
    return reviews


def subtract_covered_ranges(
    start: float,
    end: float,
    covered: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    remaining = [(start, end)]
    for covered_start, covered_end in covered:
        updated = []
        for item_start, item_end in remaining:
            if covered_end <= item_start or covered_start >= item_end:
                updated.append((item_start, item_end))
                continue
            if covered_start > item_start:
                updated.append((item_start, min(item_end, covered_start)))
            if covered_end < item_end:
                updated.append((max(item_start, covered_end), item_end))
        remaining = updated
        if not remaining:
            break
    return remaining


def audit_unassigned_source_activity(
    rows: list[dict],
    cache: dict[str, np.ndarray],
    sample_rate: int,
    review_path: Path | None = None,
    lookaround_ms: float = 2000.0,
    threshold_dbfs: float = -42.0,
    frame_ms: float = 10.0,
    bridge_ms: float = 80.0,
    min_active_ms: float = 60.0,
    coverage_tolerance_ms: float = 40.0,
) -> dict:
    """Block waveform activity near planned cues that no cue source range covers.

    This catches an ASR-selected start that lands after a spoken prefix separated by
    an internal pause. Reviewed NG/extra material can be exempted only with a
    source-hash-bound TSV entry.
    """
    if min(lookaround_ms, frame_ms, min_active_ms, coverage_tolerance_ms) < 0:
        raise AssemblyError("source activity audit timing values must be non-negative")
    if frame_ms <= 0 or min_active_ms <= 0:
        raise AssemblyError("source activity frame and minimum duration must be positive")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        source_value = nfc(row.get("source_path"))
        if not source_value:
            continue
        grouped[str(Path(source_value).resolve())].append(row)
    reviews = read_source_activity_reviews(review_path)
    detections = []
    accepted = []
    unreviewed = []
    tolerance = coverage_tolerance_ms / 1000.0
    minimum = min_active_ms / 1000.0
    lookaround = lookaround_ms / 1000.0

    for source, source_rows in grouped.items():
        if source not in cache:
            raise AssemblyError(f"source activity audit audio is not loaded: {source}")
        data = cache[source]
        duration = len(data) / sample_rate
        cue_ranges = sorted(
            (
                parse_timecode(row.get("source_active_start")),
                parse_timecode(row.get("source_active_end")),
                nfc(row.get("cue_id")) or "<blank>",
            )
            for row in source_rows
        )
        if not cue_ranges:
            continue
        covered = [
            (max(0.0, start - tolerance), min(duration, end + tolerance))
            for start, end, _ in cue_ranges
        ]
        context_start = max(0.0, cue_ranges[0][0] - lookaround)
        context_end = min(duration, max(end for _, end, _ in cue_ranges) + lookaround)
        regions = detect_energy_regions(
            data, sample_rate, threshold_dbfs, frame_ms, bridge_ms,
            min_active_ms, 0.0,
        )
        source_hash = None
        for region in regions:
            region_start = max(context_start, region["active_start"])
            region_end = min(context_end, region["active_end"])
            if region_end - region_start < minimum:
                continue
            for gap_start, gap_end in subtract_covered_ranges(region_start, region_end, covered):
                if gap_end - gap_start < minimum:
                    continue
                previous_cue = next(
                    (cue_id for start, end, cue_id in reversed(cue_ranges) if end <= gap_start + tolerance),
                    "",
                )
                next_cue = next(
                    (cue_id for start, end, cue_id in cue_ranges if start >= gap_end - tolerance),
                    "",
                )
                if source_hash is None:
                    source_hash = sha256_file(Path(source))
                detection = {
                    "source": source,
                    "source_sha256": source_hash,
                    "start": round(gap_start, 6),
                    "end": round(gap_end, 6),
                    "duration_ms": round((gap_end - gap_start) * 1000.0, 3),
                    "previous_cue": previous_cue,
                    "next_cue": next_cue,
                }
                detections.append(detection)
                matched_review = next((
                    review for review in reviews
                    if review["source"] == source
                    and review["source_sha256"] == source_hash.lower()
                    and review["start"] <= gap_start + tolerance
                    and review["end"] >= gap_end - tolerance
                    and review["decision"] == "ng_or_extra"
                    and len(review["source_evidence"]) >= 12
                    and review["reviewed_by"]
                ), None)
                if matched_review:
                    accepted.append({
                        **detection,
                        "decision": matched_review["decision"],
                        "source_evidence": matched_review["source_evidence"],
                        "reviewed_by": matched_review["reviewed_by"],
                    })
                else:
                    unreviewed.append(detection)
    return {
        "status": "needs_review" if unreviewed else "passed",
        "lookaround_ms": lookaround_ms,
        "threshold_dbfs": threshold_dbfs,
        "frame_ms": frame_ms,
        "bridge_ms": bridge_ms,
        "min_active_ms": min_active_ms,
        "coverage_tolerance_ms": coverage_tolerance_ms,
        "detected_count": len(detections),
        "accepted_ng_or_extra_count": len(accepted),
        "unreviewed_count": len(unreviewed),
        "detections": detections,
        "accepted": accepted,
        "unreviewed": unreviewed,
    }


def window_rms_dbfs(data: np.ndarray) -> float:
    if not data.size:
        return -240.0
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    return dbfs(rms)


def inspect_source_boundaries(
    data: np.ndarray,
    sample_rate: int,
    cue_id: str,
    cut_start: float,
    active_start: float,
    active_end: float,
    cut_end: float,
    probe_ms: float,
    absolute_dbfs: float,
    relative_drop_db: float,
    min_safety_ms: float,
) -> dict:
    """Conservatively reject cuts whose edges still resemble active speech.

    This is a deterministic safety gate, not a speech recognizer. A flagged row
    must be re-cut at a reviewed pause or merged with its continuous neighbour.
    Manual timing never waives an unsafe source boundary.
    """
    if probe_ms <= 0 or relative_drop_db < 0 or min_safety_ms < 0:
        raise AssemblyError("boundary probe, relative drop, and safety values must be non-negative")
    duration = len(data) / sample_rate
    if not 0.0 <= cut_start <= active_start < active_end <= cut_end <= duration + 1e-6:
        raise AssemblyError(
            f"cue {cue_id} has invalid source boundaries: require "
            "0 <= cut_start <= active_start < active_end <= cut_end <= source duration"
        )
    cut_start_sample = max(0, int(round(cut_start * sample_rate)))
    cut_end_sample = min(len(data), int(round(cut_end * sample_rate)))
    active_start_sample = max(cut_start_sample, int(round(active_start * sample_rate)))
    active_end_sample = min(cut_end_sample, int(round(active_end * sample_rate)))
    probe = max(1, int(round(probe_ms * sample_rate / 1000.0)))
    active_rms = window_rms_dbfs(data[active_start_sample:active_end_sample])
    head_rms = window_rms_dbfs(data[cut_start_sample:min(cut_end_sample, cut_start_sample + probe)])
    tail_rms = window_rms_dbfs(data[max(cut_start_sample, cut_end_sample - probe):cut_end_sample])
    pre_roll_ms = max(0.0, (active_start - cut_start) * 1000.0)
    post_roll_ms = max(0.0, (cut_end - active_end) * 1000.0)
    at_source_start = cut_start_sample == 0
    at_source_end = cut_end_sample >= len(data)
    problems: list[str] = []
    warnings: list[str] = []

    if not at_source_start and pre_roll_ms + 0.5 < min_safety_ms:
        problems.append(
            f"pre-roll {pre_roll_ms:.1f} ms is shorter than required {min_safety_ms:.1f} ms"
        )
    if not at_source_end and post_roll_ms + 0.5 < min_safety_ms:
        problems.append(
            f"post-roll {post_roll_ms:.1f} ms is shorter than required {min_safety_ms:.1f} ms"
        )

    edge_limit = max(absolute_dbfs, active_rms - relative_drop_db)
    if not at_source_start and head_rms >= edge_limit:
        problems.append(
            f"cut start edge remains active ({head_rms:.1f} dBFS; safe limit {edge_limit:.1f} dBFS)"
        )
    if not at_source_end and tail_rms >= edge_limit:
        problems.append(
            f"cut end edge remains active ({tail_rms:.1f} dBFS; safe limit {edge_limit:.1f} dBFS)"
        )
    if at_source_start and head_rms >= edge_limit:
        warnings.append("active speech reaches the physical start of the source; no fade-in will be applied")
    if at_source_end and tail_rms >= edge_limit:
        problems.append("active speech reaches the physical end of the source; recording may already be truncated")

    return {
        "cue_id": cue_id,
        "status": "unsafe" if problems else "safe",
        "source_duration": duration,
        "cut_start": cut_start,
        "active_start": active_start,
        "active_end": active_end,
        "cut_end": cut_end,
        "pre_roll_ms": pre_roll_ms,
        "post_roll_ms": post_roll_ms,
        "active_rms_dbfs": active_rms,
        "head_probe_rms_dbfs": head_rms,
        "tail_probe_rms_dbfs": tail_rms,
        "edge_safe_limit_dbfs": edge_limit,
        "problems": problems,
        "warnings": warnings,
    }


def apply_margin_fades(
    clip: np.ndarray,
    sample_rate: int,
    fade_ms: float,
    pre_roll_seconds: float,
    post_roll_seconds: float,
) -> tuple[np.ndarray, int, int]:
    """Apply fades only inside reviewed safety margins, never over active speech."""
    requested = max(0, int(round(fade_ms * sample_rate / 1000.0)))
    pre_samples = max(0, int(math.floor(pre_roll_seconds * sample_rate)))
    post_samples = max(0, int(math.floor(post_roll_seconds * sample_rate)))
    fade_in = min(requested, pre_samples, len(clip) // 2)
    fade_out = min(requested, post_samples, len(clip) // 2)
    if fade_in:
        clip[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)[:, None]
    if fade_out:
        clip[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, endpoint=False)[:, None]
    return clip, fade_in, fade_out


PLAN_COLUMNS = [
    "status", "source_path", "source_cut_start", "source_active_start",
    "source_active_end", "source_cut_end", "target_start", "target_end",
    "cue_id", "role", "text", "timing_policy", "overlap_evidence",
    "hard_sync", "source_transcript", "source_match_method",
    "source_match_confidence", "content_start_evidence", "content_end_evidence",
    "asr_alignment_evidence", "waveform_evidence", "source_evidence",
    "reviewed_by", "notes",
]


def plan_reel(args: argparse.Namespace) -> int:
    audio = Path(args.audio).resolve()
    ffmpeg = find_ffmpeg(args.ffmpeg)
    data = read_audio(audio, args.sample_rate, ffmpeg)
    if args.cue_tsv:
        cues = parse_cue_tsv(Path(args.cue_tsv).resolve(), args.episode, args.role)
    elif args.srt:
        if args.role:
            raise AssemblyError("--role requires --cue-tsv because SRT has no speaker column")
        cues = parse_srt(Path(args.srt).resolve())
    else:
        raise AssemblyError("pass --cue-tsv or --srt")
    cues = group_cues(cues, args.group_semantic_units)
    audio_duration = len(data) / args.sample_rate
    source_matches = None
    if args.source_match_tsv:
        source_matches = read_source_matches(Path(args.source_match_tsv).resolve())
        expected_ids = {cue["cue_id"] for cue in cues}
        extra_ids = sorted(set(source_matches) - expected_ids)
        if extra_ids:
            raise AssemblyError(f"source match TSV contains unexpected cue_id values: {extra_ids}")
        regions = []
    else:
        regions = detect_energy_regions(
            data, args.sample_rate, args.threshold_dbfs, args.frame_ms,
            args.bridge_ms, args.min_active_ms, args.pad_ms,
        )
    rows = []
    total = len(cues) if source_matches is not None else max(len(regions), len(cues))
    prior_nominal_end = 0.0
    prior_source_end = 0.0
    for index in range(total):
        cue = cues[index] if index < len(cues) else None
        match = source_matches.get(cue["cue_id"]) if source_matches is not None and cue else None
        match_data = None
        match_problems = []
        if match is not None:
            match_data, match_problems = validate_source_match(
                match, cue["cue_id"], audio_duration, args.min_match_confidence
            )
        if source_matches is not None:
            if match is None:
                match_problems.append("cue is missing from source match TSV")
            if match_data is not None and match_data["active_start"] < prior_source_end - 1e-6:
                match_problems.append("source ranges overlap or are not in script order")
            if match_data is not None:
                prior_source_end = max(prior_source_end, match_data["active_end"])
                pad = args.pad_ms / 1000.0
                region = {
                    "cut_start": max(0.0, match_data["active_start"] - pad),
                    "active_start": match_data["active_start"],
                    "active_end": match_data["active_end"],
                    "cut_end": min(audio_duration, match_data["active_end"] + pad),
                }
            else:
                region = None
        else:
            region = regions[index] if index < len(regions) else None
        nominal_overlap = bool(cue and index > 0 and cue["start"] < prior_nominal_end)
        content_matched = source_matches is not None and match_data is not None and not match_problems
        status = "planned" if content_matched and region and cue and not nominal_overlap else "review"
        if nominal_overlap:
            notes = "nominal cue overlap; inspect original video/audio before choosing timing policy"
            timing_policy = "review_original"
            overlap_evidence = "nominal cue timecodes overlap"
        elif status == "planned":
            notes = "spoken content matched and reviewed"
            timing_policy = "normal_push"
            overlap_evidence = ""
        elif source_matches is not None:
            notes = "; ".join(match_problems) or "source content match requires review"
            timing_policy = "review_original"
            overlap_evidence = ""
        elif len(regions) == len(cues) and region and cue:
            notes = "energy candidate only; verify spoken content and fill source evidence before rendering"
            timing_policy = "normal_push"
            overlap_evidence = ""
        else:
            notes = "segment/cue count mismatch; do not render"
            timing_policy = "review_original"
            overlap_evidence = ""
        rows.append({
            "status": status,
            "source_path": str(audio) if region else "",
            "source_cut_start": f"{region['cut_start']:.6f}" if region else "",
            "source_active_start": f"{region['active_start']:.6f}" if region else "",
            "source_active_end": f"{region['active_end']:.6f}" if region else "",
            "source_cut_end": f"{region['cut_end']:.6f}" if region else "",
            "target_start": f"{cue['start']:.6f}" if cue else "",
            "target_end": f"{cue['end']:.6f}" if cue else "",
            "cue_id": cue["cue_id"] if cue else "",
            "role": cue["role"] if cue else nfc(args.role),
            "text": cue["text"] if cue else "",
            "timing_policy": timing_policy,
            "overlap_evidence": overlap_evidence,
            "hard_sync": "false",
            "source_transcript": match_data["transcript"] if match_data else "",
            "source_match_method": match_data["method"] if match_data else "energy_order_only",
            "source_match_confidence": f"{match_data['confidence']:.3f}" if match_data else "",
            "content_start_evidence": match_data["content_start_evidence"] if match_data else "",
            "content_end_evidence": match_data["content_end_evidence"] if match_data else "",
            "asr_alignment_evidence": match_data["asr_alignment_evidence"] if match_data else "",
            "waveform_evidence": match_data["waveform_evidence"] if match_data else "",
            "source_evidence": match_data["evidence"] if match_data else "",
            "reviewed_by": match_data["reviewed_by"] if match_data else "",
            "notes": notes,
        })
        if cue:
            prior_nominal_end = max(prior_nominal_end, cue["end"])
    plan = Path(args.plan).resolve()
    source_activity_gate = None
    source_activity_report = None
    if source_matches is not None:
        complete_ranges = bool(rows) and all(
            nfc(row.get("source_path"))
            and nfc(row.get("source_active_start"))
            and nfc(row.get("source_active_end"))
            for row in rows
        )
        if complete_ranges:
            source_activity_gate = source_activity_audit_from_args(
                rows, {str(audio): data}, args
            )
            if source_activity_gate["status"] != "passed":
                affected = {
                    cue_id
                    for item in source_activity_gate["unreviewed"]
                    for cue_id in (item.get("previous_cue"), item.get("next_cue"))
                    if cue_id
                }
                for row in rows:
                    if row["cue_id"] in affected:
                        row["status"] = "review"
                        detail = (
                            "unassigned waveform activity exists adjacent to this cue; "
                            "expand the correct source range or review it as hash-bound ng_or_extra"
                        )
                        row["notes"] = f"{row['notes']}; {detail}" if row["notes"] else detail
        else:
            source_activity_gate = {
                "status": "not_run_incomplete_source_matches",
                "unreviewed_count": None,
            }
        source_activity_report = Path(
            nfc(getattr(args, "source_activity_report", None))
            or f"{plan}.source_activity.json"
        ).resolve()
        write_json(source_activity_report, source_activity_gate)
    plan.parent.mkdir(parents=True, exist_ok=True)
    if plan.exists():
        raise AssemblyError(f"refusing to overwrite existing plan: {plan}")
    with plan.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "status": "ready" if rows and all(r["status"] == "planned" for r in rows) else "review_required",
        "matching_mode": "spoken_content" if source_matches is not None else "energy_candidates_only",
        "audio": str(audio),
        "cue_count": len(cues),
        "detected_segment_count": len(regions) if source_matches is None else None,
        "source_match_count": len(source_matches) if source_matches is not None else 0,
        "source_activity_gate": source_activity_gate["status"] if source_activity_gate else None,
        "source_activity_report": str(source_activity_report) if source_activity_report else None,
        "plan": str(plan),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "ready" else 2


def merge_plans(args: argparse.Namespace) -> int:
    plan_dir = Path(args.plan_dir).resolve()
    paths = sorted(p for p in plan_dir.glob("*.tsv") if not p.name.startswith((".", "._")))
    if not paths:
        raise AssemblyError(f"no role/combined plans found: {plan_dir}")
    rows = []
    seen = set()
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for source_row in csv.DictReader(handle, delimiter="\t"):
                row = {column: nfc(source_row.get(column)) for column in PLAN_COLUMNS}
                fingerprint = (
                    row["source_path"], row["source_cut_start"], row["source_cut_end"],
                    row["target_start"], row["cue_id"], row["role"],
                )
                if fingerprint in seen:
                    raise AssemblyError(f"duplicate plan row while merging: {fingerprint}")
                seen.add(fingerprint)
                row["timing_policy"] = row["timing_policy"] or "normal_push"
                row["hard_sync"] = row["hard_sync"] or "false"
                rows.append(row)
    rows.sort(key=lambda row: (float(row["target_start"] or "inf"), row["cue_id"], row["role"]))
    prior_nominal_end = 0.0
    for row in rows:
        if float(row["target_start"]) < prior_nominal_end:
            if row["timing_policy"] == "normal_push":
                row["status"] = "review"
                row["timing_policy"] = "review_original"
                row["overlap_evidence"] = row["overlap_evidence"] or "nominal cue timecodes overlap"
                row["notes"] = "inspect original video/audio and complete semantic unit"
        prior_nominal_end = max(prior_nominal_end, float(row["target_end"]))
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise AssemblyError(f"refusing to overwrite merged plan: {output}")
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    attention = sum(
        row["status"] != "planned" or row["timing_policy"] == "review_original"
        for row in rows
    )
    summary = {
        "status": "ready" if attention == 0 else "review_required",
        "input_plan_count": len(paths),
        "row_count": len(rows),
        "attention_count": attention,
        "output": str(output),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if attention == 0 else 2


def resolve_job_path(value: str, base: Path) -> str | None:
    text = nfc(value)
    if not text:
        return None
    path = Path(text)
    return str((path if path.is_absolute() else base / path).resolve())


def plan_batch(args: argparse.Namespace) -> int:
    jobs_path = Path(args.jobs).resolve()
    plan_dir = Path(args.plan_dir).resolve()
    plan_dir.mkdir(parents=True, exist_ok=True)
    with jobs_path.open("r", encoding="utf-8-sig", newline="") as handle:
        jobs = list(csv.DictReader(handle, delimiter="\t"))
    required = {"job_id", "audio"}
    if not jobs or not required.issubset(jobs[0]):
        raise AssemblyError("jobs TSV requires at least job_id and audio columns")
    results = []
    base = jobs_path.parent
    for row in jobs:
        job_id = nfc(row.get("job_id"))
        if not job_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            results.append({"job_id": job_id, "status": "failed", "error": "invalid job_id"})
            continue
        plan_path = plan_dir / f"{job_id}.tsv"
        job_args = argparse.Namespace(
            audio=resolve_job_path(row.get("audio", ""), base),
            cue_tsv=resolve_job_path(row.get("cue_tsv", ""), base),
            srt=resolve_job_path(row.get("srt", ""), base),
            episode=nfc(row.get("episode")) or None,
            role=nfc(row.get("role")) or None,
            group_semantic_units=nfc(row.get("group_semantic_units")).lower() in {"1", "true", "yes", "y"},
            source_match_tsv=resolve_job_path(row.get("source_match_tsv", ""), base),
            min_match_confidence=args.min_match_confidence,
            plan=str(plan_path),
            sample_rate=args.sample_rate,
            threshold_dbfs=args.threshold_dbfs,
            frame_ms=args.frame_ms,
            bridge_ms=args.bridge_ms,
            min_active_ms=args.min_active_ms,
            pad_ms=args.pad_ms,
            source_activity_review=(
                resolve_job_path(row.get("source_activity_review", ""), base)
                or args.source_activity_review
            ),
            source_activity_report=str(plan_path) + ".source_activity.json",
            source_activity_lookaround_ms=args.source_activity_lookaround_ms,
            source_activity_threshold_dbfs=args.source_activity_threshold_dbfs,
            source_activity_frame_ms=args.source_activity_frame_ms,
            source_activity_bridge_ms=args.source_activity_bridge_ms,
            source_activity_min_ms=args.source_activity_min_ms,
            source_activity_coverage_tolerance_ms=args.source_activity_coverage_tolerance_ms,
            ffmpeg=args.ffmpeg,
        )
        try:
            code = plan_reel(job_args)
            results.append({
                "job_id": job_id,
                "status": "ready" if code == 0 else "review_required",
                "plan": str(plan_path),
            })
        except (AssemblyError, OSError, sf.LibsndfileError, ValueError) as exc:
            results.append({"job_id": job_id, "status": "failed", "error": str(exc)})
    failed = [r for r in results if r["status"] != "ready"]
    summary = {
        "status": "ready" if not failed else "review_required",
        "job_count": len(results),
        "ready_count": len(results) - len(failed),
        "attention_count": len(failed),
        "jobs": results,
    }
    write_json(Path(args.summary).resolve(), summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "jobs"}, ensure_ascii=False))
    return 0 if not failed else 2


def make_auto_manual_args(args: argparse.Namespace, plan: Path) -> argparse.Namespace:
    output = Path(args.output).resolve()
    return argparse.Namespace(
        plan=str(plan),
        manual_cues=None,
        episode=output.stem,
        out_dir=str(output.parent),
        episode_duration=None,
        fade_ms=args.fade_ms,
        tail_pad=args.tail_pad,
        min_gap_ms=args.min_gap_ms,
        max_auto_delay_ms=args.max_auto_delay_ms,
        min_match_confidence=args.min_match_confidence,
        boundary_probe_ms=args.boundary_probe_ms,
        boundary_absolute_dbfs=args.boundary_absolute_dbfs,
        boundary_relative_drop_db=args.boundary_relative_drop_db,
        min_safety_ms=args.min_safety_ms,
        source_activity_review=args.source_activity_review,
        source_activity_lookaround_ms=args.source_activity_lookaround_ms,
        source_activity_threshold_dbfs=args.source_activity_threshold_dbfs,
        source_activity_frame_ms=args.source_activity_frame_ms,
        source_activity_bridge_ms=args.source_activity_bridge_ms,
        source_activity_min_ms=args.source_activity_min_ms,
        source_activity_coverage_tolerance_ms=args.source_activity_coverage_tolerance_ms,
        sample_rate=args.sample_rate,
        subtype=args.subtype,
        target_peak=args.target_peak,
        ffmpeg=args.ffmpeg,
        qc_report=None,
    )


def require_content_boundary_evidence(row: dict, cue_id: str) -> dict[str, str]:
    """Require an explicit content-completeness check in addition to quiet cut edges."""
    start_evidence = nfc(row.get("content_start_evidence"))
    end_evidence = nfc(row.get("content_end_evidence"))
    if len(start_evidence) < 8:
        raise AssemblyError(
            f"cue {cue_id} requires content_start_evidence identifying the earliest audible "
            "token/nonverbal and how it was verified; silence at the cut is not proof"
        )
    if len(end_evidence) < 8:
        raise AssemblyError(
            f"cue {cue_id} requires content_end_evidence identifying the latest audible "
            "token/nonverbal and how it was verified; silence at the cut is not proof"
        )
    return {
        "status": "passed",
        "content_start_evidence": start_evidence,
        "content_end_evidence": end_evidence,
    }


def require_joint_detection_evidence(row: dict, cue_id: str) -> dict[str, str]:
    """Require independent ASR/alignment and waveform evidence for source discovery."""
    asr_evidence = nfc(row.get("asr_alignment_evidence"))
    waveform_evidence = nfc(row.get("waveform_evidence"))
    if len(asr_evidence) < 8:
        raise AssemblyError(
            f"cue {cue_id} requires asr_alignment_evidence with Japanese ASR/forced-alignment "
            "words, timestamps, and uncertainty notes"
        )
    if len(waveform_evidence) < 8:
        raise AssemblyError(
            f"cue {cue_id} requires waveform_evidence covering the full actor utterance and "
            "adjacent source activity"
        )
    return {
        "status": "passed",
        "asr_alignment_evidence": asr_evidence,
        "waveform_evidence": waveform_evidence,
    }


def source_activity_audit_from_args(
    rows: list[dict],
    cache: dict[str, np.ndarray],
    args: argparse.Namespace,
) -> dict:
    review_value = nfc(getattr(args, "source_activity_review", None))
    return audit_unassigned_source_activity(
        rows,
        cache,
        args.sample_rate,
        Path(review_value).resolve() if review_value else None,
        args.source_activity_lookaround_ms,
        args.source_activity_threshold_dbfs,
        args.source_activity_frame_ms,
        args.source_activity_bridge_ms,
        args.source_activity_min_ms,
        args.source_activity_coverage_tolerance_ms,
    )


def render_plan(args: argparse.Namespace) -> int:
    plan = Path(args.plan).resolve()
    with plan.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise AssemblyError("plan contains no rows")
    coverage_source = Path(args.coverage_source).resolve()
    coverage_audit = audit_plan_coverage(rows, coverage_source)
    unresolved = [row for row in rows if nfc(row.get("status")) != "planned"]
    if unresolved:
        return render_manual_stems(make_auto_manual_args(args, plan))
    policies = [nfc(row.get("timing_policy")) or "normal_push" for row in rows]
    if any(policy not in {"normal_push", "intentional_overlap"} for policy in policies):
        return render_manual_stems(make_auto_manual_args(args, plan))
    for row, policy in zip(rows, policies):
        cue_id = nfc(row.get("cue_id")) or "<blank>"
        require_content_boundary_evidence(row, cue_id)
        require_joint_detection_evidence(row, cue_id)
        if policy == "intentional_overlap" and not nfc(row.get("overlap_evidence")):
            raise AssemblyError("intentional_overlap requires overlap_evidence from original material")
        if policy == "intentional_overlap" and not nfc(row.get("reviewed_by")):
            raise AssemblyError("intentional_overlap requires reviewed_by")
        match_method = nfc(row.get("source_match_method"))
        if match_method == "energy_order_only":
            raise AssemblyError(
                "energy_order_only is never valid for rendering; match every waveform region "
                "with Japanese ASR/forced alignment and reviewed spoken content"
            )
        elif match_method:
            if match_method not in CONTENT_MATCH_METHODS:
                raise AssemblyError(f"unsupported source_match_method: {match_method}")
            if not nfc(row.get("source_transcript")):
                raise AssemblyError("content-matched plan row requires source_transcript")
            if not nfc(row.get("source_evidence")) or not nfc(row.get("reviewed_by")):
                raise AssemblyError("content-matched plan row requires source_evidence and reviewed_by")
            try:
                confidence = float(nfc(row.get("source_match_confidence")))
            except ValueError as exc:
                raise AssemblyError("content-matched plan row requires numeric source_match_confidence") from exc
            if not 0.0 <= confidence <= 1.0:
                raise AssemblyError("source_match_confidence must be between 0 and 1")
    ffmpeg = find_ffmpeg(args.ffmpeg)
    cache: dict[str, np.ndarray] = {}
    for row in rows:
        source = str(Path(row["source_path"]).resolve())
        if source not in cache:
            cache[source] = read_audio(Path(source), args.sample_rate, ffmpeg)
    source_activity_gate = source_activity_audit_from_args(rows, cache, args)
    if source_activity_gate["status"] != "passed":
        first = source_activity_gate["unreviewed"][0]
        raise AssemblyError(
            "unassigned source waveform activity blocks rendering: "
            f"{first['source']} {first['start']:.3f}-{first['end']:.3f}s, "
            f"between cues {first['previous_cue'] or '<start>'} and "
            f"{first['next_cue'] or '<end>'}; extend the correct cue range or provide "
            "a source-hash-bound ng_or_extra review"
        )
    placements = []
    rows = sorted(rows, key=lambda row: (float(row["target_start"]), nfc(row.get("cue_id"))))
    output_end = coverage_audit["source_last_end"]
    previous_active_end = 0.0
    schedule = []
    boundary_checks = []
    for row in rows:
        source = str(Path(row["source_path"]).resolve())
        if source not in cache:
            cache[source] = read_audio(Path(source), args.sample_rate, ffmpeg)
        cut_start = float(row["source_cut_start"])
        active_start = float(row["source_active_start"])
        active_end = float(row["source_active_end"])
        cut_end = float(row["source_cut_end"])
        cue_id = nfc(row.get("cue_id")) or "<blank>"
        boundary = inspect_source_boundaries(
            cache[source], args.sample_rate, cue_id,
            cut_start, active_start, active_end, cut_end,
            args.boundary_probe_ms, args.boundary_absolute_dbfs,
            args.boundary_relative_drop_db, args.min_safety_ms,
        )
        if boundary["status"] != "safe":
            raise AssemblyError(
                f"unsafe source boundary at cue {cue_id}: " + "; ".join(boundary["problems"])
            )
        boundary["content_boundary_gate"] = require_content_boundary_evidence(row, cue_id)
        boundary["joint_detection_gate"] = require_joint_detection_evidence(row, cue_id)
        boundary_checks.append(boundary)
        target_start = float(row["target_start"])
        pre_roll = active_start - cut_start
        clip_duration = cut_end - cut_start
        policy = nfc(row.get("timing_policy")) or "normal_push"
        if policy == "intentional_overlap":
            actual_active_start = target_start
        else:
            actual_active_start = max(target_start, previous_active_end + args.min_gap_ms / 1000.0)
        delay = actual_active_start - target_start
        if nfc(row.get("hard_sync")).lower() in {"1", "true", "yes", "y"} and delay > 1e-6:
            return render_manual_stems(make_auto_manual_args(args, plan))
        if delay * 1000.0 > args.max_auto_delay_ms:
            return render_manual_stems(make_auto_manual_args(args, plan))
        place_start = actual_active_start - pre_roll
        actual_clip_end = place_start + clip_duration
        actual_active_end = actual_active_start + (active_end - active_start)
        previous_active_end = max(previous_active_end, actual_active_end)
        output_end = max(output_end, actual_clip_end, float(row["target_end"]))
        placements.append((row, source, cut_start, cut_end, place_start))
        schedule.append({
            "cue_id": nfc(row.get("cue_id")),
            "role": nfc(row.get("role")),
            "source_transcript": nfc(row.get("source_transcript")),
            "source_match_method": nfc(row.get("source_match_method")),
            "source_match_confidence": nfc(row.get("source_match_confidence")),
            "content_start_evidence": nfc(row.get("content_start_evidence")),
            "content_end_evidence": nfc(row.get("content_end_evidence")),
            "asr_alignment_evidence": nfc(row.get("asr_alignment_evidence")),
            "waveform_evidence": nfc(row.get("waveform_evidence")),
            "source_evidence": nfc(row.get("source_evidence")),
            "reviewed_by": nfc(row.get("reviewed_by")),
            "timing_policy": policy,
            "target_start": target_start,
            "actual_active_start": actual_active_start,
            "actual_active_end": actual_active_end,
            "actual_clip_end": actual_clip_end,
            "delay_ms": delay * 1000.0,
            "delay_warning": delay * 1000.0 >= args.warn_delay_ms,
        })
    mixed = np.zeros((int(math.ceil((output_end + args.tail_pad) * args.sample_rate)), 2), dtype=np.float32)
    for row, source, cut_start, cut_end, place_start in placements:
        data = cache[source]
        source_start = max(0, int(round(cut_start * args.sample_rate)))
        source_end = min(len(data), int(round(cut_end * args.sample_rate)))
        clip = data[source_start:source_end].copy()
        active_start = float(row["source_active_start"])
        active_end = float(row["source_active_end"])
        clip, _, _ = apply_margin_fades(
            clip, args.sample_rate, args.fade_ms,
            active_start - cut_start, cut_end - active_end,
        )
        target_sample = int(round(place_start * args.sample_rate))
        if target_sample < 0:
            clip = clip[-target_sample:]
            target_sample = 0
        mixed[target_sample : target_sample + len(clip)] += clip
    mixed, _ = normalize_peak(mixed, args.target_peak)
    output = Path(args.output).resolve()
    write_audio(output, mixed, args.sample_rate, args.subtype)
    loudness = measure_loudness(output, ffmpeg)
    overlap_samples = np.zeros(len(mixed), dtype=np.uint16)
    for _, _, cut_start, cut_end, place_start in placements:
        start = max(0, int(round(place_start * args.sample_rate)))
        end = min(len(mixed), start + int(round((cut_end - cut_start) * args.sample_rate)))
        overlap_samples[start:end] += 1
    qc = {
        "status": "ok",
        "mode": "render-plan",
        "output": str(output),
        "sample_rate": args.sample_rate,
        "channels": 2,
        "subtype": args.subtype,
        "duration": len(mixed) / args.sample_rate,
        "placement_count": len(placements),
        "coverage_gate": coverage_audit,
        "overlap_ratio": float(np.mean(overlap_samples > 1)),
        "delayed_cue_count": sum(item["delay_ms"] > 0.5 for item in schedule),
        "max_delay_ms": max((item["delay_ms"] for item in schedule), default=0.0),
        "delay_warning_count": sum(item["delay_warning"] for item in schedule),
        "schedule": schedule,
        "source_activity_gate": source_activity_gate,
        "zero_truncation_gate": {
            "status": "passed",
            "checked_cue_count": len(boundary_checks),
            "probe_ms": args.boundary_probe_ms,
            "absolute_dbfs": args.boundary_absolute_dbfs,
            "relative_drop_db": args.boundary_relative_drop_db,
            "min_safety_ms": args.min_safety_ms,
            "cues": boundary_checks,
        },
        "metrics": audio_metrics(mixed),
        "loudness": loudness,
        "warnings": [
            "No time-stretching was applied; performance duration was preserved.",
            "Source cut edges and spoken-content head/tail evidence passed the zero-truncation gate; fades were confined to safety margins.",
        ],
    }
    write_json(Path(args.qc_report or f"{output}.qc.json"), qc)
    print(json.dumps(qc, ensure_ascii=False))
    return 0


def read_manual_cues(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames or "cue_id" not in reader.fieldnames:
            raise AssemblyError("manual cue TSV requires a cue_id column")
        cues = {}
        for row_number, row in enumerate(reader, start=2):
            cue_id = nfc(row.get("cue_id"))
            if not cue_id:
                raise AssemblyError(f"empty cue_id in manual cue TSV row {row_number}")
            if cue_id in cues:
                raise AssemblyError(f"duplicate cue_id in manual cue TSV: {cue_id}")
            cues[cue_id] = {
                key: nfc(value) for key, value in row.items() if key is not None
            }
    if not cues:
        raise AssemblyError(f"manual cue TSV has no rows: {path}")
    return cues


def validate_experimental_source_row(
    row: dict,
    min_match_confidence: float,
) -> tuple[str, float, float, float, float]:
    cue_id = nfc(row.get("cue_id")) or "<blank>"
    source_value = nfc(row.get("source_path"))
    if not source_value:
        raise AssemblyError(f"cue {cue_id} has no source_path")
    source = str(Path(source_value).resolve())
    try:
        cut_start = parse_timecode(row.get("source_cut_start"))
        active_start = parse_timecode(row.get("source_active_start"))
        active_end = parse_timecode(row.get("source_active_end"))
        cut_end = parse_timecode(row.get("source_cut_end"))
    except AssemblyError as exc:
        raise AssemblyError(f"invalid source range at cue {cue_id}: {exc}") from exc
    if not cut_start <= active_start < active_end <= cut_end:
        raise AssemblyError(
            f"cue {cue_id} requires source_cut_start <= source_active_start "
            "< source_active_end <= source_cut_end"
        )
    require_content_boundary_evidence(row, cue_id)
    require_joint_detection_evidence(row, cue_id)
    method = nfc(row.get("source_match_method"))
    if method == "energy_order_only":
        raise AssemblyError(
            f"cue {cue_id} is energy-only; manual timing cannot waive joint ASR and waveform matching"
        )
    elif method in CONTENT_MATCH_METHODS:
        if not nfc(row.get("source_transcript")):
            raise AssemblyError(f"cue {cue_id} requires source_transcript")
        if not nfc(row.get("source_evidence")) or not nfc(row.get("reviewed_by")):
            raise AssemblyError(f"cue {cue_id} requires source evidence and reviewed_by")
        try:
            confidence = float(nfc(row.get("source_match_confidence")))
        except ValueError as exc:
            raise AssemblyError(f"cue {cue_id} requires numeric match confidence") from exc
        if not 0.0 <= confidence <= 1.0:
            raise AssemblyError(f"cue {cue_id} match confidence must be between 0 and 1")
        if confidence < min_match_confidence:
            raise AssemblyError(
                f"cue {cue_id} match confidence {confidence:.3f} is below "
                f"{min_match_confidence:.3f}; manual timing does not waive content matching"
            )
    else:
        raise AssemblyError(f"cue {cue_id} has unsupported source_match_method: {method or '<blank>'}")
    return source, cut_start, active_start, active_end, cut_end


def audit_plan_boundaries(args: argparse.Namespace) -> int:
    """Audit every evidence-complete source cut without rendering audio."""
    plan = Path(args.plan).resolve()
    with plan.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise AssemblyError(f"plan has no rows: {plan}")
    ffmpeg = find_ffmpeg(args.ffmpeg)
    cache: dict[str, np.ndarray] = {}
    checks = []
    for row in rows:
        cue_id = nfc(row.get("cue_id")) or "<blank>"
        source, cut_start, active_start, active_end, cut_end = validate_experimental_source_row(
            row, args.min_match_confidence
        )
        if source not in cache:
            cache[source] = read_audio(Path(source), args.sample_rate, ffmpeg)
        check = inspect_source_boundaries(
            cache[source], args.sample_rate, cue_id,
            cut_start, active_start, active_end, cut_end,
            args.boundary_probe_ms, args.boundary_absolute_dbfs,
            args.boundary_relative_drop_db, args.min_safety_ms,
        )
        check["source"] = source
        check["content_boundary_gate"] = require_content_boundary_evidence(row, cue_id)
        check["joint_detection_gate"] = require_joint_detection_evidence(row, cue_id)
        checks.append(check)
    source_activity_gate = source_activity_audit_from_args(rows, cache, args)
    unsafe = [item for item in checks if item["status"] != "safe"]
    report = {
        "status": (
            "unassigned_source_activity"
            if source_activity_gate["status"] != "passed"
            else ("unsafe_boundaries" if unsafe else "safe")
        ),
        "plan": str(plan),
        "checked_cue_count": len(checks),
        "unsafe_cue_count": len(unsafe),
        "probe_ms": args.boundary_probe_ms,
        "absolute_dbfs": args.boundary_absolute_dbfs,
        "relative_drop_db": args.boundary_relative_drop_db,
        "min_safety_ms": args.min_safety_ms,
        "source_activity_gate": source_activity_gate,
        "cues": checks,
    }
    if args.report:
        write_json(Path(args.report).resolve(), report)
    print(json.dumps(report, ensure_ascii=False))
    return 2 if unsafe or source_activity_gate["status"] != "passed" else 0


def safe_component(value: object, fallback: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_.\-\u3040-\u30ff\u3400-\u9fff]+", "_", nfc(value))
    return text.strip("._") or fallback


def render_manual_stems(args: argparse.Namespace) -> int:
    """Split safe timing rows and unresolved timing rows; never waive source evidence."""
    plan = Path(args.plan).resolve()
    with plan.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise AssemblyError(f"plan has no rows: {plan}")
    if args.manual_cues:
        manual_cues = read_manual_cues(Path(args.manual_cues).resolve())
        manual_source = str(Path(args.manual_cues).resolve())
    else:
        manual_cues = {}
        for row in rows:
            cue_id = nfc(row.get("cue_id"))
            if nfc(row.get("status")) != "planned":
                manual_cues[cue_id] = {
                    "reason": nfc(row.get("notes")) or "timing requires manual handling",
                }
        manual_source = "auto: unresolved plan rows"
    plan_ids = [nfc(row.get("cue_id")) for row in rows]
    if any(not cue_id for cue_id in plan_ids):
        raise AssemblyError("plan contains an empty cue_id")
    if len(plan_ids) != len(set(plan_ids)):
        raise AssemblyError("plan contains duplicate cue_id values")
    unknown_manual = sorted(set(manual_cues) - set(plan_ids))
    if unknown_manual:
        raise AssemblyError(f"manual cue TSV contains unknown cue_id values: {unknown_manual}")

    ffmpeg = find_ffmpeg(args.ffmpeg)
    cache: dict[str, np.ndarray] = {}
    for row in rows:
        source_value = nfc(row.get("source_path"))
        if not source_value:
            raise AssemblyError(f"cue {nfc(row.get('cue_id')) or '<blank>'} has no source_path")
        source = str(Path(source_value).resolve())
        if source not in cache:
            cache[source] = read_audio(Path(source), args.sample_rate, ffmpeg)
    source_activity_gate = source_activity_audit_from_args(rows, cache, args)
    if source_activity_gate["status"] != "passed":
        first = source_activity_gate["unreviewed"][0]
        raise AssemblyError(
            "unassigned source waveform activity blocks manual-stem rendering: "
            f"{first['source']} {first['start']:.3f}-{first['end']:.3f}s; "
            "manual timing does not waive a possibly truncated actor utterance"
        )
    rows = sorted(rows, key=lambda row: (float(row["target_start"]), nfc(row.get("cue_id"))))
    placements = []
    schedule = []
    boundary_checks = []
    previous_safe_active_end = 0.0
    timeline_end = 0.0
    auto_mode = args.manual_cues is None
    for row in rows:
        cue_id = nfc(row.get("cue_id"))
        is_manual = cue_id in manual_cues
        if not is_manual and nfc(row.get("status")) != "planned":
            if auto_mode:
                manual_cues[cue_id] = {
                    "reason": nfc(row.get("notes")) or "timing requires manual handling",
                }
                is_manual = True
            else:
                raise AssemblyError(
                    f"non-manual cue {cue_id} is unresolved; either finish review or list it in manual cues"
                )
        source, cut_start, active_start, active_end, cut_end = validate_experimental_source_row(
            row, args.min_match_confidence
        )
        if source not in cache:
            cache[source] = read_audio(Path(source), args.sample_rate, ffmpeg)
        source_duration = len(cache[source]) / args.sample_rate
        if cut_end > source_duration + 1e-6:
            raise AssemblyError(f"cue {cue_id} source range exceeds source audio duration")
        boundary = inspect_source_boundaries(
            cache[source], args.sample_rate, cue_id,
            cut_start, active_start, active_end, cut_end,
            args.boundary_probe_ms, args.boundary_absolute_dbfs,
            args.boundary_relative_drop_db, args.min_safety_ms,
        )
        if boundary["status"] != "safe":
            raise AssemblyError(
                f"unsafe source boundary at cue {cue_id}: " + "; ".join(boundary["problems"])
            )
        boundary["content_boundary_gate"] = require_content_boundary_evidence(row, cue_id)
        boundary["joint_detection_gate"] = require_joint_detection_evidence(row, cue_id)
        boundary_checks.append(boundary)
        target_start = parse_timecode(row.get("target_start"))
        target_end = parse_timecode(row.get("target_end"))
        if target_end <= target_start:
            raise AssemblyError(f"cue {cue_id} target_end must be later than target_start")
        pre_roll = active_start - cut_start
        policy = nfc(row.get("timing_policy")) or "normal_push"
        if not is_manual and policy not in {"normal_push", "intentional_overlap"}:
            if auto_mode:
                manual_cues[cue_id] = {
                    "reason": nfc(row.get("notes")) or "timing policy requires manual handling",
                }
                is_manual = True
            else:
                raise AssemblyError(
                    f"non-manual cue {cue_id} has unresolved timing_policy {policy!r}"
                )
        if is_manual:
            actual_active_start = target_start
            reason = (
                manual_cues[cue_id].get("reason")
                or manual_cues[cue_id].get("manual_reason")
                or nfc(row.get("notes"))
                or "manual timing review"
            )
        else:
            if policy == "intentional_overlap":
                if not nfc(row.get("overlap_evidence")) or not nfc(row.get("reviewed_by")):
                    raise AssemblyError(
                        f"cue {cue_id} intentional_overlap requires evidence and reviewed_by"
                    )
                actual_active_start = target_start
            else:
                actual_active_start = max(
                    target_start, previous_safe_active_end + args.min_gap_ms / 1000.0
                )
            delay_ms = (actual_active_start - target_start) * 1000.0
            hard_sync_conflict = (
                nfc(row.get("hard_sync")).lower() in {"1", "true", "yes", "y"}
                and delay_ms > 0.001
            )
            excessive_delay = delay_ms > args.max_auto_delay_ms
            if hard_sync_conflict or excessive_delay:
                if auto_mode:
                    manual_cues[cue_id] = {
                        "reason": (
                            "hard sync conflict; manual timing required"
                            if hard_sync_conflict
                            else f"automatic delay {delay_ms:.1f} ms exceeds limit"
                        ),
                    }
                    is_manual = True
                    actual_active_start = target_start
                    reason = manual_cues[cue_id]["reason"]
                else:
                    if hard_sync_conflict:
                        raise AssemblyError(
                            f"hard sync conflict at cue {cue_id}; add it to manual cues for the experiment"
                        )
                    raise AssemblyError(
                        f"safe-track delay {delay_ms:.1f} ms exceeds limit at cue {cue_id}; "
                        "add it to manual cues"
                    )
            else:
                previous_safe_active_end = max(
                    previous_safe_active_end,
                    actual_active_start + (active_end - active_start),
                )
                reason = ""
        place_start = actual_active_start - pre_roll
        clip_end = place_start + (cut_end - cut_start)
        actual_active_end = actual_active_start + (active_end - active_start)
        timeline_end = max(timeline_end, target_end, clip_end)
        placements.append({
            "row": row,
            "manual": is_manual,
            "reason": reason,
            "source": source,
            "cut_start": cut_start,
            "active_start": active_start,
            "active_end": active_end,
            "cut_end": cut_end,
            "place_start": place_start,
            "actual_active_start": actual_active_start,
            "actual_active_end": actual_active_end,
            "clip_end": clip_end,
        })
        schedule.append({
            "cue_id": cue_id,
            "role": nfc(row.get("role")),
            "content_start_evidence": nfc(row.get("content_start_evidence")),
            "content_end_evidence": nfc(row.get("content_end_evidence")),
            "asr_alignment_evidence": nfc(row.get("asr_alignment_evidence")),
            "waveform_evidence": nfc(row.get("waveform_evidence")),
            "manual_required": is_manual,
            "manual_reason": reason,
            "target_start": target_start,
            "target_end": target_end,
            "actual_active_start": actual_active_start,
            "actual_active_end": actual_active_end,
            "source_active_duration": active_end - active_start,
            "rendered_active_duration": actual_active_end - actual_active_start,
        })

    if args.episode_duration is not None:
        if args.episode_duration <= 0:
            raise AssemblyError("--episode-duration must be positive")
        total_duration = max(args.episode_duration, timeline_end + args.tail_pad)
    else:
        total_duration = timeline_end + args.tail_pad
    frame_count = int(math.ceil(total_duration * args.sample_rate))
    safe_track = np.zeros((frame_count, 2), dtype=np.float32)
    manual_track = np.zeros((frame_count, 2), dtype=np.float32)
    prepared_clips = []
    for item in placements:
        data = cache[item["source"]]
        source_start = max(0, int(round(item["cut_start"] * args.sample_rate)))
        source_end = min(len(data), int(round(item["cut_end"] * args.sample_rate)))
        clip = data[source_start:source_end].copy()
        clip, _, _ = apply_margin_fades(
            clip, args.sample_rate, args.fade_ms,
            item["active_start"] - item["cut_start"],
            item["cut_end"] - item["active_end"],
        )
        target_sample = int(round(item["place_start"] * args.sample_rate))
        if target_sample < 0:
            clip_for_track = clip[-target_sample:]
            target_sample = 0
        else:
            clip_for_track = clip
        target = manual_track if item["manual"] else safe_track
        end_sample = target_sample + len(clip_for_track)
        if end_sample > len(target):
            raise AssemblyError("internal duration error while rendering experimental stems")
        target[target_sample:end_sample] += clip_for_track
        prepared_clips.append((item, clip))

    combined = safe_track + manual_track
    combined_peak = float(np.max(np.abs(combined))) if combined.size else 0.0
    if combined_peak <= 1e-9:
        raise AssemblyError("experimental render is silent")
    target_amplitude = 10.0 ** (args.target_peak / 20.0)
    shared_gain = target_amplitude / combined_peak
    safe_track *= shared_gain
    manual_track *= shared_gain

    out_dir = Path(args.out_dir).resolve()
    episode = safe_component(args.episode, "episode")
    safe_output = out_dir / f"{episode}_normal_dialogue.wav"
    manual_output = out_dir / f"{episode}_manual_dialogue.wav"
    clips_dir = out_dir / "manual_clips"
    manifest_path = out_dir / f"{episode}_manual_manifest.tsv"
    qc_path = Path(args.qc_report).resolve() if args.qc_report else out_dir / f"{episode}_manual_stems.qc.json"
    manual_items = [(item, clip) for item, clip in prepared_clips if item["manual"]]
    clip_paths = []
    for index, (item, _) in enumerate(manual_items, start=1):
        row = item["row"]
        clip_paths.append(
            clips_dir / (
                f"{index:03d}_{safe_component(row.get('cue_id'), 'cue')}_"
                f"{safe_component(row.get('role'), 'role')}.wav"
            )
        )
    all_outputs = [safe_output, manual_output, manifest_path, qc_path, *clip_paths]
    existing = [str(path) for path in all_outputs if path.exists()]
    if existing:
        raise AssemblyError(f"refusing to overwrite existing experimental outputs: {existing}")

    write_audio(safe_output, safe_track, args.sample_rate, args.subtype)
    write_audio(manual_output, manual_track, args.sample_rate, args.subtype)
    manifest_rows = []
    for (item, clip), clip_path in zip(manual_items, clip_paths):
        clip = clip * shared_gain
        write_audio(clip_path, clip, args.sample_rate, args.subtype)
        row = item["row"]
        manifest_rows.append({
            "cue_id": nfc(row.get("cue_id")),
            "role": nfc(row.get("role")),
            "text": nfc(row.get("text")),
            "reason": item["reason"],
            "target_start": f"{parse_timecode(row.get('target_start')):.6f}",
            "target_end": f"{parse_timecode(row.get('target_end')):.6f}",
            "manual_track_clip_start": f"{max(0.0, item['place_start']):.6f}",
            "manual_track_active_start": f"{item['actual_active_start']:.6f}",
            "manual_track_active_end": f"{item['actual_active_end']:.6f}",
            "source_active_duration": f"{item['active_end'] - item['active_start']:.6f}",
            "individual_clip_active_offset": f"{item['active_start'] - item['cut_start']:.6f}",
            "individual_clip": str(clip_path),
        })
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cue_id", "role", "text", "reason", "target_start", "target_end",
                "manual_track_clip_start", "manual_track_active_start",
                "manual_track_active_end", "source_active_duration",
                "individual_clip_active_offset", "individual_clip",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(manifest_rows)
    qc = {
        "status": "experimental_output_not_final",
        "mode": "render-manual-stems-auto" if not args.manual_cues else "render-manual-stems",
        "plan": str(plan),
        "manual_cues": manual_source,
        "safe_output": str(safe_output),
        "manual_output": str(manual_output),
        "manual_manifest": str(manifest_path),
        "manual_clips_dir": str(clips_dir),
        "sample_rate": args.sample_rate,
        "channels": 2,
        "subtype": args.subtype,
        "duration": frame_count / args.sample_rate,
        "safe_cue_count": len(placements) - len(manual_items),
        "manual_cue_count": len(manual_items),
        "source_activity_gate": source_activity_gate,
        "shared_gain_db": dbfs(shared_gain),
        "safe_metrics": audio_metrics(safe_track),
        "manual_metrics": audio_metrics(manual_track),
        "zero_truncation_gate": {
            "status": "passed",
            "checked_cue_count": len(boundary_checks),
            "probe_ms": args.boundary_probe_ms,
            "absolute_dbfs": args.boundary_absolute_dbfs,
            "relative_drop_db": args.boundary_relative_drop_db,
            "min_safety_ms": args.min_safety_ms,
            "cues": boundary_checks,
        },
        "safe_loudness": measure_loudness(safe_output, ffmpeg),
        "manual_loudness": measure_loudness(manual_output, ffmpeg),
        "schedule": schedule,
        "warnings": [
            "Manual-timing split render; do not label it final delivery until manual timing is resolved.",
            "Both full-length stems share one gain and time origin; no independent normalization was applied.",
            "Manual dialogue active starts were placed at script target_start without time-stretching or truncation.",
            "Source cut edges and spoken-content head/tail evidence passed the zero-truncation gate; fades were confined to safety margins.",
            "Use individual clips when manual cues overlap each other.",
        ],
    }
    write_json(qc_path, qc)
    print(json.dumps({
        "status": qc["status"],
        "safe_output": str(safe_output),
        "manual_output": str(manual_output),
        "manual_cue_count": len(manual_items),
        "manifest": str(manifest_path),
    }, ensure_ascii=False))
    return 0


def render_batch(args: argparse.Namespace) -> int:
    plan_dir = Path(args.plan_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plans = sorted(p for p in plan_dir.glob("*.tsv") if not p.name.startswith((".", "._")))
    if not plans:
        raise AssemblyError(f"no plan TSV files found: {plan_dir}")
    with Path(args.coverage_map).resolve().open("r", encoding="utf-8-sig", newline="") as handle:
        coverage_rows = list(csv.DictReader(handle, delimiter="\t"))
    coverage_by_plan = {
        nfc(row.get("plan")): nfc(row.get("coverage_source")) for row in coverage_rows
    }
    if not coverage_by_plan or any(not key or not value for key, value in coverage_by_plan.items()):
        raise AssemblyError("coverage map requires non-empty plan and coverage_source columns")
    results = []
    for plan in plans:
        coverage_value = coverage_by_plan.get(plan.name) or coverage_by_plan.get(plan.stem)
        if not coverage_value:
            results.append({"job_id": plan.stem, "status": "failed", "error": "missing coverage map entry"})
            continue
        coverage_path = Path(coverage_value)
        if not coverage_path.is_absolute():
            coverage_path = Path(args.coverage_map).resolve().parent / coverage_path
        output = out_dir / f"{plan.stem}.wav"
        job_args = argparse.Namespace(
            plan=str(plan), coverage_source=str(coverage_path), output=str(output), fade_ms=args.fade_ms,
            tail_pad=args.tail_pad, sample_rate=args.sample_rate,
            subtype=args.subtype, target_peak=args.target_peak,
            min_gap_ms=args.min_gap_ms, warn_delay_ms=args.warn_delay_ms,
            max_auto_delay_ms=args.max_auto_delay_ms,
            min_match_confidence=args.min_match_confidence,
            boundary_probe_ms=args.boundary_probe_ms,
            boundary_absolute_dbfs=args.boundary_absolute_dbfs,
            boundary_relative_drop_db=args.boundary_relative_drop_db,
            min_safety_ms=args.min_safety_ms,
            source_activity_review=args.source_activity_review,
            source_activity_lookaround_ms=args.source_activity_lookaround_ms,
            source_activity_threshold_dbfs=args.source_activity_threshold_dbfs,
            source_activity_frame_ms=args.source_activity_frame_ms,
            source_activity_bridge_ms=args.source_activity_bridge_ms,
            source_activity_min_ms=args.source_activity_min_ms,
            source_activity_coverage_tolerance_ms=args.source_activity_coverage_tolerance_ms,
            ffmpeg=args.ffmpeg, qc_report=str(out_dir / f"{plan.stem}.qc.json"),
        )
        try:
            render_plan(job_args)
            auto_normal = out_dir / f"{plan.stem}_normal_dialogue.wav"
            auto_manual = out_dir / f"{plan.stem}_manual_dialogue.wav"
            if auto_normal.exists() and auto_manual.exists():
                results.append({
                    "job_id": plan.stem,
                    "status": "manual_timing_split",
                    "outputs": [str(auto_normal), str(auto_manual)],
                })
            else:
                results.append({"job_id": plan.stem, "status": "ok", "output": str(output)})
        except (AssemblyError, OSError, sf.LibsndfileError, ValueError) as exc:
            results.append({"job_id": plan.stem, "status": "failed", "error": str(exc)})
    failed = [r for r in results if r["status"] == "failed"]
    split = [r for r in results if r["status"] == "manual_timing_split"]
    summary = {
        "status": "ok_with_manual_timing" if not failed and split else ("ok" if not failed else "partial"),
        "job_count": len(results),
        "success_count": len(results) - len(failed),
        "failed_count": len(failed),
        "manual_timing_split_count": len(split),
        "jobs": results,
    }
    write_json(Path(args.summary).resolve(), summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "jobs"}, ensure_ascii=False))
    return 0 if not failed else 2


def add_audio_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--subtype", choices=["PCM_16", "PCM_24"], default="PCM_24")
    parser.add_argument("--target-peak", type=float, default=-2.06, help="sample peak in dBFS; override from the reviewed project profile")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--qc-report")


def add_source_activity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-activity-review",
        help=(
            "optional TSV for source-hash-bound ng_or_extra decisions; unassigned waveform "
            "activity otherwise blocks audit and rendering"
        ),
    )
    parser.add_argument("--source-activity-lookaround-ms", type=float, default=2000.0)
    parser.add_argument("--source-activity-threshold-dbfs", type=float, default=-42.0)
    parser.add_argument("--source-activity-frame-ms", type=float, default=10.0)
    parser.add_argument("--source-activity-bridge-ms", type=float, default=80.0)
    parser.add_argument("--source-activity-min-ms", type=float, default=60.0)
    parser.add_argument("--source-activity-coverage-tolerance-ms", type=float, default=40.0)


def add_boundary_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--boundary-probe-ms", type=float, default=40.0)
    parser.add_argument("--boundary-absolute-dbfs", type=float, default=-42.0)
    parser.add_argument("--boundary-relative-drop-db", type=float, default=12.0)
    parser.add_argument("--min-safety-ms", type=float, default=80.0)
    add_source_activity_args(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe short-drama dialogue assembly")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit-reference", help="audit delivered mixes and optional stems")
    audit.add_argument("--mix-dir", required=True)
    audit.add_argument("--stem-root")
    audit.add_argument("--report", required=True)
    audit.set_defaults(func=audit_reference)

    aligned = sub.add_parser("mix-aligned", help="mix stems already aligned to the episode timeline")
    aligned.add_argument("--stem-dir", required=True)
    aligned.add_argument("--output", required=True)
    aligned.add_argument("--srt", help="target episode SRT for timeline extent check")
    aligned.add_argument("--cue-tsv", help="reviewed dialogue TSV with cue_id,start,end,text,stem_file; checks each role against script timing")
    aligned.add_argument("--overlap-review", help="TSV of source-verified intentional overlaps")
    aligned.add_argument("--overlap-report", help="pre-mix audit JSON (written even when blocked)")
    aligned.add_argument(
        "--skip-preflight", action="store_true", default=False,
        help="bypass pre-flight gates (duration/activity/overlap/script timing); "
             "use ONLY after manual confirmation that stems are truly precision-aligned",
    )
    add_audio_output_args(aligned)
    aligned.set_defaults(func=mix_aligned)

    boundary = sub.add_parser(
        "audit-plan-boundaries",
        help="fail when reviewed source cut edges still resemble active speech",
    )
    boundary.add_argument("--plan", required=True)
    boundary.add_argument("--report")
    boundary.add_argument("--sample-rate", type=int, default=48000)
    boundary.add_argument("--min-match-confidence", type=float, default=0.75)
    boundary.add_argument("--ffmpeg")
    add_boundary_args(boundary)
    boundary.set_defaults(func=audit_plan_boundaries)

    match = sub.add_parser("match-template", help="create a spoken-content match sheet for an unaligned reel")
    match.add_argument("--cue-tsv", required=True)
    match.add_argument("--episode")
    match.add_argument("--role")
    match.add_argument("--group-semantic-units", action="store_true")
    match.add_argument("--output", required=True)
    match.set_defaults(func=write_match_template)

    plan = sub.add_parser("plan-reel", help="create a reviewable plan for an unaligned audio reel")
    plan.add_argument("--audio", required=True)
    plan.add_argument("--cue-tsv")
    plan.add_argument("--srt")
    plan.add_argument("--episode")
    plan.add_argument("--role")
    plan.add_argument("--group-semantic-units", action="store_true")
    plan.add_argument("--source-match-tsv")
    plan.add_argument("--min-match-confidence", type=float, default=0.75)
    plan.add_argument("--plan", required=True)
    plan.add_argument("--sample-rate", type=int, default=48000)
    plan.add_argument("--threshold-dbfs", type=float, default=-42.0)
    plan.add_argument("--frame-ms", type=float, default=10.0)
    plan.add_argument("--bridge-ms", type=float, default=250.0)
    plan.add_argument("--min-active-ms", type=float, default=120.0)
    plan.add_argument("--pad-ms", type=float, default=100.0)
    plan.add_argument("--source-activity-report")
    add_source_activity_args(plan)
    plan.add_argument("--ffmpeg")
    plan.set_defaults(func=plan_reel)

    plan_many = sub.add_parser("plan-batch", help="create plans from an explicit jobs TSV")
    plan_many.add_argument("--jobs", required=True)
    plan_many.add_argument("--plan-dir", required=True)
    plan_many.add_argument("--summary", required=True)
    plan_many.add_argument("--sample-rate", type=int, default=48000)
    plan_many.add_argument("--threshold-dbfs", type=float, default=-42.0)
    plan_many.add_argument("--frame-ms", type=float, default=10.0)
    plan_many.add_argument("--bridge-ms", type=float, default=250.0)
    plan_many.add_argument("--min-active-ms", type=float, default=120.0)
    plan_many.add_argument("--pad-ms", type=float, default=100.0)
    plan_many.add_argument("--min-match-confidence", type=float, default=0.75)
    add_source_activity_args(plan_many)
    plan_many.add_argument("--ffmpeg")
    plan_many.set_defaults(func=plan_batch)

    merge = sub.add_parser("merge-plans", help="merge all role plans for one episode before timing review")
    merge.add_argument("--plan-dir", required=True)
    merge.add_argument("--output", required=True)
    merge.set_defaults(func=merge_plans)

    render = sub.add_parser("render-plan", help="render a plan, auto-splitting unresolved timing rows")
    render.add_argument("--plan", required=True)
    render.add_argument(
        "--coverage-source", required=True,
        help="immutable SRT/TSV/CSV cue inventory; every cue must occur exactly once in the plan",
    )
    render.add_argument("--output", required=True)
    render.add_argument("--fade-ms", type=float, default=8.0)
    render.add_argument("--tail-pad", type=float, default=0.25)
    render.add_argument("--min-gap-ms", type=float, default=50.0)
    render.add_argument("--warn-delay-ms", type=float, default=500.0)
    render.add_argument("--max-auto-delay-ms", type=float, default=1000.0)
    render.add_argument("--min-match-confidence", type=float, default=0.75)
    add_boundary_args(render)
    add_audio_output_args(render)
    render.set_defaults(func=render_plan)

    manual = sub.add_parser(
        "render-manual-stems",
        help="experimentally split reviewed safe cues and manual-timing cues",
    )
    manual.add_argument("--plan", required=True)
    manual.add_argument("--manual-cues", help="optional override; omitted means auto-use unresolved plan rows")
    manual.add_argument("--episode", required=True)
    manual.add_argument("--out-dir", required=True)
    manual.add_argument("--episode-duration", type=float)
    manual.add_argument("--fade-ms", type=float, default=8.0)
    manual.add_argument("--tail-pad", type=float, default=0.25)
    manual.add_argument("--min-gap-ms", type=float, default=50.0)
    manual.add_argument("--max-auto-delay-ms", type=float, default=1000.0)
    manual.add_argument("--min-match-confidence", type=float, default=0.75)
    add_boundary_args(manual)
    add_audio_output_args(manual)
    manual.set_defaults(func=render_manual_stems)

    render_many = sub.add_parser("render-batch", help="render plans and auto-split unresolved timing rows")
    render_many.add_argument("--plan-dir", required=True)
    render_many.add_argument(
        "--coverage-map", required=True,
        help="TSV with plan and coverage_source columns; one exact entry per plan",
    )
    render_many.add_argument("--out-dir", required=True)
    render_many.add_argument("--summary", required=True)
    render_many.add_argument("--fade-ms", type=float, default=8.0)
    render_many.add_argument("--tail-pad", type=float, default=0.25)
    render_many.add_argument("--min-gap-ms", type=float, default=50.0)
    render_many.add_argument("--warn-delay-ms", type=float, default=500.0)
    render_many.add_argument("--max-auto-delay-ms", type=float, default=1000.0)
    render_many.add_argument("--min-match-confidence", type=float, default=0.75)
    add_boundary_args(render_many)
    add_audio_output_args(render_many)
    render_many.set_defaults(func=render_batch)
    return parser


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (AssemblyError, OSError, sf.LibsndfileError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
