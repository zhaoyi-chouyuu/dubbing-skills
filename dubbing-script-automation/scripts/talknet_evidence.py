#!/usr/bin/env python3
"""Run the local TalkNet deployment and export reviewable supporting evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import cv2
import numpy as np


WINDOWS_ROOT = Path(r"E:\AI_Models\tools\talknet-asd")
WINDOWS_FFMPEG = Path(r"E:\AI_Models\RVC\RVC20240604Nvidia50x0\ffmpeg.exe")
WINDOWS_PYTHON = Path(r"E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe")
WSL_ROOT = Path("/mnt/e/AI_Models/tools/talknet-asd")
WSL_PYTHON = Path("/home/zhaoyi/miniconda/envs/stable-ai/bin/python")
DEFAULT_ROOT = Path(os.environ.get(
    "TALKNET_ROOT",
    str(WINDOWS_ROOT if os.name == "nt" else WSL_ROOT),
))


def ffmpeg_path() -> Path:
    configured = os.environ.get("FFMPEG_BINARY", "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt" and WINDOWS_FFMPEG.is_file():
        return WINDOWS_FFMPEG
    if os.name != "nt" and Path("/usr/bin/ffmpeg").is_file():
        return Path("/usr/bin/ffmpeg")
    return Path("/opt/homebrew/bin/ffmpeg") if os.name != "nt" else Path("ffmpeg")
FPS = 25.0


class EvidenceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_checked(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    ffmpeg_dir = str(ffmpeg_path().parent)
    environment["PATH"] = os.pathsep.join(part for part in (ffmpeg_dir, environment.get("PATH", "")) if part)
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=environment,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def deployed_paths(root: Path) -> dict[str, Path]:
    isolated_python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    configured_python = os.environ.get("TALKNET_PYTHON", "").strip()
    python_path = (
        Path(configured_python) if configured_python
        else isolated_python if isolated_python.is_file()
        else WINDOWS_PYTHON if os.name == "nt" and WINDOWS_PYTHON.is_file()
        else WSL_PYTHON if os.name != "nt" and WSL_PYTHON.is_file()
        else isolated_python
    )
    return {
        "root": root,
        "repo": root / "repo",
        "python": python_path,
        "talknet_model": root / "models" / "pretrain_TalkSet.model",
        "face_model": root / "repo" / "model" / "faceDetector" / "s3fd" / "sfd_face.pth",
        "metadata": root / "installation.json",
    }


def preflight_payload(root: Path, requested_device: str = "auto") -> dict[str, Any]:
    paths = deployed_paths(root)
    required = {
        "repository": paths["repo"] / "demoTalkNet.py",
        "python": paths["python"],
        "talknet_model": paths["talknet_model"],
        "face_model": paths["face_model"],
        "ffmpeg": ffmpeg_path(),
    }
    checks = {name: path.is_file() for name, path in required.items()}
    import_check = {"ok": False, "output": ""}
    runtime = {"device": "", "cuda_available": False, "gpu": ""}
    if checks["python"]:
        try:
            probe = (
                "import json,torch,cv2,scipy,sklearn,scenedetect,python_speech_features;"
                f"requested={requested_device!r};"
                "cuda=bool(torch.cuda.is_available());"
                "device=('cuda' if cuda else 'cpu') if requested=='auto' else requested;"
                "assert not device.startswith('cuda') or cuda, 'CUDA requested but unavailable';"
                "print(json.dumps({'torch':torch.__version__,'cv2':cv2.__version__,'device':device,'cuda_available':cuda,'gpu':torch.cuda.get_device_name(0) if cuda else ''}))"
            )
            result = run_checked([
                str(paths["python"]), "-c",
                probe,
            ])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            runtime = {key: payload[key] for key in ("device", "cuda_available", "gpu")}
            import_check = {"ok": True, "output": f"torch={payload['torch']}; cv2={payload['cv2']}"}
        except (OSError, subprocess.CalledProcessError) as exc:
            import_check = {"ok": False, "output": str(exc)}
    return {
        "schema_version": 2,
        "component": "talknet_active_speaker_evidence",
        "evidence_role": "supporting_only",
        "authoritative_for_final_role": False,
        "tool_root": str(root),
        "checks": checks,
        "imports": import_check,
        "runtime": runtime,
        "ready": all(checks.values()) and import_check["ok"],
    }


def command_preflight(args: argparse.Namespace) -> int:
    payload = preflight_payload(Path(args.tool_root).resolve(), args.device)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 2


def parse_srt_timestamp(value: str) -> float:
    hours, minutes, rest = value.strip().replace(".", ",").split(":")
    seconds, milliseconds = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def read_srt(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    entries: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        lines = [line.rstrip() for line in block.splitlines()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = [part.strip() for part in lines[1].split("-->", 1)]
        entries.append({
            "source_index": lines[0].strip(),
            "start": start,
            "end": end,
            "start_seconds": parse_srt_timestamp(start),
            "end_seconds": parse_srt_timestamp(end),
            "text": "\n".join(lines[2:]).strip(),
        })
    if not entries:
        raise EvidenceError(f"No valid SRT entries found: {path}")
    return entries


def smooth_score(scores: np.ndarray, index: int) -> float:
    window = scores[max(index - 2, 0):min(index + 3, len(scores))]
    return float(np.mean(window)) if len(window) else float("-inf")


def export_track_face(track: dict[str, Any], frames_dir: Path, output: Path) -> bool:
    frames = np.asarray(track["track"]["frame"], dtype=int)
    if not len(frames):
        return False
    middle = len(frames) // 2
    image = cv2.imread(str(frames_dir / f"{int(frames[middle]) + 1:06d}.jpg"))
    if image is None:
        return False
    box = np.asarray(track["track"]["bbox"][middle], dtype=float)
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [
        max(0, min(limit, int(round(value))))
        for value, limit in zip(box, (width - 1, height - 1, width, height))
    ]
    if x2 <= x1 or y2 <= y1:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(output), image[y1:y2, x1:x2]))


def load_official_results(work_dir: Path) -> tuple[list[dict[str, Any]], list[np.ndarray]]:
    tracks_path = work_dir / "input" / "pywork" / "tracks.pckl"
    scores_path = work_dir / "input" / "pywork" / "scores.pckl"
    if not tracks_path.is_file() or not scores_path.is_file():
        raise EvidenceError("TalkNet finished without track and score artifacts")
    with tracks_path.open("rb") as handle:
        tracks = pickle.load(handle)
    with scores_path.open("rb") as handle:
        scores = pickle.load(handle)
    if len(tracks) != len(scores):
        raise EvidenceError("TalkNet track and score counts do not match")
    return tracks, [np.asarray(item, dtype=float) for item in scores]


def export_frame_scores(
    path: Path,
    tracks: list[dict[str, Any]],
    scores: list[np.ndarray],
    episode: str,
    clip_start: float,
) -> list[dict[str, Any]]:
    fields = [
        "episode", "frame", "relative_time", "absolute_time", "track_id",
        "speaking_score", "active_by_official_threshold",
        "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2",
    ]
    records: list[dict[str, Any]] = []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for track_index, (track, track_scores) in enumerate(zip(tracks, scores)):
            frames = np.asarray(track["track"]["frame"], dtype=int)
            boxes = np.asarray(track["track"]["bbox"], dtype=float)
            usable = min(len(frames), len(boxes), len(track_scores))
            for local_index in range(usable):
                frame = int(frames[local_index])
                score = smooth_score(track_scores, local_index)
                box = boxes[local_index]
                record = {
                    "episode": episode,
                    "frame": frame,
                    "relative_time": f"{frame / FPS:.3f}",
                    "absolute_time": f"{clip_start + frame / FPS:.3f}",
                    "track_id": f"T{track_index:04d}",
                    "speaking_score": f"{score:.4f}",
                    "active_by_official_threshold": "true" if score >= 0 else "false",
                    "bbox_x1": f"{box[0]:.2f}",
                    "bbox_y1": f"{box[1]:.2f}",
                    "bbox_x2": f"{box[2]:.2f}",
                    "bbox_y2": f"{box[3]:.2f}",
                }
                writer.writerow(record)
                records.append(record)
    return records


def export_subtitle_candidates(
    path: Path,
    srt_path: Path,
    frame_records: list[dict[str, Any]],
    episode: str,
) -> int:
    entries = read_srt(srt_path)
    fields = [
        "episode", "source_index", "start", "end", "text",
        "top_track_id", "top_score", "second_track_id", "second_score",
        "score_margin", "talknet_status", "talknet_role", "track_role_evidence",
    ]
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for entry in entries:
            per_track: dict[str, list[float]] = {}
            for record in frame_records:
                time_value = float(record["absolute_time"])
                if entry["start_seconds"] <= time_value <= entry["end_seconds"]:
                    per_track.setdefault(record["track_id"], []).append(float(record["speaking_score"]))
            ranked = sorted(
                ((track_id, float(np.mean(values))) for track_id, values in per_track.items()),
                key=lambda item: item[1],
                reverse=True,
            )
            top = ranked[0] if ranked else ("", float("-inf"))
            second = ranked[1] if len(ranked) > 1 else ("", float("-inf"))
            if not ranked:
                status = "no_visible_track"
            elif top[1] < 0:
                status = "no_positive_track"
            elif len(ranked) > 1 and second[1] >= 0:
                status = "multiple_positive_tracks"
            else:
                status = "single_positive_track"
            writer.writerow({
                "episode": episode,
                "source_index": entry["source_index"],
                "start": entry["start"],
                "end": entry["end"],
                "text": entry["text"],
                "top_track_id": top[0],
                "top_score": "" if not ranked else f"{top[1]:.4f}",
                "second_track_id": second[0],
                "second_score": "" if len(ranked) < 2 else f"{second[1]:.4f}",
                "score_margin": "" if len(ranked) < 2 else f"{top[1] - second[1]:.4f}",
                "talknet_status": status,
                "talknet_role": "",
                "track_role_evidence": "",
            })
            count += 1
    return count


def command_run(args: argparse.Namespace) -> int:
    root = Path(args.tool_root).resolve()
    paths = deployed_paths(root)
    preflight = preflight_payload(root, args.device)
    if not preflight["ready"]:
        raise EvidenceError("TalkNet deployment is not ready; run preflight for details")
    video = Path(args.video).resolve()
    if not video.is_file():
        raise EvidenceError(f"Video not found: {video}")
    srt = Path(args.srt).resolve() if args.srt else None
    if srt and not srt.is_file():
        raise EvidenceError(f"SRT not found: {srt}")
    out_dir = Path(args.out_dir).resolve()
    if "03_final_delivery" in out_dir.parts:
        raise EvidenceError("TalkNet evidence artifacts must stay outside 03_final_delivery")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise EvidenceError(f"Output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    clip_start = float(args.start)
    with tempfile.TemporaryDirectory(prefix="talknet_asd_") as temp_name:
        temp_root = Path(temp_name)
        video_folder = temp_root / "demo"
        video_folder.mkdir()
        safe_video = video_folder / "input.mp4"
        ffmpeg_command = [str(ffmpeg_path()), "-y"]
        if clip_start > 0:
            ffmpeg_command += ["-ss", f"{clip_start:.3f}"]
        ffmpeg_command += ["-i", str(video)]
        if args.duration > 0:
            ffmpeg_command += ["-t", f"{float(args.duration):.3f}"]
        ffmpeg_command += [
            "-map", "0:v:0", "-map", "0:a:0?", "-r", "25",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-ar", "16000", "-ac", "1", str(safe_video),
        ]
        run_checked(ffmpeg_command)
        result = run_checked([
            str(paths["python"]), str(paths["repo"] / "demoTalkNet.py"),
            "--videoName", "input",
            "--videoFolder", str(video_folder),
            "--pretrainModel", str(paths["talknet_model"]),
            "--device", preflight["runtime"]["device"],
            "--nDataLoaderThread", str(args.threads),
            "--facedetScale", str(args.face_scale),
        ], cwd=paths["repo"])

        tracks, scores = load_official_results(video_folder)
        frame_scores_path = out_dir / "talknet_frame_scores.tsv"
        frame_records = export_frame_scores(
            frame_scores_path, tracks, scores, args.episode, clip_start,
        )
        track_faces_dir = out_dir / "track_faces"
        frames_dir = video_folder / "input" / "pyframes"
        track_summaries: list[dict[str, Any]] = []
        for index, (track, values) in enumerate(zip(tracks, scores)):
            usable = min(len(track["track"]["frame"]), len(values))
            smoothed = np.asarray([smooth_score(values, i) for i in range(usable)])
            face_path = track_faces_dir / f"T{index:04d}.jpg"
            face_written = export_track_face(track, frames_dir, face_path)
            frames = np.asarray(track["track"]["frame"], dtype=int)
            track_summaries.append({
                "track_id": f"T{index:04d}",
                "start_seconds": clip_start + (float(frames[0]) / FPS if len(frames) else 0),
                "end_seconds": clip_start + (float(frames[-1]) / FPS if len(frames) else 0),
                "frames": usable,
                "mean_score": float(np.mean(smoothed)) if len(smoothed) else None,
                "max_score": float(np.max(smoothed)) if len(smoothed) else None,
                "positive_frame_ratio": float(np.mean(smoothed >= 0)) if len(smoothed) else None,
                "representative_face": str(face_path) if face_written else "",
            })

        official_video = video_folder / "input" / "pyavi" / "video_out.avi"
        annotated_video = out_dir / "talknet_annotated.mp4"
        run_checked([
            str(ffmpeg_path()), "-y", "-i", str(official_video),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(annotated_video),
        ])
        subtitle_candidates = ""
        subtitle_count = 0
        if srt:
            subtitle_path = out_dir / "talknet_subtitle_candidates.tsv"
            subtitle_count = export_subtitle_candidates(
                subtitle_path, srt, frame_records, args.episode,
            )
            subtitle_candidates = str(subtitle_path)
        report = {
            "schema_version": 2,
            "component": "talknet_active_speaker_evidence",
            "evidence_role": "supporting_only",
            "authoritative_for_final_role": False,
            "episode": args.episode,
            "source_video": str(video),
            "source_video_sha256": sha256_file(video),
            "source_srt": str(srt) if srt else "",
            "source_srt_sha256": sha256_file(srt) if srt else "",
            "clip_start_seconds": clip_start,
            "clip_duration_seconds": float(args.duration),
            "device": preflight["runtime"]["device"],
            "official_threshold": "score >= 0",
            "talknet_model": str(paths["talknet_model"]),
            "talknet_model_sha256": sha256_file(paths["talknet_model"]),
            "face_model": str(paths["face_model"]),
            "face_model_sha256": sha256_file(paths["face_model"]),
            "tracks": track_summaries,
            "subtitle_rows": subtitle_count,
            "artifacts": {
                "annotated_video": str(annotated_video),
                "frame_scores": str(frame_scores_path),
                "subtitle_candidates": subtitle_candidates,
                "track_faces": str(track_faces_dir),
            },
            "runner_output_tail": result.stdout[-4000:],
        }
        report_path = out_dir / "talknet_evidence.json"
        write_json(report_path, report)
    print(json.dumps({
        "report": str(report_path),
        "annotated_video": str(annotated_video),
        "tracks": len(track_summaries),
        "subtitle_rows": subtitle_count,
    }, ensure_ascii=False, indent=2))
    return 0


def command_apply_track_map(args: argparse.Namespace) -> int:
    candidates = Path(args.candidates).resolve()
    mapping_path = Path(args.track_map).resolve()
    output = Path(args.out_tsv).resolve()
    if output.exists():
        raise EvidenceError(f"Output already exists: {output}")
    with mapping_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not {"track_id", "role"}.issubset(set(reader.fieldnames or [])):
            raise EvidenceError("Track map requires track_id and role columns")
        mapping = {
            (row.get("track_id") or "").strip(): (row.get("role") or "").strip()
            for row in reader
            if (row.get("track_id") or "").strip()
        }
    with candidates.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if "mapped_top_track_role" not in fields:
        fields.append("mapped_top_track_role")
    if "talknet_role" not in fields:
        fields.append("talknet_role")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            mapped_role = mapping.get((row.get("top_track_id") or "").strip(), "")
            row["mapped_top_track_role"] = mapped_role
            row["talknet_role"] = (
                mapped_role if (row.get("talknet_status") or "").strip() == "single_positive_track" else ""
            )
            writer.writerow(row)
    print(f"Mapped TalkNet supporting evidence written: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local TalkNet supporting-evidence runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--tool-root", default=str(DEFAULT_ROOT))
    preflight.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    preflight.set_defaults(func=command_preflight)

    run = subparsers.add_parser("run")
    run.add_argument("--video", required=True)
    run.add_argument("--srt")
    run.add_argument("--episode", required=True)
    run.add_argument("--out-dir", required=True)
    run.add_argument("--start", type=float, default=0)
    run.add_argument("--duration", type=float, default=0)
    run.add_argument("--threads", type=int, default=4)
    run.add_argument("--face-scale", type=float, default=0.25)
    run.add_argument("--tool-root", default=str(DEFAULT_ROOT))
    run.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    run.set_defaults(func=command_run)

    mapping = subparsers.add_parser("apply-track-map")
    mapping.add_argument("--candidates", required=True)
    mapping.add_argument("--track-map", required=True)
    mapping.add_argument("--out-tsv", required=True)
    mapping.set_defaults(func=command_apply_track_map)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except EvidenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or str(exc), file=sys.stderr)
        return int(exc.returncode or 2)


if __name__ == "__main__":
    raise SystemExit(main())
