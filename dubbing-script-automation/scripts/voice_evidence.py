#!/usr/bin/env python3
"""Beta1.0 speaker-embedding evidence for dubbing-script review.

The acoustic track is deliberately advisory: it may support a reviewed role or
route a disagreement back to review, but it never changes a role by itself.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import wave
from array import array
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "3"
SKILL_VERSION = "beta1.6"
MODEL_KINDS = ("resnet34", "campplus", "ecapa512")
GENERIC_ROLE_MARKERS = (
    "男性音声", "女性音声", "子供音声", "老人男性音声", "老人女性音声",
    "ナレーション", "不明音声", "generic", "unknown", "narrator",
)
INELIGIBLE_STATUSES = {
    "not_run", "not_reviewed", "too_short", "generic_role", "mixed_unit",
    "no_gallery", "same_episode_leakage", "overlap", "low_quality", "error",
    "acoustic_turn_unreviewed", "acoustic_turn_mixed", "missing_episode",
}
ADVISORY_STATUSES = {"low_similarity", "ambiguous"}


class VoiceError(RuntimeError):
    pass


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise VoiceError(f"TSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise VoiceError(f"TSV is empty: {path}")
    return rows


def write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise VoiceError(f"Output exists: {path}. Use --overwrite or a new path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise VoiceError(f"Output exists: {path}. Use --overwrite or a new path.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_fingerprint(path: Path) -> str:
    """Fingerprint a model file or the durable files in a local model directory."""
    if path.is_file():
        return file_sha256(path)
    if not path.is_dir():
        raise VoiceError(f"Model path not found: {path}")
    files = sorted(
        item for item in path.rglob("*")
        if item.is_file() and ".cache" not in item.relative_to(path).parts
    )
    if not files:
        raise VoiceError(f"Model directory is empty: {path}")
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(file_sha256(item).encode("ascii"))
    return digest.hexdigest()


def parse_bool(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "verified"}


def parse_time_seconds(value: str) -> float:
    text = (value or "").strip().replace(",", ".")
    if not text:
        raise VoiceError("Missing timestamp")
    if ":" not in text:
        return float(text)
    parts = text.split(":")
    if len(parts) != 3:
        raise VoiceError(f"Invalid timestamp: {value!r}")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def is_generic_role(role: str) -> bool:
    folded = (role or "").strip().casefold()
    return not folded or any(marker.casefold() in folded for marker in GENERIC_ROLE_MARKERS)


def load_runtime(model_path: Path, device_name: str, model_kind: str = "resnet34"):
    """Load an explicit local backend; never download or silently substitute models."""
    if model_kind not in MODEL_KINDS:
        raise VoiceError(f"Unsupported model kind: {model_kind}")
    if not model_path.exists():
        raise VoiceError(f"Speaker embedding model not found: {model_path}")
    try:
        import numpy as np
        import torch
    except ImportError as exc:
        raise VoiceError("Requires numpy and torch in the active Python environment") from exc
    if device_name == "auto":
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_name)
    if model_kind in {"campplus", "ecapa512"}:
        # Use the official WeSpeaker ONNX exports.  Keeping the frontend here
        # makes the runtime small and avoids coupling the skill to WeSpeaker's
        # training-only Python dependencies.
        config_path = model_path / "backend_config.json"
        if not config_path.is_file():
            raise VoiceError(
                f"{model_kind} needs a local directory with backend_config.json containing "
                "backend=wespeaker_onnx, model_kind, onnx_model and embedding_size. "
                "No model or package is downloaded automatically."
            )
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            size = int(config["embedding_size"])
            if config.get("backend") != "wespeaker_onnx":
                raise ValueError("backend_config backend must be wespeaker_onnx")
            if config.get("model_kind") != model_kind:
                raise ValueError("backend_config model_kind must match --model-kind")
            expected_size = 512 if model_kind == "campplus" else 192
            if size != expected_size:
                raise ValueError(f"{model_kind} embedding_size must be {expected_size}")
            onnx_model = (model_path / config["onnx_model"]).resolve()
            if not onnx_model.is_relative_to(model_path.resolve()) or not onnx_model.is_file():
                raise ValueError("onnx_model must be an existing file inside the local model directory")
            import onnxruntime as ort
            import torchaudio.compliance.kaldi as kaldi
            available = ort.get_available_providers()
            provider = "CUDAExecutionProvider" if device.type == "cuda" else "CPUExecutionProvider"
            if provider not in available:
                raise ValueError(f"ONNX provider {provider} is unavailable; available={available}")
            options = ort.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
            session = ort.InferenceSession(str(onnx_model), sess_options=options, providers=[provider])
            inputs = session.get_inputs()
            outputs = session.get_outputs()
            if len(inputs) != 1 or inputs[0].name != "feats" or len(outputs) != 1 or outputs[0].name != "embs":
                raise ValueError("unexpected WeSpeaker ONNX input/output contract")
            output_shape = outputs[0].shape
            if output_shape[-1] != size:
                raise ValueError(f"ONNX embedding dimension {output_shape[-1]} does not match {size}")
        except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise VoiceError(
                f"{model_kind} local backend failed: {exc}. Validate the official WeSpeaker ONNX export, "
                "onnxruntime/torchaudio compatibility and backend_config.json; no fallback is used."
            ) from exc
        def infer(audio):
            if audio.get("sample_rate") != 16000:
                raise VoiceError(f"{model_kind} expects normalized 16000 Hz waveform")
            waveform = audio["waveform"].to(torch.float32).cpu()
            features = kaldi.fbank(
                waveform,
                num_mel_bins=80,
                frame_length=25,
                frame_shift=10,
                sample_frequency=16000,
                window_type="hamming",
            )
            if features.shape[0] < 2:
                raise VoiceError(f"{model_kind} received too little speech for an embedding")
            features = features - torch.mean(features, dim=0, keepdim=True)
            batch = features.unsqueeze(0).numpy().astype("float32", copy=False)
            embedding = session.run(["embs"], {"feats": batch})[0]
            return np.asarray(embedding, dtype=np.float32).reshape(-1)
        return np, infer, str(device)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            from pyannote.audio import Inference, Model
        load_path = model_path / "pytorch_model.bin" if model_path.is_dir() and (model_path / "pytorch_model.bin").is_file() else model_path
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r"You are using `torch\.load` with `weights_only=False`.*")
            model = Model.from_pretrained(str(load_path))
        # The default backend remains the existing pyannote ResNet34 implementation.
        identity = f"{type(model).__module__}.{type(model).__name__}".casefold()
        if "resnet34" not in identity:
            raise VoiceError(f"--model-kind resnet34 loaded unexpected architecture: {identity}")
        model.to(device)
        return np, Inference(model, window="whole"), str(device)
    except Exception as exc:
        raise VoiceError(f"Could not load local ResNet34 speaker model: {model_path}: {exc}") from exc


def canonical_episode(value: Any) -> str:
    text = str(value or "").strip()
    return str(int(text)) if text.isdecimal() else text


def validate_gallery_model(gallery: dict[str, Any], model_path: Path, model_kind: str) -> None:
    fingerprint = path_fingerprint(model_path)[:16]
    if gallery.get("model_kind") != model_kind or gallery.get("model_fingerprint") != fingerprint:
        raise VoiceError("Gallery model kind/fingerprint differs from --model-kind/--model; rebuild this model's gallery")
    if gallery.get("gallery_ready") is not True and gallery.get("role_scoring_ready") is not True:
        raise VoiceError("Gallery has fewer than two QC-usable role profiles; rebuild after resolving role-level QC issues")
    if len(gallery.get("roles") or []) < 2:
        raise VoiceError("Gallery must contain at least two QC-usable role profiles for scoring")
    if not gallery.get("enrollment_episodes") or any(not canonical_episode(x) for x in gallery["enrollment_episodes"]):
        raise VoiceError("Gallery lacks enrollment episode provenance; rebuild before scoring")


def normalize(np: Any, vector: Any):
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-8:
        raise VoiceError("Embedding is zero or non-finite")
    return value / norm


def rank_candidates(np: Any, vector: Any, roles: list[str], centroids: Any):
    return sorted(
        ((role, float(np.dot(vector, centroid))) for role, centroid in zip(roles, centroids)),
        key=lambda item: item[1], reverse=True,
    )


def rank_gallery_roles(np: Any, vector: Any, gallery: dict[str, Any]):
    if gallery.get("scoring_mode") != "exemplar_max":
        return rank_candidates(np, vector, gallery["roles"], gallery["centroids"])
    best: dict[str, float] = {}
    for role, exemplar in zip(gallery["exemplar_roles"], gallery["exemplars"]):
        value = float(np.dot(vector, exemplar))
        if value > best.get(role, -2.0):
            best[role] = value
    return sorted(
        ((role, best[role]) for role in gallery["roles"] if role in best),
        key=lambda item: item[1], reverse=True,
    )


def extract_audio(media: Path, start: str, end: str, output: Path, ffmpeg_bin: str) -> float:
    if not media.is_file():
        raise VoiceError(f"Media not found: {media}")
    seek: list[str] = []
    requested_duration = 0.0
    if bool(start.strip()) != bool(end.strip()):
        raise VoiceError("Clip start and end must either both be provided or both be empty")
    if start.strip() and end.strip():
        try:
            start_value = parse_time_seconds(start)
            end_value = parse_time_seconds(end)
        except (ValueError, OverflowError) as exc:
            raise VoiceError(f"Invalid clip timestamps: {start!r} -> {end!r}") from exc
        if not all(math.isfinite(value) for value in (start_value, end_value)) or start_value < 0 or end_value <= start_value:
            raise VoiceError(f"Invalid clip interval: {start!r} -> {end!r}")
        requested_duration = end_value - start_value
        # Reviewed single-speaker boundaries are authoritative: never pad into
        # adjacent speech. Request sample-accurate PCM extraction within them.
        seek = ["-ss", f"{start_value:.9f}", "-t", f"{requested_duration:.9f}"]
    command = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y", *seek,
        "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise VoiceError(f"ffmpeg executable is not available: {ffmpeg_bin}") from exc
    if completed.returncode != 0 or not output.is_file():
        detail = (completed.stderr or "unknown ffmpeg error").strip().splitlines()[-1]
        raise VoiceError(f"ffmpeg could not extract audio from {media}: {detail}")
    with wave.open(str(output), "rb") as handle:
        sample_rate = handle.getframerate()
        actual_duration = handle.getnframes() / max(1, sample_rate)
    if actual_duration <= 0:
        raise VoiceError("Extracted clip is empty")
    if requested_duration and actual_duration + max(0.001, 2 / max(1, sample_rate)) < requested_duration:
        raise VoiceError(f"Extracted clip is shorter than the reviewed interval: actual={actual_duration:.6f}s requested={requested_duration:.6f}s; check source duration and timestamps")
    return actual_duration


def analyze_wav_quality(path: Path) -> dict[str, float]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if channels != 1 or width != 2 or rate <= 0 or not raw:
        raise VoiceError("Expected non-empty mono 16-bit PCM audio")
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = 32768.0
    square_sum = sum(float(sample) * float(sample) for sample in samples)
    rms = math.sqrt(square_sum / max(1, len(samples)))
    rms_dbfs = 20.0 * math.log10(max(rms / peak, 1e-9))
    clipping_ratio = sum(abs(sample) >= 32760 for sample in samples) / max(1, len(samples))
    frame_size = max(1, int(rate * 0.02))
    active = total = 0
    for offset in range(0, len(samples), frame_size):
        frame = samples[offset:offset + frame_size]
        if not frame:
            continue
        frame_rms = math.sqrt(sum(float(sample) * float(sample) for sample in frame) / len(frame))
        frame_dbfs = 20.0 * math.log10(max(frame_rms / peak, 1e-9))
        total += 1
        if frame_dbfs >= -45.0:
            active += 1
    return {
        "rms_dbfs": rms_dbfs,
        "clipping_ratio": clipping_ratio,
        "active_ratio": active / max(1, total),
    }


def load_wav_for_inference(np: Any, path: Path) -> dict[str, Any]:
    """Load FFmpeg-normalized PCM directly to bypass optional TorchCodec decoding."""
    try:
        import torch
    except ImportError as exc:
        raise VoiceError("Requires torch in the active Python environment") from exc
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if channels != 1 or width != 2 or rate <= 0 or not raw:
        raise VoiceError("Expected non-empty mono 16-bit PCM audio")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    waveform = torch.from_numpy(samples).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": rate}


def quality_problem(quality: dict[str, float], args: argparse.Namespace) -> str:
    if quality["rms_dbfs"] < args.min_rms_dbfs:
        return "signal_too_quiet"
    if quality["clipping_ratio"] > args.max_clipping_ratio:
        return "clipping"
    if quality["active_ratio"] < args.min_active_ratio:
        return "too_little_active_audio"
    return ""


def embed_row(np: Any, inference: Any, row: dict[str, str], temp_dir: Path, args: argparse.Namespace):
    media_value = (row.get("media_path") or row.get("audio_path") or "").strip()
    if not media_value:
        raise VoiceError("Manifest row is missing media_path/audio_path")
    key = hashlib.sha1(f"{media_value}|{row.get('start')}|{row.get('end')}".encode("utf-8")).hexdigest()[:16]
    wav = temp_dir / f"clip_{key}.wav"
    duration = extract_audio(
        Path(media_value), (row.get("start") or "").strip(),
        (row.get("end") or "").strip(), wav, args.ffmpeg,
    )
    quality = analyze_wav_quality(wav)
    problem = quality_problem(quality, args)
    if problem:
        raise VoiceError(f"audio_quality:{problem}")
    try:
        embedding = inference(load_wav_for_inference(np, wav))
    except Exception as exc:
        raise VoiceError(f"speaker embedding failed: {exc}") from exc
    return normalize(np, embedding), duration, quality


def percentile(np: Any, values: list[float], value: float, fallback: float) -> float:
    if not values:
        return fallback
    return float(np.percentile(np.asarray(values, dtype=np.float32), value))


def gallery_payload(np: Any, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VoiceError(f"Voice gallery not found: {path}")
    data = np.load(path, allow_pickle=False)
    roles = [str(item) for item in data["roles"].tolist()]
    result: dict[str, Any] = {
        "roles": roles,
        "centroids": data["centroids"],
        "schema_version": "1",
        "gallery_id": file_sha256(path)[:16],
        "processing_domain": "speech_dominant",
        "separator_id": "",
        "enrollment_episodes": [],
        "role_similarity_thresholds": {role: 0.0 for role in roles},
        "role_margin_thresholds": {role: 0.0 for role in roles},
    }
    for field in ("schema_version", "gallery_id", "model_kind", "model_fingerprint"):
        if field in data.files:
            result[field] = str(data[field].tolist()[0])
    for field in ("processing_domain", "separator_id"):
        if field in data.files:
            result[field] = str(data[field].tolist()[0])
    result["gallery_ready"] = bool(data["gallery_ready"].tolist()[0]) if "gallery_ready" in data.files else False
    result["role_scoring_ready"] = (
        bool(data["role_scoring_ready"].tolist()[0])
        if "role_scoring_ready" in data.files else result["gallery_ready"]
    )
    if "enrollment_episodes_json" in data.files:
        result["enrollment_episodes"] = json.loads(str(data["enrollment_episodes_json"].tolist()[0]))
    if "role_similarity_thresholds" in data.files:
        result["role_similarity_thresholds"] = dict(zip(roles, [float(x) for x in data["role_similarity_thresholds"].tolist()]))
    if "role_margin_thresholds" in data.files:
        result["role_margin_thresholds"] = dict(zip(roles, [float(x) for x in data["role_margin_thresholds"].tolist()]))
    result["scoring_mode"] = str(data["scoring_mode"].tolist()[0]) if "scoring_mode" in data.files else "centroid"
    if result["scoring_mode"] == "exemplar_max":
        if "exemplars" not in data.files or "exemplar_roles" not in data.files:
            raise VoiceError(f"Exemplar gallery lacks stored exemplars: {path}")
        result["exemplars"] = data["exemplars"]
        result["exemplar_roles"] = [str(item) for item in data["exemplar_roles"].tolist()]
    return result


def build_gallery_arrays(np: Any, grouped: dict[str, list[dict[str, Any]]], args: argparse.Namespace):
    roles = sorted(grouped)
    preliminary = {role: normalize(np, np.mean([item["vector"] for item in grouped[role]], axis=0)) for role in roles}
    audit_updates: dict[int, dict[str, Any]] = {}
    retained: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for role in roles:
        records = grouped[role]
        for position, item in enumerate(records):
            others = [entry["vector"] for index, entry in enumerate(records) if index != position]
            same = float(np.dot(item["vector"], normalize(np, np.mean(others, axis=0)))) if others else -1.0
            impostor = max((float(np.dot(item["vector"], preliminary[other])) for other in roles if other != role), default=-1.0)
            separation = same - impostor
            # A role with one long, verified clip has no within-role comparison yet.
            # Do not call that clip an outlier solely because same-role similarity is undefined;
            # the duration gate and cross-role separation still decide whether the role is usable.
            outlier = len(records) > 1 and (same < args.min_intra_similarity or separation < args.min_gallery_margin)
            audit_updates[item["audit_index"]] = {
                "same_role_similarity": f"{same:.6f}",
                "best_other_similarity": f"{impostor:.6f}",
                "gallery_margin": f"{separation:.6f}",
                "gallery_clip_status": "outlier_dropped" if outlier else "accepted",
            }
            if not outlier:
                retained[role].append(item)
    issues: list[dict[str, Any]] = []
    for role in roles:
        clips = retained[role]
        episodes = {str(item.get("episode") or "").strip() for item in clips if str(item.get("episode") or "").strip()}
        total_duration = sum(float(item.get("duration", 0.0)) for item in clips)
        if total_duration < args.min_total_duration_per_role:
            issues.append({"role": role, "issue": "insufficient_total_duration", "actual": round(total_duration, 3), "required": args.min_total_duration_per_role})
        if len(clips) < args.min_clips_per_role:
            issues.append({"role": role, "issue": "insufficient_clips", "actual": len(clips), "required": args.min_clips_per_role})
        if len(episodes) < args.min_episodes_per_role:
            issues.append({"role": role, "issue": "insufficient_distinct_episodes", "actual": len(episodes), "required": args.min_episodes_per_role})
    valid_roles = [role for role in roles if len(retained[role]) >= args.min_clips_per_role and sum(float(item.get("duration", 0.0)) for item in retained[role]) >= args.min_total_duration_per_role]
    if len(valid_roles) < 2:
        issues.append({"role": "", "issue": "gallery_requires_at_least_two_valid_roles"})
        return roles, None, None, None, audit_updates, retained, issues
    centroids_map = {role: normalize(np, np.mean([item["vector"] for item in retained[role]], axis=0)) for role in valid_roles}
    exemplar_mode = getattr(args, "scoring_mode", "centroid") == "exemplar_max"
    similarity_thresholds: list[float] = []
    margin_thresholds: list[float] = []
    for role in valid_roles:
        genuine: list[float] = []
        impostors: list[float] = []
        margins: list[float] = []
        records = retained[role]
        for position, item in enumerate(records):
            others = [entry["vector"] for index, entry in enumerate(records) if index != position]
            if exemplar_mode:
                # Leave-one-out calibration against the nearest stored exemplar, matching score-time ranking.
                same = max((float(np.dot(item["vector"], vector)) for vector in others), default=-1.0)
                other = max(
                    float(np.dot(item["vector"], entry["vector"]))
                    for name in valid_roles if name != role for entry in retained[name]
                )
            else:
                same_centroid = normalize(np, np.mean(others, axis=0)) if others else centroids_map[role]
                same = float(np.dot(item["vector"], same_centroid))
                other = max(float(np.dot(item["vector"], centroids_map[name])) for name in valid_roles if name != role)
            genuine.append(same)
            impostors.append(other)
            margins.append(same - other)
        genuine_p10 = percentile(np, genuine, 10, args.min_similarity)
        impostor_p95 = percentile(np, impostors, 95, -1.0)
        margin_p10 = percentile(np, margins, 10, args.min_margin)
        similarity_threshold = max(args.min_similarity, impostor_p95 + args.threshold_safety_margin)
        margin_threshold = max(args.min_margin, min(margin_p10, args.max_calibrated_margin))
        if genuine_p10 < similarity_threshold or margin_p10 < args.min_margin:
            issues.append({
                "role": role,
                "issue": "confusable_gallery_role",
                "genuine_p10": round(genuine_p10, 6),
                "impostor_p95": round(impostor_p95, 6),
                "margin_p10": round(margin_p10, 6),
            })
        similarity_thresholds.append(similarity_threshold)
        margin_thresholds.append(margin_threshold)
    centroids = np.stack([centroids_map[role] for role in valid_roles])
    return valid_roles, centroids, similarity_thresholds, margin_thresholds, audit_updates, retained, issues


def cmd_build_gallery(args: argparse.Namespace) -> int:
    if args.allow_unverified:
        raise VoiceError("--allow-unverified is disabled in beta1.6: manual identity confirmation is required")
    if args.min_clips_per_role < 3 or args.min_episodes_per_role < 1 or args.min_total_duration_per_role < 8:
        raise VoiceError("beta1.6 minimum enrollment is 3 clips, 1 confirmed episode and 8 seconds per role")
    np, inference, device = load_runtime(Path(args.model), args.device, args.model_kind)
    manifest = Path(args.manifest)
    rows = read_tsv(manifest)
    processing_domains = sorted({
        (row.get("processing_domain") or "").strip().casefold()
        for row in rows if (row.get("processing_domain") or "").strip()
    })
    separator_ids = sorted({
        (row.get("separator_id") or "").strip()
        for row in rows if (row.get("separator_id") or "").strip()
    })
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audit: list[dict[str, Any]] = []
    manifest_issues: list[dict[str, Any]] = []
    seen_intervals: dict[tuple[str, str, str], tuple[str, int]] = {}
    with tempfile.TemporaryDirectory(prefix="voice_gallery_") as temp:
        temp_dir = Path(temp)
        for number, row in enumerate(rows, start=2):
            role = (row.get("role") or "").strip()
            episode = (row.get("episode") or "").strip()
            record: dict[str, Any] = {
                "row": number, "role": role, "episode": episode,
                "media_path": row.get("media_path") or row.get("audio_path") or "",
                "start": row.get("start", ""), "end": row.get("end", ""),
                "duration": "", "rms_dbfs": "", "active_ratio": "", "clipping_ratio": "",
                "same_role_similarity": "", "best_other_similarity": "", "gallery_margin": "",
                "gallery_clip_status": "rejected", "error": "",
            }
            audit.append(record)
            if not role or is_generic_role(role):
                record["error"] = "missing_or_generic_role"
                continue
            if not episode:
                record["error"] = "missing_episode"
                continue
            if not args.allow_unverified and not parse_bool(row.get("verified")):
                record["error"] = "clip_not_verified"
                continue
            if any(parse_bool(row.get(field)) for field in ("overlap", "mixed_speaker", "music_dominant")):
                record["error"] = "declared_contaminated_audio"
                continue
            media_value = (row.get("media_path") or row.get("audio_path") or "").strip()
            interval_key = (str(Path(media_value).resolve()), (row.get("start") or "").strip(), (row.get("end") or "").strip())
            if interval_key in seen_intervals:
                prior_role, prior_row = seen_intervals[interval_key]
                record["error"] = f"duplicate_enrollment_interval_of_row_{prior_row}_{prior_role}"
                continue
            seen_intervals[interval_key] = (role, number)
            try:
                vector, duration, quality = embed_row(np, inference, row, temp_dir, args)
                record.update({
                    "duration": f"{duration:.3f}", "rms_dbfs": f"{quality['rms_dbfs']:.3f}",
                    "active_ratio": f"{quality['active_ratio']:.6f}",
                    "clipping_ratio": f"{quality['clipping_ratio']:.8f}",
                })
                if duration < args.min_enrollment_duration or duration > args.max_enrollment_duration:
                    record["error"] = "enrollment_duration_out_of_range"
                    continue
                grouped[role].append({"vector": vector, "episode": episode, "duration": duration, "audit_index": len(audit) - 1})
                record["gallery_clip_status"] = "embedded"
            except VoiceError as exc:
                record["error"] = str(exc)
    if not grouped:
        write_tsv(Path(args.audit_tsv), audit, list(audit[0]), args.overwrite) if args.audit_tsv and audit else None
        raise VoiceError("No enrollment clips passed verification and audio-quality gates")
    roles, centroids, sim_thresholds, margin_thresholds, audit_updates, retained, gallery_issues = build_gallery_arrays(np, grouped, args)
    if len(processing_domains) > 1:
        gallery_issues.append({
            "role": "",
            "issue": "mixed_processing_domains",
            "actual": processing_domains,
            "required": "one processing domain per gallery",
        })
    if processing_domains == ["separated"] and len(separator_ids) > 1:
        gallery_issues.append({
            "role": "",
            "issue": "mixed_separator_ids",
            "actual": separator_ids,
            "required": "one separator/model version per separated gallery",
        })
    for index, values in audit_updates.items():
        audit[index].update(values)
    rejected = [row for row in audit if row["gallery_clip_status"] not in {"accepted"}]
    manifest_issues.extend({"row": row["row"], "role": row["role"], "issue": row["error"] or row["gallery_clip_status"]} for row in rejected)
    global_issues = [issue for issue in gallery_issues if not str(issue.get("role") or "").strip()]
    role_issue_names = {
        str(issue.get("role") or "").strip()
        for issue in gallery_issues if str(issue.get("role") or "").strip()
    }
    usable_indices = [index for index, role in enumerate(roles) if role not in role_issue_names]
    usable_roles = [roles[index] for index in usable_indices]
    role_scoring_ready = bool(len(usable_roles) >= 2 and not global_issues and centroids is not None)
    gallery_ready = bool(role_scoring_ready and not gallery_issues)
    if centroids is not None and usable_indices:
        centroids = centroids[usable_indices]
        sim_thresholds = [sim_thresholds[index] for index in usable_indices]
        margin_thresholds = [margin_thresholds[index] for index in usable_indices]
    enrollment_episodes = sorted({
        str(item.get("episode") or "")
        for role in usable_roles for item in retained[role] if item.get("episode")
    })
    role_coverage = {
        role: {
            "accepted_clips": len(retained[role]),
            "accepted_duration_seconds": round(sum(float(item.get("duration", 0.0)) for item in retained[role]), 3),
            "enrollment_episodes": sorted({str(item.get("episode") or "") for item in retained[role] if item.get("episode")}),
        }
        for role in sorted(grouped)
    }
    role_status = {
        role: {
            "usable": role in usable_roles,
            "issues": [issue for issue in gallery_issues if issue.get("role") == role],
        }
        for role in sorted(grouped)
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "skill_version": SKILL_VERSION,
        "workflow_version": SKILL_VERSION,
        "model_kind": args.model_kind,
        "manifest": str(manifest.resolve()),
        "model": str(Path(args.model).resolve()),
        "device": device,
        "input_clips": len(rows),
        "accepted_clips": sum(row["gallery_clip_status"] == "accepted" for row in audit),
        "roles": sorted(grouped),
        "usable_roles": usable_roles,
        "role_status": role_status,
        "role_coverage": role_coverage,
        "enrollment_episodes": enrollment_episodes,
        "selection_authority": "user",
        "processing_domains": processing_domains or ["speech_dominant"],
        "separator_ids": separator_ids,
        "gallery_issues": gallery_issues,
        "clip_issues": manifest_issues,
        "workflow_stage": "beta1.6",
        "scoring_mode": args.scoring_mode,
        "voice_evidence_authority": "supporting",
        "automatic_identity_assignment": False,
        "gallery_ready": gallery_ready,
        "role_scoring_ready": role_scoring_ready,
        # Compatibility fields for older preparation packages.  They do not
        # define a second active workflow.
        "production_ready": False,
        "diagnostic_only": True,
    }
    if args.audit_tsv:
        write_tsv(Path(args.audit_tsv), audit, list(audit[0]), args.overwrite)
    report_path = Path(args.report) if args.report else Path(args.out_gallery).with_suffix(".report.json")
    write_json(report_path, report, args.overwrite)
    if not role_scoring_ready and not args.allow_degraded:
        raise VoiceError(f"Gallery QC failed; inspect {report_path}")
    if not role_scoring_ready or centroids is None or sim_thresholds is None or margin_thresholds is None:
        raise VoiceError("Gallery has insufficient valid roles")
    model_path = Path(args.model)
    model_fingerprint = path_fingerprint(model_path)[:16]
    exemplar_mode = args.scoring_mode == "exemplar_max"
    gallery_id = hashlib.sha256(
        (
            file_sha256(manifest) + model_fingerprint + json.dumps(usable_roles, ensure_ascii=False)
            + ("|exemplar_max" if exemplar_mode else "")
        ).encode("utf-8")
    ).hexdigest()[:16]
    out = Path(args.out_gallery)
    if out.exists() and not args.overwrite:
        raise VoiceError(f"Output exists: {out}. Use --overwrite or a new path.")
    out.parent.mkdir(parents=True, exist_ok=True)
    exemplar_arrays: dict[str, Any] = {}
    if exemplar_mode:
        exemplar_arrays = {
            "scoring_mode": np.asarray(["exemplar_max"]),
            "exemplars": np.stack([item["vector"] for role in usable_roles for item in retained[role]]),
            "exemplar_roles": np.asarray([role for role in usable_roles for _ in retained[role]]),
        }
    np.savez_compressed(
        out,
        schema_version=np.asarray([SCHEMA_VERSION]),
        gallery_id=np.asarray([gallery_id]),
        model_fingerprint=np.asarray([model_fingerprint]),
        model_kind=np.asarray([args.model_kind]),
        gallery_ready=np.asarray([gallery_ready]),
        role_scoring_ready=np.asarray([role_scoring_ready]),
        processing_domain=np.asarray([(processing_domains or ["speech_dominant"])[0]]),
        separator_id=np.asarray([separator_ids[0] if len(separator_ids) == 1 else ""]),
        enrollment_episodes_json=np.asarray([json.dumps(enrollment_episodes, ensure_ascii=False)]),
        roles=np.asarray(usable_roles), centroids=centroids,
        role_similarity_thresholds=np.asarray(sim_thresholds, dtype=np.float32),
        role_margin_thresholds=np.asarray(margin_thresholds, dtype=np.float32),
        role_clip_counts=np.asarray([len(retained[role]) for role in usable_roles], dtype=np.int32),
        **exemplar_arrays,
    )
    print(json.dumps({"gallery": str(out), "gallery_id": gallery_id, "roles": len(usable_roles), "usable_roles": usable_roles, "accepted_clips": report["accepted_clips"], "device": device}, ensure_ascii=False))
    return 0


def resolve_semantic_unit_ids(labels: list[dict[str, str]]) -> dict[str, str]:
    existing = {
        (row.get("source_index") or "").strip(): (row.get("semantic_unit") or "").strip()
        for row in labels
    }
    if existing and all(existing.values()):
        return existing
    tool_path = Path(__file__).with_name("dubbing_tool.py")
    spec = importlib.util.spec_from_file_location("dubbing_tool_for_voice_units", tool_path)
    if spec is None or spec.loader is None:
        raise VoiceError(f"Could not load semantic-unit logic from {tool_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        inferred = module.infer_semantic_unit_ids(labels)
    except Exception as exc:
        raise VoiceError(f"Could not infer semantic units from legacy label TSV: {exc}") from exc
    return {
        (row.get("source_index") or "").strip(): existing.get((row.get("source_index") or "").strip())
        or inferred[int(row["source_index"])]
        for row in labels
    }


def prepare_manifest_rows(labels: list[dict[str, str]], media: Path, episode: str, gallery: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    anonymous = bool(getattr(args, "anonymous_turns", False))
    if anonymous:
        indices = [(row.get("source_index") or "").strip() for row in labels]
        if any(not value for value in indices) or len(indices) != len(set(indices)):
            raise VoiceError("Anonymous turn input requires unique non-empty source_index for every subtitle row")
    groups: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    unit_ids = resolve_semantic_unit_ids(labels)
    for row in labels:
        source_index = (row.get("source_index") or "").strip()
        unit = unit_ids[source_index]
        if unit not in groups:
            groups[unit] = []
            order.append(unit)
        groups[unit].append(row)
    output: list[dict[str, Any]] = []
    gallery_roles = set(gallery["roles"])
    enrollment_episodes = {canonical_episode(value) for value in gallery.get("enrollment_episodes", [])}
    acoustic_mode = any(
        (row.get("acoustic_turn_id") or "").strip()
        or (row.get("acoustic_turn_status") or "").strip()
        for row in labels
    )
    if acoustic_mode:
        # A semantic unit may be split by a source-language speaker change, while
        # one source-language turn may span several Japanese subtitle units.  For
        # safe rows, score the latter only once by its explicit acoustic-turn ID.
        groups, order = {}, []
        for row in labels:
            turn_id = (row.get("acoustic_turn_id") or "").strip()
            key = f"turn:{turn_id}" if turn_id else f"semantic:{unit_ids[(row.get('source_index') or '').strip()]}"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
    for unit in order:
        rows = groups[unit]
        indices = [str(row.get("source_index") or "").strip() for row in rows]
        roles = {(row.get("role") or "").strip() for row in rows if (row.get("role") or "").strip()}
        statuses = {(row.get("status") or "").strip() for row in rows}
        if anonymous:
            try:
                intervals = {(parse_time_seconds(row.get("acoustic_turn_start", "")), parse_time_seconds(row.get("acoustic_turn_end", ""))) for row in rows}
            except (VoiceError, ValueError, OverflowError) as exc:
                raise VoiceError(f"Anonymous acoustic turn {unit} requires explicit acoustic_turn_start/end on every source row") from exc
            if len(intervals) != 1:
                raise VoiceError(f"Anonymous acoustic turn {unit} has inconsistent reviewed acoustic_turn_start/end")
            start, end = next(iter(intervals))
            if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
                raise VoiceError(f"Anonymous acoustic turn {unit} has invalid acoustic_turn_start/end")
        else:
            start = min(parse_time_seconds(row.get("start", "")) for row in rows)
            end = max(parse_time_seconds(row.get("end", "")) for row in rows)
        role = next(iter(roles)) if len(roles) == 1 and not anonymous else ""
        eligibility = "eligible"
        reason = "reviewed_anonymous_acoustic_turn" if anonymous else "verified_named_role_with_gallery"
        acoustic_ids = {(row.get("acoustic_turn_id") or "").strip() for row in rows}
        acoustic_statuses = {(row.get("acoustic_turn_status") or "").strip() for row in rows}
        alignments = {(row.get("semantic_acoustic_alignment") or "").strip() for row in rows}
        if not canonical_episode(episode):
            eligibility, reason = "missing_episode", "evaluation episode is required for leakage protection"
        elif anonymous and (not acoustic_mode or any(not (row.get("acoustic_reviewed_by") or "").strip() for row in rows)):
            eligibility, reason = "acoustic_turn_unreviewed", "anonymous scoring requires explicit acoustic_reviewed_by on every source row"
        elif acoustic_mode and (not acoustic_ids or "" in acoustic_ids or not acoustic_statuses or "" in acoustic_statuses):
            eligibility, reason = "acoustic_turn_unreviewed", "voice scoring requires a reviewed single-speaker acoustic_turn"
        elif acoustic_mode and (len(acoustic_ids) != 1 or len(acoustic_statuses) != 1 or acoustic_statuses != {"single_speaker"}):
            eligibility, reason = "acoustic_turn_mixed", "semantic unit does not map to one reviewed single-speaker acoustic turn"
        elif acoustic_mode and any(value in {"semantic_split", "mixed", "review"} for value in alignments):
            eligibility, reason = "acoustic_turn_mixed", "split or unresolved semantic/acoustic alignment must be probed per acoustic turn"
        elif not anonymous and statuses - {"manual"}:
            eligibility, reason = "not_reviewed", "all component rows must be manual"
        elif not anonymous and len(roles) != 1:
            eligibility, reason = "mixed_unit", "semantic unit contains multiple or missing roles"
        elif not anonymous and is_generic_role(role):
            eligibility, reason = "generic_role", "generic roles do not have stable identity galleries"
        elif end - start < args.min_duration:
            eligibility, reason = "too_short", f"duration below {args.min_duration:.3f}s"
        elif not anonymous and role not in gallery_roles:
            eligibility, reason = "no_gallery", "reviewed role has no gallery centroid"
        elif canonical_episode(episode) in enrollment_episodes:
            eligibility, reason = "same_episode_leakage", "evaluation episode occurs in enrollment gallery"
        semantic_units = []
        for row in rows:
            semantic_id = unit_ids[(row.get("source_index") or "").strip()]
            if semantic_id not in semantic_units:
                semantic_units.append(semantic_id)
        acoustic_turn_id = next(iter(acoustic_ids)) if len(acoustic_ids) == 1 else ""
        output.append({
            "episode": episode, "source_indices": ",".join(indices), "semantic_unit": ",".join(semantic_units),
            "acoustic_turn_id": acoustic_turn_id,
            "acoustic_reviewed_by": "|".join(sorted({(row.get("acoustic_reviewed_by") or "").strip() for row in rows})),
            "expected_role": role, "media_path": str(media.resolve()),
            "start": f"{start:.3f}", "end": f"{end:.3f}", "duration": f"{end - start:.3f}",
            "eligibility": eligibility, "eligibility_reason": reason,
        })
    return output


MANIFEST_FIELDS = [
    "episode", "source_indices", "semantic_unit", "acoustic_turn_id", "acoustic_reviewed_by", "expected_role", "media_path",
    "start", "end", "duration", "eligibility", "eligibility_reason",
]


def cmd_prepare_manifest(args: argparse.Namespace) -> int:
    try:
        import numpy as np
    except ImportError as exc:
        raise VoiceError("Preparing a score manifest requires numpy to read the gallery") from exc
    gallery = gallery_payload(np, Path(args.gallery))
    labels = read_tsv(Path(args.labels))
    rows = prepare_manifest_rows(labels, Path(args.media), str(args.episode), gallery, args)
    write_tsv(Path(args.out_tsv), rows, MANIFEST_FIELDS, args.overwrite)
    counts = Counter(row["eligibility"] for row in rows)
    print(json.dumps({"output": args.out_tsv, "semantic_units": len(rows), "eligibility": dict(counts)}, ensure_ascii=False))
    return 0


VOICE_OUTPUT_FIELDS = [
    "episode", "source_indices", "semantic_unit", "acoustic_turn_id", "acoustic_reviewed_by", "expected_role", "voice_status",
    "voice_candidate", "voice_similarity", "voice_second_candidate", "voice_second_similarity",
    "voice_margin", "voice_duration", "voice_quality_rms_dbfs", "voice_quality_active_ratio",
    "voice_quality_clipping_ratio", "voice_decision_reason", "voice_gallery_id", "voice_error",
    "voice_model_kind", "voice_model_fingerprint", "voice_gallery_roles",
]


def score_manifest_rows(rows: list[dict[str, str]], gallery: dict[str, Any], np: Any, inference: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    roles = gallery["roles"]
    output: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="voice_score_") as temp:
        temp_dir = Path(temp)
        for row in rows:
            status = (row.get("eligibility") or "eligible").strip()
            candidate = second = ""
            similarity = second_similarity = margin = ""
            duration = row.get("duration", "")
            quality: dict[str, float] = {}
            reason = (row.get("eligibility_reason") or "").strip()
            error = ""
            if status == "eligible":
                if not canonical_episode(row.get("episode")):
                    status, reason = "missing_episode", "evaluation episode is required for leakage protection"
                elif canonical_episode(row.get("episode")) in {canonical_episode(item) for item in gallery.get("enrollment_episodes", [])}:
                    status, reason = "same_episode_leakage", "evaluation episode occurs in enrollment gallery"
                else:
                    try:
                        vector, actual_duration, quality = embed_row(np, inference, row, temp_dir, args)
                        duration = f"{actual_duration:.3f}"
                        if actual_duration < args.min_duration:
                            status, reason = "too_short", f"duration below {args.min_duration:.3f}s"
                        else:
                            ranked = rank_gallery_roles(np, vector, gallery)
                            candidate, top1 = ranked[0]
                            second, top2 = ranked[1] if len(ranked) > 1 else ("", -1.0)
                            similarity, second_similarity, margin = f"{top1:.6f}", f"{top2:.6f}", f"{top1 - top2:.6f}"
                            sim_gate = max(args.min_similarity, float(gallery["role_similarity_thresholds"].get(candidate, 0.0)))
                            margin_gate = max(args.min_margin, float(gallery["role_margin_thresholds"].get(candidate, 0.0)))
                            if top1 < sim_gate:
                                status, reason = "low_similarity", f"top1 {top1:.4f} below gate {sim_gate:.4f}"
                            elif top1 - top2 < margin_gate:
                                status, reason = "ambiguous", f"margin {top1 - top2:.4f} below gate {margin_gate:.4f}"
                            else:
                                status, reason = "eligible", f"passed similarity {sim_gate:.4f} and margin {margin_gate:.4f} gates"
                    except VoiceError as exc:
                        error = str(exc)
                        if error.startswith("audio_quality:"):
                            status, reason = "low_quality", error.split(":", 1)[1]
                        else:
                            status, reason = "error", "embedding_or_audio_extraction_failed"
            keep_candidates = status == "eligible" or status in ADVISORY_STATUSES
            output.append({
                "episode": row.get("episode", ""), "source_indices": row.get("source_indices") or row.get("source_index", ""),
                "semantic_unit": row.get("semantic_unit", ""), "acoustic_turn_id": row.get("acoustic_turn_id", ""),
                "expected_role": row.get("expected_role", ""),
                "acoustic_reviewed_by": row.get("acoustic_reviewed_by", ""),
                "voice_status": status,
                "voice_candidate": candidate if keep_candidates else "",
                "voice_similarity": similarity if keep_candidates else "",
                "voice_second_candidate": second if keep_candidates else "",
                "voice_second_similarity": second_similarity if keep_candidates else "",
                "voice_margin": margin if keep_candidates else "",
                "voice_duration": duration,
                "voice_quality_rms_dbfs": f"{quality['rms_dbfs']:.3f}" if quality else "",
                "voice_quality_active_ratio": f"{quality['active_ratio']:.6f}" if quality else "",
                "voice_quality_clipping_ratio": f"{quality['clipping_ratio']:.8f}" if quality else "",
                "voice_decision_reason": reason,
                "voice_gallery_id": gallery["gallery_id"],
                "voice_error": error,
                "voice_model_kind": gallery.get("model_kind", ""),
                "voice_model_fingerprint": gallery.get("model_fingerprint", ""),
                "voice_gallery_roles": "|".join(roles),
            })
    return output


CLUSTER_ROW_FIELDS = [
    "episode", "source_index", "anonymous_speaker", "acoustic_turn_id", "row_status",
    "row_duration", "row_cluster_similarity", "row_voice_candidate", "row_voice_similarity",
    "row_voice_margin", "row_outlier", "row_reason", "voice_model_kind", "voice_gallery_id",
]
ANONYMOUS_SPEAKER_RE = re.compile(r"Speaker[0-9]{2,}")


def voice_gates(gallery: dict[str, Any], candidate: str, args: argparse.Namespace) -> tuple[float, float]:
    return (
        max(args.min_similarity, float(gallery["role_similarity_thresholds"].get(candidate, 0.0))),
        max(args.min_margin, float(gallery["role_margin_thresholds"].get(candidate, 0.0))),
    )


def score_cluster_rows(
    rows: list[dict[str, str]],
    media: Path,
    episode: str,
    gallery: dict[str, Any],
    np: Any,
    embed: Any,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Score each episode-local Speaker cluster as one duration-weighted aggregate.

    The result is a candidate for the whole cluster plus per-row outlier flags; it
    never assigns a role.  Rows the diarization step did not mark single_speaker
    stay outside every aggregate and require row-level review.
    """
    episode_label = str(episode).strip()
    episode_key = canonical_episode(episode_label)
    if not episode_key:
        raise VoiceError("Cluster scoring requires an evaluation episode")
    leakage = episode_key in {canonical_episode(item) for item in gallery.get("enrollment_episodes", [])}
    clusters: dict[str, list[dict[str, Any]]] = {}
    details: list[dict[str, Any]] = []
    for row in rows:
        if canonical_episode(row.get("episode")) != episode_key:
            raise VoiceError(f"Anonymous script row {row.get('source_index')} belongs to another episode")
        speaker = (row.get("anonymous_speaker") or "").strip()
        diarization_status = (row.get("diarization_status") or "").strip()
        detail: dict[str, Any] = {
            "episode": row.get("episode", ""),
            "source_index": (row.get("source_index") or "").strip(),
            "anonymous_speaker": speaker, "acoustic_turn_id": "", "row_status": "",
            "row_duration": "", "row_cluster_similarity": "", "row_voice_candidate": "",
            "row_voice_similarity": "", "row_voice_margin": "", "row_outlier": "unknown", "row_reason": "",
            "voice_model_kind": gallery.get("model_kind", ""), "voice_gallery_id": gallery["gallery_id"],
        }
        details.append(detail)
        if not ANONYMOUS_SPEAKER_RE.fullmatch(speaker) or diarization_status != "single_speaker":
            detail.update(row_status="needs_row_review", row_reason=f"diarization_status={diarization_status or 'missing'}")
            continue
        detail["acoustic_turn_id"] = f"cluster:{episode_label}:{speaker}"
        clusters.setdefault(speaker, []).append({"row": row, "detail": detail})

    output: list[dict[str, Any]] = []
    for speaker, members in clusters.items():
        candidate = second = similarity = second_similarity = margin = error = ""
        total_seconds = 0.0
        accepted: list[dict[str, Any]] = []
        if leakage:
            status, reason = "same_episode_leakage", "evaluation episode occurs in enrollment gallery"
            for member in members:
                member["detail"].update(row_status="same_episode_leakage", row_reason=reason)
        else:
            failures: list[str] = []
            for member in members:
                row, detail = member["row"], member["detail"]
                clip = {"media_path": str(media), "start": row.get("start_timecode", ""), "end": row.get("end_timecode", "")}
                try:
                    vector, duration, _ = embed(clip)
                except VoiceError as exc:
                    text = str(exc)
                    kind = "low_quality" if text.startswith("audio_quality:") else "error"
                    failures.append(kind if kind == "low_quality" else text)
                    detail.update(row_status=kind, row_reason=text)
                    continue
                detail["row_duration"] = f"{duration:.3f}"
                if duration < args.min_cue_duration:
                    detail.update(row_status="too_short", row_reason=f"duration below {args.min_cue_duration:.3f}s")
                    continue
                accepted.append({"vector": vector, "duration": duration, "detail": detail})
            total_seconds = sum(item["duration"] for item in accepted)
            if not accepted:
                errors = [value for value in failures if value != "low_quality"]
                if errors:
                    status, reason, error = "error", "embedding_or_audio_extraction_failed", errors[0]
                elif failures:
                    status, reason = "low_quality", "no cluster row passed the audio-quality gate"
                else:
                    status, reason = "too_short", f"no cluster row reached {args.min_cue_duration:.3f}s"
            elif total_seconds < args.min_cluster_seconds:
                status, reason = "too_short", f"accepted cluster speech {total_seconds:.3f}s below {args.min_cluster_seconds:.3f}s"
            else:
                weighted_sum = np.sum([item["vector"] * item["duration"] for item in accepted], axis=0)
                aggregate = normalize(np, weighted_sum)
                ranked = rank_gallery_roles(np, aggregate, gallery)
                candidate, top1 = ranked[0]
                second, top2 = ranked[1] if len(ranked) > 1 else ("", -1.0)
                similarity, second_similarity, margin = f"{top1:.6f}", f"{top2:.6f}", f"{top1 - top2:.6f}"
                sim_gate, margin_gate = voice_gates(gallery, candidate, args)
                agreeing = 0
                for item in accepted:
                    detail = item["detail"]
                    row_ranked = rank_gallery_roles(np, item["vector"], gallery)
                    row_candidate, row_top1 = row_ranked[0]
                    row_top2 = row_ranked[1][1] if len(row_ranked) > 1 else -1.0
                    cluster_similarity: float | None = None
                    if len(accepted) > 1:
                        rest = weighted_sum - item["vector"] * item["duration"]
                        if float(np.linalg.norm(rest)) > 1e-8:
                            cluster_similarity = float(np.dot(item["vector"], normalize(np, rest)))
                    row_sim_gate, row_margin_gate = voice_gates(gallery, row_candidate, args)
                    strong_disagreement = (
                        row_candidate != candidate and row_top1 >= row_sim_gate
                        and row_top1 - row_top2 >= row_margin_gate
                    )
                    weak_member = cluster_similarity is not None and cluster_similarity < args.row_outlier_threshold
                    agreeing += int(row_candidate == candidate)
                    detail.update(
                        row_status="scored",
                        row_cluster_similarity=f"{cluster_similarity:.6f}" if cluster_similarity is not None else "",
                        row_voice_candidate=row_candidate,
                        row_voice_similarity=f"{row_top1:.6f}",
                        row_voice_margin=f"{row_top1 - row_top2:.6f}",
                        row_outlier="true" if strong_disagreement or weak_member else "false",
                        row_reason=(
                            "row voice strongly prefers another role" if strong_disagreement
                            else "row is far from the rest of its cluster" if weak_member
                            else "consistent with cluster"
                        ),
                    )
                agreement = agreeing / len(accepted)
                if top1 < sim_gate:
                    status, reason = "low_similarity", f"cluster top1 {top1:.4f} below gate {sim_gate:.4f}"
                elif top1 - top2 < margin_gate:
                    status, reason = "ambiguous", f"cluster margin {top1 - top2:.4f} below gate {margin_gate:.4f}"
                elif agreement < args.min_row_agreement:
                    status, reason = "ambiguous", f"row agreement {agreement:.2f} below {args.min_row_agreement:.2f}"
                else:
                    status, reason = "eligible", (
                        f"cluster passed similarity {sim_gate:.4f} and margin {margin_gate:.4f} gates; "
                        f"row agreement {agreement:.2f} over {len(accepted)} rows / {total_seconds:.1f}s"
                    )
        keep = status == "eligible" or status in ADVISORY_STATUSES
        output.append({
            "episode": episode_label,
            "source_indices": ",".join(member["detail"]["source_index"] for member in members),
            "semantic_unit": "",
            "acoustic_turn_id": f"cluster:{episode_label}:{speaker}",
            "acoustic_reviewed_by": "subtitle-guided-cluster",
            "expected_role": "",
            "voice_status": status,
            "voice_candidate": candidate if keep else "",
            "voice_similarity": similarity if keep else "",
            "voice_second_candidate": second if keep else "",
            "voice_second_similarity": second_similarity if keep else "",
            "voice_margin": margin if keep else "",
            "voice_duration": f"{total_seconds:.3f}",
            "voice_quality_rms_dbfs": "", "voice_quality_active_ratio": "", "voice_quality_clipping_ratio": "",
            "voice_decision_reason": reason,
            "voice_gallery_id": gallery["gallery_id"],
            "voice_error": error,
            "voice_model_kind": gallery.get("model_kind", ""),
            "voice_model_fingerprint": gallery.get("model_fingerprint", ""),
            "voice_gallery_roles": "|".join(gallery["roles"]),
        })
    return output, details


def cmd_score_clusters(args: argparse.Namespace) -> int:
    np, inference, device = load_runtime(Path(args.model), args.device, args.model_kind)
    gallery = gallery_payload(np, Path(args.gallery))
    validate_gallery_model(gallery, Path(args.model), args.model_kind)
    rows = read_tsv(Path(args.anonymous_script))
    media = Path(args.media).resolve()
    if not media.is_file():
        raise VoiceError(f"Media not found: {media}")
    with tempfile.TemporaryDirectory(prefix="voice_clusters_") as temp:
        temp_dir = Path(temp)
        clusters, details = score_cluster_rows(
            rows, media, str(args.episode), gallery, np,
            lambda clip: embed_row(np, inference, clip, temp_dir, args), args,
        )
    write_tsv(Path(args.out_tsv), clusters, VOICE_OUTPUT_FIELDS, args.overwrite)
    write_tsv(Path(args.out_rows_tsv), details, CLUSTER_ROW_FIELDS, args.overwrite)
    print(json.dumps({
        "output": args.out_tsv,
        "row_output": args.out_rows_tsv,
        "clusters": len(clusters),
        "statuses": dict(Counter(row["voice_status"] for row in clusters)),
        "row_outliers": sum(row["row_outlier"] == "true" for row in details),
        "rows_needing_row_review": sum(row["row_outlier"] != "false" for row in details),
        "gallery_id": gallery["gallery_id"],
        "scoring_mode": gallery.get("scoring_mode", "centroid"),
        "device": device,
        "automatic_identity_assignment": False,
    }, ensure_ascii=False))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    np, inference, device = load_runtime(Path(args.model), args.device, args.model_kind)
    gallery = gallery_payload(np, Path(args.gallery))
    validate_gallery_model(gallery, Path(args.model), args.model_kind)
    rows = read_tsv(Path(args.manifest))
    output = score_manifest_rows(rows, gallery, np, inference, args)
    write_tsv(Path(args.out_tsv), output, VOICE_OUTPUT_FIELDS, args.overwrite)
    counts = Counter(row["voice_status"] for row in output)
    summary = {"output": args.out_tsv, "semantic_units": len(output), "statuses": dict(counts), "gallery_id": gallery["gallery_id"], "device": device}
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if counts.get("error") else 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Compare one complete ambiguous interval against a leakage-safe gallery."""
    np, inference, device = load_runtime(Path(args.model), args.device, args.model_kind)
    gallery = gallery_payload(np, Path(args.gallery))
    validate_gallery_model(gallery, Path(args.model), args.model_kind)
    start = parse_time_seconds(args.start)
    end = parse_time_seconds(args.end)
    if end <= start:
        raise VoiceError("Probe end must be later than start")
    manifest_row = {
        "episode": str(args.episode),
        "source_indices": args.source_indices or "probe",
        "semantic_unit": args.semantic_unit or "probe",
        "acoustic_turn_id": args.acoustic_turn_id or "manual_probe",
        "expected_role": args.expected_role or "",
        "media_path": str(Path(args.media).resolve()),
        "start": f"{start:.3f}",
        "end": f"{end:.3f}",
        "duration": f"{end - start:.3f}",
        "eligibility": "eligible",
        "eligibility_reason": "manual_ambiguous_interval_probe",
    }
    result = score_manifest_rows([manifest_row], gallery, np, inference, args)[0]
    payload = {
        **result,
        "device": device,
        "policy": "Advisory only. Use top-1/top-2 as support or a conflict trigger; never auto-assign a role.",
    }
    if args.out_tsv:
        write_tsv(Path(args.out_tsv), [result], VOICE_OUTPUT_FIELDS, args.overwrite)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if result["voice_status"] == "error" else 0


def cmd_audit(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "semantic_units.tsv"
    evidence_path = out_dir / "voice_evidence.tsv"
    merged_path = out_dir / "final_roles_with_voice.tsv"
    summary_path = out_dir / "voice_audit_summary.json"
    np, inference, device = load_runtime(Path(args.model), args.device, args.model_kind)
    gallery = gallery_payload(np, Path(args.gallery))
    validate_gallery_model(gallery, Path(args.model), args.model_kind)
    labels = read_tsv(Path(args.labels))
    manifest_rows = prepare_manifest_rows(labels, Path(args.media), str(args.episode), gallery, args)
    write_tsv(manifest_path, manifest_rows, MANIFEST_FIELDS, args.overwrite)
    evidence_rows = score_manifest_rows(manifest_rows, gallery, np, inference, args)
    write_tsv(evidence_path, evidence_rows, VOICE_OUTPUT_FIELDS, args.overwrite)
    merge_command = [
        sys.executable, str(Path(__file__).with_name("dubbing_tool.py")), "merge-voice-evidence",
        "--tsv", str(Path(args.labels)), "--voice-tsv", str(evidence_path), "--out-tsv", str(merged_path),
    ]
    if args.overwrite:
        merge_command.append("--overwrite")
    completed = subprocess.run(merge_command, check=False, capture_output=True, text=True)
    if completed.returncode not in {0, 2}:
        raise VoiceError(f"Could not merge voice evidence: {(completed.stderr or completed.stdout).strip()}")
    counts = Counter(row["voice_status"] for row in evidence_rows)
    conflicts = sum(
        row["voice_status"] == "eligible" and row.get("voice_candidate") and row.get("expected_role")
        and row["voice_candidate"].casefold() != row["expected_role"].casefold()
        for row in evidence_rows
    )
    summary = {
        "episode": str(args.episode), "gallery_id": gallery["gallery_id"], "device": device,
        "semantic_units": len(manifest_rows), "statuses": dict(counts), "eligible_conflicts": conflicts,
        "manifest": str(manifest_path.resolve()), "evidence": str(evidence_path.resolve()),
        "merged_tsv": str(merged_path.resolve()), "review_required": conflicts > 0,
    }
    write_json(summary_path, summary, args.overwrite)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if conflicts or counts.get("error") else 0


def cmd_preflight(args: argparse.Namespace) -> int:
    checks: dict[str, Any] = {
        "ffmpeg": shutil.which(args.ffmpeg) or (str(Path(args.ffmpeg).resolve()) if Path(args.ffmpeg).is_file() else ""),
        "model_exists": Path(args.model).exists(),
        "gallery_exists": Path(args.gallery).is_file() if args.gallery else None,
    }
    try:
        np, _, device = load_runtime(Path(args.model), args.device, args.model_kind)
        checks["runtime"] = "ok"
        checks["device"] = device
        if args.gallery:
            gallery = gallery_payload(np, Path(args.gallery))
            validate_gallery_model(gallery, Path(args.model), args.model_kind)
            checks["gallery_id"] = gallery["gallery_id"]
            checks["gallery_roles"] = len(gallery["roles"])
            report_path = Path(args.gallery_report) if args.gallery_report else Path(args.gallery).with_suffix(".report.json")
            checks["gallery_report"] = str(report_path.resolve())
            if report_path.is_file():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    checks["workflow_stage"] = report.get("workflow_stage", "legacy")
                    checks["gallery_ready"] = (
                        report.get("gallery_ready") is True
                        or report.get("production_ready") is True
                    )
                    checks["role_scoring_ready"] = (
                        report.get("role_scoring_ready") is True
                        or checks["gallery_ready"]
                    )
                    checks["usable_roles"] = report.get("usable_roles", report.get("roles", []))
                    if not checks["role_scoring_ready"]:
                        checks["gallery_issues"] = report.get("gallery_issues", [])
                except (OSError, json.JSONDecodeError) as exc:
                    checks["gallery_ready"] = False
                    checks["gallery_report_error"] = str(exc)
            else:
                checks["gallery_ready"] = False
                checks["gallery_report_missing"] = True
    except VoiceError as exc:
        checks["runtime"] = "error"
        checks["error"] = str(exc)
    ready = bool(checks["ffmpeg"] and checks["model_exists"] and checks.get("runtime") == "ok" and (not args.gallery or checks["gallery_exists"]))
    checks["runtime_ready"] = ready
    checks["ready"] = bool(ready and (not args.gallery or checks.get("role_scoring_ready") is True))
    # Compatibility fields for older handoff readers.
    checks["production_runtime_ready"] = ready
    checks["production_ready"] = False
    checks["skill_version"] = SKILL_VERSION
    checks["workflow_version"] = SKILL_VERSION
    checks["model_kind"] = args.model_kind
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["ready"] else 2


def add_quality_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BINARY", "ffmpeg"))
    parser.add_argument("--min-rms-dbfs", type=float, default=-45.0)
    parser.add_argument("--min-active-ratio", type=float, default=0.30)
    parser.add_argument("--max-clipping-ratio", type=float, default=0.005)


def add_score_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-duration", type=float, default=1.5)
    parser.add_argument("--min-similarity", type=float, default=0.35)
    parser.add_argument("--min-margin", type=float, default=0.08)
    parser.add_argument("--device", default="auto")
    add_quality_arguments(parser)


def cmd_self_test(_: argparse.Namespace) -> int:
    try:
        import numpy as np
    except ImportError as exc:
        raise VoiceError("Self-test requires numpy") from exc
    roles = ["A", "B", "C"]
    centroids = np.stack([normalize(np, [1.0, 0.0, 0.0]), normalize(np, [0.0, 1.0, 0.0]), normalize(np, [0.0, 0.0, 1.0])])
    ranked = rank_candidates(np, normalize(np, [0.1, 0.9, 0.0]), roles, centroids)
    assert ranked[0][0] == "B" and ranked[0][1] > ranked[1][1]
    gallery = {
        "roles": ["A", "B"], "enrollment_episodes": ["1"], "gallery_id": "test",
        "role_similarity_thresholds": {"A": 0.5, "B": 0.5}, "role_margin_thresholds": {"A": 0.1, "B": 0.1},
    }
    labels = [
        {"source_index": "1", "semantic_unit": "u1", "role": "A", "status": "manual", "start": "00:00:01,000", "end": "00:00:02,800"},
        {"source_index": "2", "semantic_unit": "u2", "role": "男性音声01", "status": "manual", "start": "00:00:03,000", "end": "00:00:05,000"},
    ]
    args = argparse.Namespace(min_duration=1.5)
    rows = prepare_manifest_rows(labels, Path(__file__), "2", gallery, args)
    assert rows[0]["eligibility"] == "eligible"
    assert rows[1]["eligibility"] == "generic_role"
    leaked = prepare_manifest_rows(labels[:1], Path(__file__), "1", gallery, args)
    assert leaked[0]["eligibility"] == "same_episode_leakage"
    acoustic = [
        dict(labels[0], acoustic_turn_id="at01", acoustic_turn_status="single_speaker", semantic_acoustic_alignment="aligned"),
        dict(labels[1], acoustic_turn_id="at01", acoustic_turn_status="single_speaker", semantic_acoustic_alignment="semantic_merge", role="A"),
    ]
    acoustic_rows = prepare_manifest_rows(acoustic, Path(__file__), "2", gallery, args)
    assert len(acoustic_rows) == 1 and acoustic_rows[0]["acoustic_turn_id"] == "at01"
    split = [dict(labels[0], acoustic_turn_id="at02", acoustic_turn_status="single_speaker", semantic_acoustic_alignment="semantic_split")]
    split_rows = prepare_manifest_rows(split, Path(__file__), "2", gallery, args)
    assert split_rows[0]["eligibility"] == "acoustic_turn_mixed"
    legacy = [dict(labels[0], semantic_unit=""), dict(labels[1], semantic_unit="")]
    inferred = resolve_semantic_unit_ids(legacy)
    assert set(inferred) == {"1", "2"} and all(inferred.values())

    # Experimental exemplar gallery: nearest stored clip wins even when the role mean is far away.
    exemplar_gallery = {
        "roles": ["A", "B"], "scoring_mode": "exemplar_max",
        "centroids": np.stack([normalize(np, [1.0, 0.0, 1.0]), normalize(np, [0.0, 0.6, 0.8])]),
        "exemplars": np.stack([normalize(np, [1.0, 0.0, 0.0]), normalize(np, [0.0, 0.0, 1.0]), normalize(np, [0.0, 0.6, 0.8])]),
        "exemplar_roles": ["A", "A", "B"],
    }
    assert rank_gallery_roles(np, normalize(np, [0.1, 0.1, 1.0]), exemplar_gallery)[0][0] == "A"
    assert rank_gallery_roles(np, normalize(np, [0.1, 0.1, 1.0]), dict(exemplar_gallery, scoring_mode="centroid"))[0][0] == "B"

    # Experimental cluster scoring with a deterministic fake encoder.
    cluster_gallery = {
        "roles": ["A", "B"], "enrollment_episodes": ["1"], "gallery_id": "g", "model_kind": "campplus",
        "model_fingerprint": "f", "centroids": np.stack([normalize(np, [1.0, 0.0, 0.0]), normalize(np, [0.0, 1.0, 0.0])]),
        "role_similarity_thresholds": {"A": 0.5, "B": 0.5}, "role_margin_thresholds": {"A": 0.1, "B": 0.1},
    }
    vectors = {
        "00:00:01,000": ([1.0, 0.05, 0.0], 1.5), "00:00:03,000": ([0.95, 0.1, 0.05], 1.2),
        "00:00:05,000": ([0.0, 1.0, 0.0], 1.4), "00:00:07,000": ([1.0, 0.0, 0.1], 0.3),
        "00:00:09,000": ([0.0, 1.0, 0.1], 2.5), "00:00:11,000": ([0.1, 1.0, 0.0], 2.0),
    }

    def fake_embed(clip: dict[str, str]):
        vector, duration = vectors[clip["start"]]
        return normalize(np, vector), duration, {}

    script = [
        {"episode": "0002", "source_index": str(i + 1), "anonymous_speaker": speaker, "diarization_status": status,
         "start_timecode": start, "end_timecode": start.replace(",000", ",900")}
        for i, (speaker, status, start) in enumerate([
            ("Speaker01", "single_speaker", "00:00:01,000"), ("Speaker01", "single_speaker", "00:00:03,000"),
            ("Speaker01", "single_speaker", "00:00:05,000"), ("Speaker01", "single_speaker", "00:00:07,000"),
            ("Speaker02", "single_speaker", "00:00:09,000"), ("Speaker02", "single_speaker", "00:00:11,000"),
            ("MULTI_SPEAKER_REVIEW", "dual_dialogue_cue", "00:00:13,000"),
        ])
    ]
    cluster_args = argparse.Namespace(
        min_cue_duration=0.5, min_cluster_seconds=3.0, min_row_agreement=0.6, row_outlier_threshold=0.4,
        min_similarity=0.35, min_margin=0.08,
    )
    cluster_rows, row_details = score_cluster_rows(script, Path(__file__), "0002", cluster_gallery, np, fake_embed, cluster_args)
    by_turn = {row["acoustic_turn_id"]: row for row in cluster_rows}
    assert by_turn["cluster:0002:Speaker01"]["voice_status"] == "eligible"
    assert by_turn["cluster:0002:Speaker01"]["voice_candidate"] == "A"
    assert by_turn["cluster:0002:Speaker01"]["source_indices"] == "1,2,3,4"
    assert by_turn["cluster:0002:Speaker02"]["voice_candidate"] == "B"
    detail = {row["source_index"]: row for row in row_details}
    assert detail["3"]["row_outlier"] == "true", "a B-sounding row inside an A cluster must be flagged"
    assert detail["1"]["row_outlier"] == "false"
    assert detail["4"]["row_status"] == "too_short" and detail["4"]["row_outlier"] == "unknown"
    assert detail["7"]["row_status"] == "needs_row_review"
    leak_script = [dict(row, episode="0001") for row in script]
    leak_clusters, _ = score_cluster_rows(leak_script, Path(__file__), "0001", cluster_gallery, np, fake_embed, cluster_args)
    assert all(row["voice_status"] == "same_episode_leakage" for row in leak_clusters)
    print(
        "Self-test passed: ranking, semantic-unit fallback, acoustic-turn gating/merge, generic-role gating, duration gating, "
        "episode-leakage protection, exemplar ranking, and cluster scoring/outlier flags are valid."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Beta1.0 speaker-embedding evidence for dubbing review")
    sub = parser.add_subparsers(dest="command", required=True)

    gallery = sub.add_parser("build-gallery", help="Build and QC a leakage-audited role gallery")
    gallery.add_argument(
        "--manifest", required=True,
        help="User-labeled TSV: role, episode, media_path/audio_path, verified; start/end optional for pre-cut audio",
    )
    gallery.add_argument("--model", required=True)
    gallery.add_argument("--model-kind", choices=MODEL_KINDS, default="resnet34")
    gallery.add_argument("--out-gallery", required=True)
    gallery.add_argument("--audit-tsv", required=True)
    gallery.add_argument("--report")
    gallery.add_argument("--min-clips-per-role", type=int, default=3, help="Beta minimum retained enrollment clips per named role")
    gallery.add_argument("--min-episodes-per-role", type=int, default=1, help="Minimum distinct confirmed enrollment episodes per named role")
    gallery.add_argument("--min-total-duration-per-role", type=float, default=8.0, help="Minimum retained single-speaker seconds per named role")
    gallery.add_argument("--min-enrollment-duration", type=float, default=2.0)
    gallery.add_argument("--max-enrollment-duration", type=float, default=12.0)
    gallery.add_argument("--min-intra-similarity", type=float, default=0.30)
    gallery.add_argument("--min-gallery-margin", type=float, default=0.08)
    gallery.add_argument("--min-similarity", type=float, default=0.35)
    gallery.add_argument("--min-margin", type=float, default=0.08)
    gallery.add_argument("--threshold-safety-margin", type=float, default=0.03)
    gallery.add_argument("--max-calibrated-margin", type=float, default=0.25)
    gallery.add_argument("--device", default="auto")
    gallery.add_argument("--allow-unverified", action="store_true", help="Migration compatibility only; never bypass manual identity confirmation")
    gallery.add_argument("--allow-degraded", action="store_true", help="Write the Beta gallery for review even when QC issues keep it unready")
    gallery.add_argument(
        "--beta", "--diagnostic", dest="beta", action="store_true", default=True,
        help="Build the user-confirmed Beta supporting-evidence gallery (--diagnostic is a legacy alias)",
    )
    gallery.add_argument(
        "--scoring-mode", choices=["centroid", "exemplar_max"], default="centroid",
        help="centroid: standard one averaged profile per role; exemplar_max: experimental, keep every accepted clip and score against the nearest one",
    )
    gallery.add_argument("--overwrite", action="store_true")
    add_quality_arguments(gallery)
    gallery.set_defaults(func=cmd_build_gallery)

    prepare = sub.add_parser("prepare-manifest", help="Build acoustic-turn scoring input from reviewed role TSV")
    prepare.add_argument("--labels", required=True)
    prepare.add_argument("--media", required=True)
    prepare.add_argument("--episode", required=True)
    prepare.add_argument("--gallery", required=True)
    prepare.add_argument("--out-tsv", required=True)
    prepare.add_argument("--min-duration", type=float, default=1.5)
    prepare.add_argument("--anonymous-turns", action="store_true", help="Score before role assignment: requires reviewed single-speaker acoustic_turn_id/status, consistent acoustic_turn_start/end and acoustic_reviewed_by per source row; expected_role stays empty")
    prepare.add_argument("--overwrite", action="store_true")
    prepare.set_defaults(func=cmd_prepare_manifest)

    score = sub.add_parser("score", help="Score reviewed single-speaker acoustic turns with calibrated rejection gates")
    score.add_argument("--manifest", required=True)
    score.add_argument("--gallery", required=True)
    score.add_argument("--model", required=True)
    score.add_argument("--model-kind", choices=MODEL_KINDS, default="resnet34")
    score.add_argument("--out-tsv", required=True)
    score.add_argument("--overwrite", action="store_true")
    add_score_arguments(score)
    score.set_defaults(func=cmd_score)

    clusters = sub.add_parser(
        "score-clusters",
        help="Experimental: score each episode-local anonymous Speaker cluster as one aggregate and flag outlier rows",
    )
    clusters.add_argument("--anonymous-script", required=True, help="Preparation anonymous_script.tsv built with subtitle-guided segmentation")
    clusters.add_argument("--media", required=True)
    clusters.add_argument("--episode", required=True)
    clusters.add_argument("--gallery", required=True)
    clusters.add_argument("--model", required=True)
    clusters.add_argument("--model-kind", choices=MODEL_KINDS, default="resnet34")
    clusters.add_argument("--out-tsv", required=True, help="One ensemble-compatible evidence row per cluster")
    clusters.add_argument("--out-rows-tsv", required=True, help="Per-row cluster membership, row candidate and outlier flag")
    clusters.add_argument("--min-cue-duration", type=float, default=0.5)
    clusters.add_argument("--min-cluster-seconds", type=float, default=3.0)
    clusters.add_argument("--min-row-agreement", type=float, default=0.6)
    clusters.add_argument("--row-outlier-threshold", type=float, default=0.40)
    clusters.add_argument("--overwrite", action="store_true")
    add_score_arguments(clusters)
    clusters.set_defaults(func=cmd_score_clusters)

    probe = sub.add_parser("probe", help="Compare one reviewed single-speaker acoustic turn with a leakage-safe gallery")
    probe.add_argument("--media", required=True)
    probe.add_argument("--episode", required=True)
    probe.add_argument("--start", required=True)
    probe.add_argument("--end", required=True)
    probe.add_argument("--gallery", required=True)
    probe.add_argument("--model", required=True)
    probe.add_argument("--model-kind", choices=MODEL_KINDS, default="resnet34")
    probe.add_argument("--expected-role")
    probe.add_argument("--semantic-unit")
    probe.add_argument("--acoustic-turn-id", help="Reviewed original-language single-speaker turn ID")
    probe.add_argument("--source-indices")
    probe.add_argument("--out-tsv")
    probe.add_argument("--overwrite", action="store_true")
    add_score_arguments(probe)
    probe.set_defaults(func=cmd_probe)

    audit = sub.add_parser("audit", help="Prepare, score, and merge one episode in one command")
    audit.add_argument("--labels", required=True)
    audit.add_argument("--media", required=True)
    audit.add_argument("--episode", required=True)
    audit.add_argument("--gallery", required=True)
    audit.add_argument("--model", required=True)
    audit.add_argument("--model-kind", choices=MODEL_KINDS, default="resnet34")
    audit.add_argument("--out-dir", required=True)
    audit.add_argument("--overwrite", action="store_true")
    add_score_arguments(audit)
    audit.set_defaults(func=cmd_audit)

    preflight = sub.add_parser("preflight", help="Verify ffmpeg, model runtime, device, and optional gallery")
    preflight.add_argument("--model", required=True)
    preflight.add_argument("--model-kind", choices=MODEL_KINDS, default="resnet34")
    preflight.add_argument("--gallery")
    preflight.add_argument("--gallery-report", help="Gallery QC report; defaults to the gallery path with .report.json suffix")
    preflight.add_argument("--device", default="auto")
    preflight.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BINARY", "ffmpeg"))
    preflight.set_defaults(func=cmd_preflight)

    test = sub.add_parser("self-test", help="Run dependency-light decision and manifest checks")
    test.set_defaults(func=cmd_self_test)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        args = build_parser().parse_args()
        return int(args.func(args))
    except VoiceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
