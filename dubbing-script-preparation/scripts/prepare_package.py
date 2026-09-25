#!/usr/bin/env python3
"""Build a validated dubbing-script preparation handoff package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


FRAMES_DIR = "01_video_frames"
SCRIPTS_DIR = "02_review_work"
CHARACTERS_DIR = "03_character_data"
VOICE_DIR = "04_voice_evidence"
REVIEW_DIR = "05_qc_approval"
HANDOFF_DIR = "06_handoff"
DIAGNOSTIC_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}
WINDOWS_FFMPEG = Path(r"E:\AI_Models\RVC\RVC20240604Nvidia50x0\ffmpeg.exe")


def default_ffmpeg() -> str:
    configured = os.environ.get("FFMPEG_BINARY", "").strip()
    if configured:
        return configured
    if os.name == "nt" and WINDOWS_FFMPEG.is_file():
        return str(WINDOWS_FFMPEG)
    return "ffmpeg"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def enrich_review_queue(
    path: Path,
    gallery_names: list[str],
    speech_dominant_audio: str = "",
    anonymous_speaker_map: str = "",
    speaker_analysis_media: str = "",
) -> None:
    if not path.is_file():
        return
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for task in tasks:
            task["eligible_voice_galleries"] = gallery_names
            task["speech_dominant_audio"] = speech_dominant_audio
            task["speaker_analysis_media"] = speaker_analysis_media
            task["anonymous_speaker_map"] = anonymous_speaker_map
            task["voice_evidence_rule"] = (
                "Start from the episode-local anonymous speaker map, but never treat Speaker01/Speaker02 as character names. "
                "After the confirmed first episode, score reviewed continuous single-speaker turns with the confirmed three-model ensemble first, then independently check Japanese dialogue logic. "
                "For the first episode, prepare the complete mapping from dialogue/video evidence for user confirmation before enrollment. "
                "If the semantic unit splits into several turns, probe each safe turn separately; if several semantic units share "
                "one turn, probe that turn once. Probe only an episode-eligible, user-labeled gallery. Treat a passed top-1/top-2 "
                "result as strong supporting evidence, not an automatic overwrite. Never probe overlap, mixed, silence, too-short, "
                "or unreviewed turns. Inspect continuous source video only when the safe voice result is unavailable, weak, close, "
                "mixed, or conflicts with semantic evidence."
            )
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")


def resolve_input(value: str, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def portable_relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def source_locator(path: Path, feature_root: Path, sha256: str) -> dict[str, Any]:
    return {
        "relative_to_feature": portable_relative_path(path, feature_root),
        "basename": path.name,
        "sha256": sha256,
    }


def frame_record_path(record: dict[str, Any], manifest_dir: Path) -> Path:
    relative_value = str(record.get("frame_relative", "")).strip()
    if relative_value:
        candidate = (manifest_dir / relative_value).resolve()
        if candidate.is_file():
            return candidate
    recorded = Path(str(record.get("frame", "")))
    if recorded.is_file():
        return recorded
    return manifest_dir / recorded.name if recorded.name else recorded


def inspect_frame_manifest(
    path: Path,
    expected_srt_sha256: str,
    expected_video_sha256: str = "",
) -> tuple[list[int], list[str]]:
    """Validate every required frame in a preparation-stage manifest."""
    if not path.is_file():
        return [], ["frames_manifest_missing"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"frames_manifest_unreadable: {exc}"]
    if expected_srt_sha256 and str(payload.get("srt_sha256", "")).casefold() != expected_srt_sha256.casefold():
        return [], ["frames_manifest_srt_sha256_mismatch"]
    if expected_video_sha256 and str(payload.get("video_sha256", "")).casefold() != expected_video_sha256.casefold():
        return [], ["frames_manifest_video_sha256_mismatch"]

    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        return [], ["frames_manifest_entries_missing"]
    summary = payload.get("summary") or {}
    try:
        expected_count = int(summary.get("frames_per_subtitle", 3))
    except (TypeError, ValueError):
        expected_count = 3
    expected_count = max(1, expected_count)

    missing_indices: list[int] = []
    issues: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            issues.append("frames_manifest_entry_invalid")
            continue
        raw_index = item.get("source_index", "")
        try:
            source_index = int(raw_index)
        except (TypeError, ValueError):
            source_index = -1
        records = item.get("frames")
        present_records = []
        if isinstance(records, list):
            present_records = [
                record for record in records
                if isinstance(record, dict)
                and record.get("status") == "present"
                and frame_record_path(record, path.parent).is_file()
                and frame_record_path(record, path.parent).stat().st_size > 0
            ]
        if item.get("frame_status") != "present" or len(present_records) < expected_count:
            if source_index >= 0:
                missing_indices.append(source_index)

    declared_missing = (summary.get("missing") or []) if isinstance(summary, dict) else []
    declared_missing_set = {int(value) for value in declared_missing if str(value).lstrip("-").isdigit()}
    actual_missing_set = set(missing_indices)
    if declared_missing_set != actual_missing_set:
        issues.append("frames_manifest_missing_summary_mismatch")
    if missing_indices:
        issues.append("frames_missing_or_unreadable")
    return sorted(set(missing_indices)), issues


def load_episode_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = set(reader.fieldnames or [])
        required = {"episode", "video", "srt"}
        missing = sorted(required - fields)
        if missing:
            raise ValueError(f"Episode manifest is missing columns: {', '.join(missing)}")
        rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError("Episode manifest is empty")
    seen: set[str] = set()
    for row in rows:
        episode = (row.get("episode") or "").strip()
        if not re.fullmatch(r"[0-9]{4}", episode) or episode in seen:
            raise ValueError(f"Episode manifest has an empty or duplicate episode: {episode!r}")
        seen.add(episode)
        if not (row.get("video") or "").strip() or not (row.get("srt") or "").strip():
            raise ValueError(f"Episode {episode} needs both video and srt")
    return rows


def find_main_skill(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(__file__).resolve().parents[2] / "dubbing-script-automation")
    candidates.append(Path.home() / ".claude" / "skills" / "dubbing-script-automation")
    for candidate in candidates:
        tool = candidate / "scripts" / "dubbing_tool.py"
        if tool.is_file():
            return candidate.resolve()
    raise ValueError("Could not locate the installed dubbing-script-automation skill; pass --main-skill-dir")


def run_tool(tool: Path, arguments: list[str], python_executable: str | None = None) -> int:
    command = [python_executable or sys.executable, str(tool), *arguments]
    print("+", " ".join(str(item) for item in command))
    completed = subprocess.run(command, check=False)
    return completed.returncode


def run_tool_json(tool: Path, arguments: list[str], python_executable: str) -> tuple[int, dict[str, Any]]:
    command = [python_executable, str(tool), *arguments]
    print("+", " ".join(str(item) for item in command))
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"raw_output": completed.stdout.strip()}
    return completed.returncode, payload


def load_voice_required_roles(path: Path | None, relationship_graph: Path) -> list[str]:
    graph = json.loads(relationship_graph.read_text(encoding="utf-8"))
    approved = [str(item.get("name", "")).strip() for item in graph.get("profiles", []) if str(item.get("name", "")).strip()]
    if path is None:
        return approved
    if not path.is_file():
        raise ValueError(f"Voice required-role list not found: {path}")
    if path.suffix.casefold() in {".tsv", ".csv"}:
        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            field = next((name for name in ("role", "name", "役名", "名前") if name in (reader.fieldnames or [])), None)
            if not field:
                raise ValueError("Voice required-role table needs a role/name/役名/名前 column")
            requested = [(row.get(field) or "").strip() for row in reader]
    else:
        requested = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    unknown = sorted(set(requested) - set(approved))
    if unknown:
        raise ValueError(f"Voice required-role list contains roles outside the approved table: {', '.join(unknown)}")
    return list(dict.fromkeys(requested))


def normalize_voice_enrollment(source: Path, destination: Path) -> list[dict[str, str]]:
    if not source.is_file():
        raise ValueError(f"Voice enrollment manifest not found: {source}")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    required = {"role", "episode", "verified"}
    if not required.issubset(fields) or not ({"media_path", "audio_path"} & set(fields)):
        raise ValueError("Voice enrollment manifest needs role, episode, media_path/audio_path, and verified")
    # A user-supplied pre-cut audio file is consumed in full when start/end are
    # absent. Longer source media can still provide explicit interval columns.
    for field in ("start", "end"):
        if field not in fields:
            fields.append(field)
    for row in rows:
        for field in ("media_path", "audio_path"):
            value = (row.get(field) or "").strip()
            if value:
                row[field] = str(resolve_input(value, source.parent))
    write_tsv(destination, rows, fields)
    return rows


def create_beta_voice_manifest(
    source_root: Path,
    destination: Path,
    approved_roles: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """Create a Beta enrollment manifest from user-confirmed role folders."""
    if not source_root.is_dir():
        raise ValueError(f"Beta voice folder not found: {source_root}")
    approved_by_name = {role.casefold(): role for role in approved_roles}
    role_folders = [path for path in sorted(source_root.iterdir()) if path.is_dir() and not path.name.startswith("._")]
    unknown = sorted(path.name for path in role_folders if path.name.casefold() not in approved_by_name)
    if unknown:
        raise ValueError(
            "Beta voice folders are not approved roles: " + ", ".join(unknown)
        )
    rows: list[dict[str, str]] = []
    registered_roles: list[str] = []
    for folder in role_folders:
        role = approved_by_name[folder.name.casefold()]
        files = [
            path for path in sorted(folder.rglob("*"))
            if path.is_file() and not path.name.startswith("._")
            and path.suffix.casefold() in DIAGNOSTIC_AUDIO_SUFFIXES
        ]
        if not files:
            continue
        registered_roles.append(role)
        for number, path in enumerate(files, start=1):
            rows.append({
                "role": role,
                "episode": "user_confirmed",
                "audio_path": str(path.resolve()),
                "verified": "true",
                "clip_slot": str(number),
                "selected_by": "user",
                "selection_method": "user_role_folder",
                "reviewer_note": "beta supporting evidence; user folder name is the role authority",
            })
    write_tsv(
        destination,
        rows,
        [
            "role", "episode", "audio_path", "verified", "clip_slot", "selected_by",
            "selection_method", "reviewer_note",
        ],
    )
    return rows, list(dict.fromkeys(registered_roles))


def voice_defaults(args: argparse.Namespace) -> tuple[str, str, str]:
    mac_python = Path("/opt/homebrew/Caskroom/miniconda/base/envs/stable-ai/bin/python")
    mac_model = Path("/Users/zhaoyi/Documents/配音/.models/wespeaker-voxceleb-resnet34-LM")
    mac_ffmpeg = Path("/opt/homebrew/bin/ffmpeg")
    known_python = Path(r"E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe")
    known_model = Path(r"E:\AI_Models\pyannote\wespeaker-voxceleb-resnet34-LM")
    known_ffmpeg = Path(r"E:\AI_Models\RVC\RVC20240604Nvidia50x0\ffmpeg.exe")
    python_executable = args.voice_python or (
        str(mac_python) if mac_python.is_file()
        else str(known_python) if known_python.is_file()
        else sys.executable
    )
    model = (
        args.voice_model
        or os.environ.get("SPEAKER_EMBEDDING_MODEL", "")
        or (str(mac_model) if mac_model.exists() else "")
        or (str(known_model) if known_model.exists() else "")
    )
    ffmpeg = args.ffmpeg or os.environ.get("FFMPEG_BINARY", "") or (
        str(mac_ffmpeg) if mac_ffmpeg.is_file()
        else str(known_ffmpeg) if known_ffmpeg.is_file()
        else "ffmpeg"
    )
    return python_executable, model, ffmpeg


def build_voice_package(
    args: argparse.Namespace,
    package: Path,
    main_skill: Path,
    relationship_graph: Path,
    episode_rows: list[dict[str, str]],
) -> tuple[dict[str, Any], list[str]]:
    voice_root = package / VOICE_DIR
    voice_root.mkdir(parents=True, exist_ok=True)
    required_path = Path(args.voice_required_roles).resolve() if args.voice_required_roles else None
    required_roles = list(getattr(args, "voice_required_role_values", []) or [])
    if not required_roles:
        required_roles = load_voice_required_roles(required_path, relationship_graph)
    template_fields = [
        "role", "clip_slot", "episode", "media_path", "start", "end", "verified",
        "overlap", "mixed_speaker", "music_dominant", "selected_by", "selection_method",
        "source_audio_path", "reviewer_note",
    ]
    template_rows = [
        {
            "role": role, "clip_slot": slot, "verified": "false", "overlap": "false",
            "mixed_speaker": "false", "music_dominant": "false", "selected_by": "user",
            "selection_method": "manual",
        }
        for role in required_roles for slot in range(1, 4)
    ]
    template_path = voice_root / "voice_enrollment_template.tsv"
    write_tsv(template_path, template_rows, template_fields)
    result: dict[str, Any] = {
        "requested": bool(args.voice_enrollment_manifest),
        "required": bool(args.require_voice_gallery),
        "selection_authority": "user",
        "agent_enrollment_search_allowed": False,
        "required_roles": required_roles,
        "enrollment_template": relative_path(template_path, package),
        "galleries": [],
        "episode_gallery_map": {},
        "uncovered_episodes": [],
        "gallery_ready": False,
    }
    issues: list[str] = []
    manifests = [Path(value).resolve() for value in (args.voice_enrollment_manifest or [])]
    if not manifests:
        if args.require_voice_gallery:
            issues.append(
                "voice gallery is required but no user-supplied --manual-voice-enrollment-manifest "
                "(or --voice-enrollment-manifest) was supplied"
            )
        return result, issues

    voice_python, model, ffmpeg = voice_defaults(args)
    if not Path(voice_python).is_file():
        issues.append(f"voice Python not found: {voice_python}")
        return result, issues
    if not model or not Path(model).exists():
        issues.append(f"speaker embedding model not found: {model or '<unset>'}")
        return result, issues
    voice_tool = main_skill / "scripts" / "voice_evidence.py"
    if not voice_tool.is_file():
        issues.append(f"voice evidence tool not found: {voice_tool}")
        return result, issues

    used_ids: set[str] = set()
    for number, source in enumerate(manifests, start=1):
        gallery_id = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-.") or f"gallery-{number}"
        if gallery_id in used_ids:
            gallery_id = f"{gallery_id}-{number}"
        used_ids.add(gallery_id)
        gallery_dir = voice_root / "galleries" / gallery_id
        normalized_manifest = gallery_dir / "enrollment_manifest.tsv"
        normalize_voice_enrollment(source, normalized_manifest)
        gallery_path = gallery_dir / "voice_gallery.npz"
        audit_path = gallery_dir / "voice_gallery_audit.tsv"
        report_path = gallery_dir / "voice_gallery.report.json"
        preflight_path = gallery_dir / "voice_gallery_preflight.json"
        build_rc = run_tool(voice_tool, [
            "build-gallery", "--manifest", str(normalized_manifest), "--model", model,
            "--out-gallery", str(gallery_path), "--audit-tsv", str(audit_path),
            "--report", str(report_path), "--device", args.voice_device,
            "--ffmpeg", ffmpeg,
            "--min-clips-per-role", "3",
            "--min-episodes-per-role", "1",
            "--min-total-duration-per-role", "8",
            "--max-enrollment-duration", "12",
            "--allow-degraded",
            "--beta",
            "--overwrite",
        ], python_executable=voice_python)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        covered_roles = [str(role) for role in report.get("roles", [])]
        missing_roles = sorted(set(required_roles) - set(covered_roles))
        extra_roles = sorted(set(covered_roles) - set(required_roles))
        preflight_rc = 2
        preflight: dict[str, Any] = {"runtime_ready": False, "ready": False}
        if gallery_path.is_file():
            preflight_rc, preflight = run_tool_json(voice_tool, [
                "preflight", "--model", model, "--gallery", str(gallery_path),
                "--device", args.voice_device, "--ffmpeg", ffmpeg,
            ], voice_python)
        write_json(preflight_path, preflight)
        beta_workflow = report.get("workflow_stage") == "beta" or bool(report.get("diagnostic_only"))
        gallery_ready = bool(
            build_rc == 0 and preflight_rc == 0
            and (report.get("gallery_ready") is True or report.get("production_ready") is True)
            and (preflight.get("runtime_ready") is True or preflight.get("production_runtime_ready") is True)
            and not missing_roles and not extra_roles
        )
        item = {
            "gallery_name": gallery_id,
            "gallery_ready": gallery_ready,
            "workflow_stage": "beta" if beta_workflow else str(report.get("workflow_stage", "standalone")),
            "voice_evidence_authority": "supporting",
            "automatic_identity_assignment": False,
            "diagnostic_only": bool(report.get("diagnostic_only")),
            "gallery": relative_path(gallery_path, package) if gallery_path.is_file() else "",
            "enrollment_manifest": relative_path(normalized_manifest, package),
            "audit_tsv": relative_path(audit_path, package) if audit_path.is_file() else "",
            "report": relative_path(report_path, package) if report_path.is_file() else "",
            "preflight": relative_path(preflight_path, package),
            "covered_roles": covered_roles,
            "missing_roles": missing_roles,
            "extra_roles": extra_roles,
            "enrollment_episodes": [str(value) for value in report.get("enrollment_episodes", [])],
        }
        for field, path in (
            ("manifest_sha256", normalized_manifest), ("gallery_sha256", gallery_path),
            ("audit_sha256", audit_path), ("report_sha256", report_path),
            ("preflight_sha256", preflight_path),
        ):
            item[field] = sha256_file(path) if path.is_file() else ""
        result["galleries"].append(item)
        if not gallery_ready:
            issues.append(
                f"voice gallery {gallery_id} is not ready"
                + (f"; missing roles: {', '.join(missing_roles)}" if missing_roles else "")
                + (f"; unapproved roles: {', '.join(extra_roles)}" if extra_roles else "")
            )

    target_episodes = [row["episode"].strip() for row in episode_rows]
    for episode in target_episodes:
        eligible = [
            item["gallery_name"] for item in result["galleries"]
            if item["gallery_ready"] and episode not in set(item["enrollment_episodes"])
        ]
        result["episode_gallery_map"][episode] = eligible
    result["uncovered_episodes"] = [episode for episode, galleries in result["episode_gallery_map"].items() if not galleries]
    result["gallery_ready"] = bool(result["galleries"]) and not result["uncovered_episodes"]
    if args.require_voice_gallery and result["uncovered_episodes"]:
        issues.append(
            "no leakage-safe voice gallery for episodes: " + ", ".join(result["uncovered_episodes"])
            + "; supply a second disjoint enrollment manifest for cross-fit coverage"
        )
    return result, issues


def refresh_internal_reference(args: argparse.Namespace) -> int:
    """Rebuild named reference context without changing reviewed labels, SRTs or anonymous drafts."""
    package = Path(args.refresh_internal_reference).resolve()
    handoff_path = package / "handoff_manifest.json"
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if handoff.get("package_type") != "dubbing-script-preparation" or int(handoff.get("schema_version", 0)) < 9:
        raise ValueError("Internal-reference refresh requires a schema 9 or later preparation package")
    def resolve(value: str) -> Path:
        if not value:
            raise ValueError("Missing required internal-reference refresh path")
        path = Path(value)
        return path if path.is_absolute() else package / path
    reference = resolve(str(handoff.get("internal_character_reference", "")))
    graph = resolve(str(handoff.get("relationship_graph", "")))
    if not reference.is_file():
        raise ValueError(f"Internal character-reference draft missing: {reference}")
    episodes = handoff.get("episodes") or []
    if not episodes:
        raise ValueError("Preparation package has no episodes")
    tool = find_main_skill(args.main_skill_dir) / "scripts" / "dubbing_tool.py"
    updates: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory(prefix="refresh-internal-reference-") as temporary:
        temporary_root = Path(temporary)
        next_graph = temporary_root / "relationship_graph.json"
        if run_tool(tool, ["analyze-roles", "--roles", str(reference), "--out-json", str(next_graph), "--authority", "internal_character_reference"]) != 0:
            raise ValueError("Updated internal character-reference analysis failed; package context was not changed")
        updates.append((next_graph, graph))
        for episode in episodes:
            label = str(episode.get("episode", ""))
            if not re.fullmatch(r"[0-9]{4}", label):
                raise ValueError(f"Invalid episode ID: {label!r}")
            srt = resolve(str(episode.get("source_srt", "")))
            queue = resolve(str(episode.get("review_queue", "")))
            next_queue = temporary_root / f"{label}_review_queue.jsonl"
            discarded_labels = temporary_root / f"{label}_labels.tsv"
            command = ["prepare-codex-review", "--srt", str(srt), "--roles", str(reference), "--episode", label, "--out-jsonl", str(next_queue), "--out-tsv", str(discarded_labels)]
            for option, value in (("--manifest", episode.get("frames_manifest")), ("--face-reference-manifest", handoff.get("face_reference_manifest"))):
                if value:
                    command.extend([option, str(resolve(str(value)))])
            if run_tool(tool, command) != 0:
                raise ValueError(f"Episode {label} context refresh failed; package context was not changed")
            enrich_review_queue(next_queue, episode.get("eligible_voice_galleries", []), str(episode.get("speech_dominant_audio", "")), str(resolve(str(episode["anonymous_speaker_map"]))) if episode.get("anonymous_speaker_map") else "", str(episode.get("speaker_analysis_media", "")))
            updates.append((next_queue, queue))
        # Only context artifacts are replaced. Existing reviewed labels and immutable speech drafts survive.
        for source, destination in updates:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_destination = destination.with_name(destination.name + ".refresh.tmp")
            shutil.copy2(source, temporary_destination)
            os.replace(temporary_destination, destination)
    handoff["internal_character_reference_sha256"] = sha256_file(reference)
    handoff["internal_character_reference_status"] = "draft_pending_user_approval"
    handoff["generation_authorized"] = False
    # The changed handoff and reference hashes invalidate any existing AI review and approval.
    write_json(handoff_path, handoff)
    report_value = str(handoff.get("preparation_report", ""))
    if report_value:
        report_path = resolve(report_value)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        report.update({"generation_authorized": False, "approval_gate_status": "fresh_ai_review_required_after_internal_reference_refresh"})
        write_json(report_path, report)
    print(json.dumps({"package": str(package), "internal_character_reference_sha256": handoff["internal_character_reference_sha256"], "generation_authorized": False, "next_step": "Create a fresh preparation review and obtain explicit user approval of the revised baseline."}, ensure_ascii=False, indent=2))
    return 0


def refresh_package(args: argparse.Namespace) -> int:
    """Rebuild package galleries after the user edits enrollment manifests in-place."""
    package = Path(args.refresh_package).resolve()
    handoff_path = package / "handoff_manifest.json"
    if not handoff_path.is_file():
        raise ValueError(f"Preparation handoff not found: {handoff_path}")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if handoff.get("package_type") != "dubbing-script-preparation":
        raise ValueError("Not a dubbing-script-preparation package")
    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else package / path
    relationship_graph = resolve(str(handoff.get("relationship_graph", "")))
    if not relationship_graph.is_file():
        raise ValueError(f"Relationship graph not found: {relationship_graph}")
    voice_package = handoff.get("voice_gallery") or {}
    existing_manifests = []
    for item in voice_package.get("galleries", []) if isinstance(voice_package, dict) else []:
        value = str(item.get("enrollment_manifest", ""))
        if value:
            existing_manifests.append(str(resolve(value)))
    if args.voice_enrollment_manifest:
        manifests = list(args.voice_enrollment_manifest)
    else:
        manifests = existing_manifests
    if not manifests:
        raise ValueError("No enrollment manifest supplied or found in the preparation package")
    episodes = [{"episode": str(item.get("episode", ""))} for item in handoff.get("episodes", [])]
    if not episodes or any(not item["episode"] for item in episodes):
        raise ValueError("Preparation handoff has no valid episode list")
    main_skill = find_main_skill(args.main_skill_dir)
    args.voice_enrollment_manifest = manifests
    if args.voice_required_roles and Path(args.voice_required_roles).is_file():
        args.voice_required_role_values = load_voice_required_roles(Path(args.voice_required_roles).resolve(), relationship_graph)
    elif not getattr(args, "voice_required_role_values", None):
        args.voice_required_role_values = list(voice_package.get("required_roles", []) or [])
    args.require_voice_gallery = bool(args.require_voice_gallery or voice_package.get("required"))
    refreshed, voice_issues = build_voice_package(
        args, package, main_skill, relationship_graph, episodes,
    )
    previous_blocking = [
        str(issue) for issue in handoff.get("blocking_issues", [])
        if not str(issue).startswith("voice gallery") and "voice gallery preparation failed" not in str(issue)
    ]
    blocking = [*previous_blocking, *voice_issues]
    normal_ready = all(str(item.get("status", "")) == "ready" for item in handoff.get("episodes", []))
    target_schema = 6 if int(handoff.get("layout_version", 0) or 0) >= 1 else 5
    handoff["schema_version"] = max(int(handoff.get("schema_version", 2)), target_schema)
    handoff["voice_gallery"] = refreshed
    handoff["blocking_issues"] = blocking
    handoff["preparation_ready"] = bool(normal_ready and not blocking)
    handoff["technical_preparation_ready"] = handoff["preparation_ready"]
    handoff["generation_authorized"] = False
    if not isinstance(handoff.get("approval_gate"), dict):
        handoff["approval_gate"] = {
            "required": True,
            "ai_review": "preparation_ai_review.json",
            "ai_review_markdown": "preparation_ai_review.md",
            "user_approval": "preparation_user_approval.json",
            "rule": (
                "A fresh AI preparation review and explicit user approval bound to the current "
                "package fingerprint are required before formal script generation."
            ),
        }
    write_json(handoff_path, handoff)
    report_value = str(handoff.get("preparation_report", "preparation_report.json"))
    report_path = resolve(report_value)
    write_json(report_path, {
        "preparation_ready": handoff["preparation_ready"],
        "episodes": len(handoff.get("episodes", [])),
        "ready_episodes": sum(str(item.get("status", "")) == "ready" for item in handoff.get("episodes", [])),
        "blocking_issues": blocking,
        "face_reference_images": int((handoff.get("face_reference_summary") or {}).get("reference_images", 0)),
        "face_reference_roles": (handoff.get("face_reference_summary") or {}).get("roles_with_images", []),
        "face_reference_missing_roles": (handoff.get("face_reference_summary") or {}).get("missing_roles", []),
        "voice_gallery_ready": refreshed.get("gallery_ready", False),
        "voice_gallery_uncovered_episodes": refreshed.get("uncovered_episodes", []),
        "generation_authorized": False,
        "approval_gate_status": "fresh_ai_review_required_after_refresh",
        "package_layout": handoff.get("package_layout", ""),
        "handoff_manifest": str(handoff_path),
    })
    print(json.dumps({
        "preparation_ready": handoff["preparation_ready"],
        "voice_gallery_ready": refreshed.get("gallery_ready", False),
        "blocking_issues": blocking,
        "handoff_manifest": str(handoff_path),
    }, ensure_ascii=False, indent=2))
    return 0 if handoff["preparation_ready"] else 2


def build_package(args: argparse.Namespace) -> int:
    manifest_path = Path(args.episode_manifest).resolve()
    package = Path(args.out_dir).resolve()
    script_root = Path(args.script_root).resolve() if args.script_root else package.parent.resolve()
    feature_root = Path(args.feature_root).resolve() if args.feature_root else script_root.parent.resolve()
    if package.parent.resolve() != script_root:
        raise ValueError(f"Preparation package must be a direct child of the script directory: {script_root}")
    if script_root.parent.resolve() != feature_root:
        raise ValueError(f"The script directory must be a direct child of the feature directory: {feature_root}")
    if package.exists() and any(package.iterdir()) and not args.overwrite:
        raise ValueError(
            f"Output directory contains generated or unknown files: {package}. "
            "Use an empty package or --overwrite."
        )
    rows = load_episode_manifest(manifest_path)
    if args.overwrite and (package / "handoff_manifest.json").is_file():
        previous = json.loads((package / "handoff_manifest.json").read_text(encoding="utf-8"))
        previous_episodes = {str(item.get("episode", "")) for item in previous.get("episodes", [])}
        current_episodes = {row["episode"].strip() for row in rows}
        if previous_episodes != current_episodes:
            raise ValueError("Cannot overwrite a package with a different episode scope; use a new empty output directory")
    if args.beta_voice_dir or args.voice_enrollment_manifest or args.require_voice_gallery:
        raise ValueError("New Beta preparation packages create anonymous drafts and enrollment templates only; named galleries belong to the downstream confirmed first-episode workflow")
    if not args.anonymous_diarization_pipeline:
        raise ValueError("--anonymous-diarization-pipeline is required before creating a package")
    main_skill = find_main_skill(args.main_skill_dir)
    preflight_args = ["preflight", "--pipeline", args.anonymous_diarization_pipeline, "--main-skill-dir", str(main_skill), "--ffmpeg", args.ffmpeg or default_ffmpeg()]
    for model in args.identity_model:
        preflight_args.extend(["--voice-model", model])
    if run_tool(Path(__file__).with_name("anonymous_speaker_draft.py"), preflight_args) != 0:
        raise ValueError("Anonymous diarization / three-model identity preflight failed; package was not modified")
    package.mkdir(parents=True, exist_ok=True)
    for folder in (
        FRAMES_DIR, SCRIPTS_DIR, CHARACTERS_DIR, VOICE_DIR, REVIEW_DIR, HANDOFF_DIR,
    ):
        (package / folder).mkdir(parents=True, exist_ok=True)
    speech_column_declared = "speech_dominant_audio" in rows[0]
    legacy_clean_column_declared = "clean_voice_audio" in rows[0]
    speech_audio_declared = speech_column_declared or legacy_clean_column_declared
    main_skill = find_main_skill(args.main_skill_dir)
    tool = main_skill / "scripts" / "dubbing_tool.py"
    episode_manifest_copy = package / HANDOFF_DIR / "episode_manifest.tsv"
    shutil.copy2(manifest_path, episode_manifest_copy)

    roles_source = Path(args.roles).resolve()
    if not roles_source.is_file():
        raise ValueError(f"Role table not found: {roles_source}")
    role_suffix = roles_source.suffix.casefold() or ".tsv"
    roles_dest = (
        package / CHARACTERS_DIR / "01_internal_character_draft"
        / f"internal_character_reference{role_suffix}"
    )
    roles_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(roles_source, roles_dest)
    source_role_workbook_rel = ""
    source_role_workbook_path: Path | None = None
    if args.source_role_workbook:
        source_role_workbook = Path(args.source_role_workbook).resolve()
        if not source_role_workbook.is_file():
            raise ValueError(f"Source role workbook not found: {source_role_workbook}")
        source_role_workbook_path = source_role_workbook
        source_role_dest = package / CHARACTERS_DIR / "00_source_role_workbook" / source_role_workbook.name
        source_role_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_role_workbook, source_role_dest)
        source_role_workbook_rel = relative_path(source_role_dest, package)
    relationship_graph = (
        package / CHARACTERS_DIR / "03_relationship_evidence" / "relationship_graph.json"
    )
    relationship_rc = run_tool(tool, [
        "analyze-roles", "--roles", str(roles_dest),
        "--out-json", str(relationship_graph),
        "--authority", "internal_character_reference",
    ])
    name_map_rel = ""
    if args.name_map_xlsx:
        name_map_source = Path(args.name_map_xlsx).resolve()
        if not name_map_source.is_file():
            raise ValueError(f"Japanese name workbook not found: {name_map_source}")
        name_map_dest = package / CHARACTERS_DIR / "04_review" / "name_mapping" / name_map_source.name
        name_map_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(name_map_source, name_map_dest)
        name_map_rel = relative_path(name_map_dest, package)

    screen_text_rel = ""
    if args.screen_text_xlsx:
        screen_text_source = Path(args.screen_text_xlsx).resolve()
        if not screen_text_source.is_file():
            raise ValueError(f"Screen-text workbook not found: {screen_text_source}")
        screen_text_dest = (
            package / CHARACTERS_DIR / "03_relationship_evidence"
            / "screen_text" / screen_text_source.name
        )
        screen_text_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(screen_text_source, screen_text_dest)
        screen_text_rel = relative_path(screen_text_dest, package)

    episodes: list[dict[str, Any]] = []
    blocking: list[str] = []
    if relationship_rc != 0 or not relationship_graph.is_file():
        blocking.append("internal character-reference relationship analysis failed")
    face_reference_manifest = package / CHARACTERS_DIR / "02_face_gallery" / "face_reference_manifest.tsv"
    face_reference_report = package / CHARACTERS_DIR / "02_face_gallery" / "face_reference_report.json"
    face_reference_data: dict[str, Any] = {
        "reference_images": 0,
        "roles_with_images": [],
        "missing_roles": [],
        "matching_mode": "one_shot_visual_reference",
    }
    face_source = (
        source_role_workbook_path
        if source_role_workbook_path and source_role_workbook_path.suffix.casefold() == ".xlsx"
        else roles_dest
    )
    if face_source.suffix.casefold() == ".xlsx":
        face_arguments = [
            "extract-role-images", "--roles", str(face_source),
            "--out-dir", str(face_reference_manifest.parent / "role_images"),
            "--out-manifest", str(face_reference_manifest),
            "--report", str(face_reference_report),
            "--overwrite",
        ]
        if args.require_face_references:
            face_arguments.append("--require-all")
        face_rc = run_tool(tool, face_arguments)
        if face_reference_report.is_file():
            face_reference_data = json.loads(face_reference_report.read_text(encoding="utf-8"))
        if face_rc != 0 and args.require_face_references:
            blocking.append(
                "required role-table face references are incomplete: "
                + ", ".join(map(str, face_reference_data.get("missing_roles", [])[:12]))
            )
    if args.beta_voice_dir:
        approved_voice_roles = load_voice_required_roles(None, relationship_graph)
        beta_manifest = package / VOICE_DIR / "beta_voice_manifest.tsv"
        _, beta_roles = create_beta_voice_manifest(
            Path(args.beta_voice_dir).resolve(), beta_manifest, approved_voice_roles,
        )
        if beta_roles:
            args.voice_enrollment_manifest = [str(beta_manifest)]
            args.voice_required_role_values = beta_roles
        else:
            args.voice_enrollment_manifest = []
            args.voice_required_role_values = []

    voice_package: dict[str, Any] = {
        "requested": bool(args.voice_enrollment_manifest),
        "required": bool(args.require_voice_gallery),
        "gallery_ready": False,
        "galleries": [],
        "episode_gallery_map": {},
    }
    if relationship_graph.is_file():
        try:
            voice_package, voice_issues = build_voice_package(
                args, package, main_skill, relationship_graph, rows,
            )
            if args.require_voice_gallery:
                blocking.extend(voice_issues)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            blocking.append(f"voice gallery preparation failed: {exc}")
    anonymous_tool = Path(__file__).with_name("anonymous_speaker_draft.py")
    anonymous_root = package / VOICE_DIR / "01_anonymous_diarization"
    anonymous_root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        episode = row["episode"].strip()
        video = resolve_input(row["video"].strip(), manifest_path.parent)
        source_srt = resolve_input(row["srt"].strip(), manifest_path.parent)
        speech_value = (row.get("speech_dominant_audio") or "").strip()
        legacy_clean_value = (row.get("clean_voice_audio") or "").strip()
        if speech_value and legacy_clean_value and speech_value != legacy_clean_value:
            blocking.append(
                f"episode {episode}: speech_dominant_audio conflicts with legacy clean_voice_audio"
            )
        speech_value = speech_value or legacy_clean_value
        source_speech_audio = (
            resolve_input(speech_value, manifest_path.parent)
            if speech_value else None
        )
        script_episode_dir = package / SCRIPTS_DIR / episode
        qc_episode_dir = package / REVIEW_DIR / "01_episode_qc" / episode
        for directory in (
            script_episode_dir, qc_episode_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        packaged_srt = script_episode_dir / "source.srt"
        speech_audio_reference: Path | None = None
        if not video.is_file():
            blocking.append(f"episode {episode}: video not found: {video}")
        source_video_sha256 = sha256_file(video) if video.is_file() else ""
        if speech_audio_declared and source_speech_audio is None:
            blocking.append(f"episode {episode}: speech_dominant_audio is blank")
        elif source_speech_audio is not None:
            if not source_speech_audio.is_file():
                blocking.append(
                    f"episode {episode}: speech_dominant_audio not found: {source_speech_audio}"
                )
            else:
                speech_audio_reference = source_speech_audio.resolve()
        if not source_srt.is_file():
            blocking.append(f"episode {episode}: srt not found: {source_srt}")
            continue
        shutil.copy2(source_srt, packaged_srt)
        srt_report = qc_episode_dir / "srt_report.json"
        review_queue = script_episode_dir / "codex_review_queue.jsonl"
        labels_tsv = script_episode_dir / "codex_labels.tsv"
        anonymous_episode_dir = anonymous_root / episode
        anonymous_map = anonymous_episode_dir / "subtitle_speaker_map.tsv"
        anonymous_script = anonymous_episode_dir / "anonymous_script.tsv"
        anonymous_workbook = anonymous_episode_dir / "anonymous_speaker_script.xlsx"
        anonymous_report = anonymous_episode_dir / "diarization_report.json"

        anonymous_rc = 2
        diarization_media = speech_audio_reference or (video if video.is_file() else None)
        if not args.anonymous_diarization_pipeline:
            blocking.append(
                f"episode {episode}: anonymous diarization pipeline is not configured"
            )
        elif diarization_media is None:
            blocking.append(f"episode {episode}: no media is available for anonymous diarization")
        else:
            diarize_arguments = [
                "diarize", "--episode", episode,
                "--srt", str(packaged_srt),
                "--media", str(diarization_media),
                "--pipeline", str(Path(args.anonymous_diarization_pipeline).resolve()),
                "--device", args.anonymous_device,
                "--ffmpeg", args.ffmpeg or default_ffmpeg(),
                "--out-dir", str(anonymous_episode_dir),
            ]
            if args.workflow_track == "experimental":
                diarize_arguments.extend(["--segmentation", "subtitle"])
            anonymous_rc = run_tool(anonymous_tool, diarize_arguments)
            if anonymous_rc != 0:
                blocking.append(f"episode {episode}: anonymous speaker diarization failed")

        frames_episode_dir = package / FRAMES_DIR / episode
        frames_manifest = frames_episode_dir / "frames_manifest.json"
        extract_rc = 2
        if getattr(args, "extract_frames", False):
            frames_episode_dir.mkdir(parents=True, exist_ok=True)
            extract_rc = run_tool(tool, [
                "extract-frames", "--video", str(video),
                "--srt", str(packaged_srt),
                "--out-dir", str(frames_episode_dir),
                "--source-root", str(feature_root),
                "--frames-per-subtitle", str(getattr(args, "frames_per_subtitle", 3)),
                "--overwrite"
            ])
            if extract_rc != 0:
                blocking.append(f"episode {episode}: extract-frames failed")
        else:
            blocking.append(f"episode {episode}: frame extraction was skipped")

        inspect_rc = run_tool(tool, [
            "inspect-srt", "--srt", str(packaged_srt), "--report", str(srt_report)
        ])
        if inspect_rc != 0:
            blocking.append(f"episode {episode}: inspect-srt failed")
            continue
        review_arguments = [
            "prepare-codex-review", "--srt", str(packaged_srt),
            "--roles", str(roles_dest),
            "--episode", episode, "--out-jsonl", str(review_queue),
            "--out-tsv", str(labels_tsv), "--overwrite",
        ]
        if frames_manifest.is_file():
            review_arguments.extend(["--manifest", str(frames_manifest)])
        if face_reference_manifest.is_file():
            review_arguments.extend(["--face-reference-manifest", str(face_reference_manifest)])
        review_rc = run_tool(tool, review_arguments)
        if review_rc != 0:
            blocking.append(f"episode {episode}: review queue generation failed")
        enrich_review_queue(
            review_queue,
            voice_package.get("episode_gallery_map", {}).get(episode, []),
            (
                str(speech_audio_reference)
                if speech_audio_reference and speech_audio_reference.is_file() else ""
            ),
            str(anonymous_map) if anonymous_map.is_file() else "",
            str(diarization_media.resolve()) if diarization_media else "",
        )

        srt_data = json.loads(srt_report.read_text(encoding="utf-8")) if srt_report.is_file() else {}
        missing_frames, frame_issues = inspect_frame_manifest(
            frames_manifest, sha256_file(packaged_srt), source_video_sha256,
        )
        for frame_issue in frame_issues:
            blocking.append(f"episode {episode}: {frame_issue}")
        speech_audio_ready = (
            not speech_audio_declared
            or bool(speech_audio_reference and speech_audio_reference.is_file())
        )
        episode_qc = {
            "schema_version": 3,
            "episode": episode,
            "status": (
                "ready"
                if extract_rc == 0 and not frame_issues
                and inspect_rc == 0 and review_rc == 0 and speech_audio_ready
                and anonymous_rc == 0 and anonymous_map.is_file()
                else "blocked"
            ),
            "entries": srt_data.get("entries", 0),
            "semantic_units": len({
                str(item.get("semantic_unit_hint"))
                for line in review_queue.read_text(encoding="utf-8").splitlines()
                if line.strip()
                for item in [json.loads(line)]
                if item.get("semantic_unit_hint")
            }) if review_queue.is_file() else 0,
            "video_review_mode": "on_demand_only",
            "missing_frame_indices": missing_frames,
            "source_srt_sha256": sha256_file(packaged_srt),
            "relationship_context_tasks": sum(
                bool((item.get("relationship_context") or {}).get("cues") or (item.get("relationship_context") or {}).get("relevant_edges"))
                for line in review_queue.read_text(encoding="utf-8").splitlines()
                if line.strip()
                for item in [json.loads(line)]
            ) if review_queue.is_file() else 0,
            "handoff_rule": "Candidates are hints only; final roles require multimodal review.",
            "face_reference_images": int(face_reference_data.get("reference_images", 0)),
            "face_matching_mode": "one_shot_visual_reference",
            "eligible_voice_galleries": voice_package.get("episode_gallery_map", {}).get(episode, []),
            "speech_dominant_audio_status": (
                "ready" if speech_audio_reference and speech_audio_reference.is_file()
                else ("missing" if speech_audio_declared else "original_video_fallback")
            ),
            "anonymous_speaker_draft_status": "ready" if anonymous_rc == 0 else "blocked",
            "anonymous_segmentation_mode": "subtitle_guided" if args.workflow_track == "experimental" else "sliding_window",
        }
        episode_qc_path = qc_episode_dir / "preparation_qc.json"
        write_json(episode_qc_path, episode_qc)
        episodes.append({
            "episode": episode,
            "source_video": str(video.resolve()),
            "video_sha256": source_video_sha256,
            "source_video_locator": source_locator(video, feature_root, source_video_sha256),
            "source_srt": relative_path(packaged_srt, package),
            "original_source_srt": str(source_srt.resolve()),
            "original_source_srt_locator": source_locator(
                source_srt, feature_root, sha256_file(source_srt),
            ),
            "srt_report": relative_path(srt_report, package),
            "frames_manifest": (
                relative_path(frames_manifest, package)
                if frames_manifest.is_file() else ""
            ),
            "review_queue": relative_path(review_queue, package),
            "labels_tsv": relative_path(labels_tsv, package),
            "anonymous_speaker_map": (
                relative_path(anonymous_map, package) if anonymous_map.is_file() else ""
            ),
            "anonymous_script_tsv": (
                relative_path(anonymous_script, package) if anonymous_script.is_file() else ""
            ),
            "anonymous_script_xlsx": (
                relative_path(anonymous_workbook, package) if anonymous_workbook.is_file() else ""
            ),
            "anonymous_diarization_report": (
                relative_path(anonymous_report, package) if anonymous_report.is_file() else ""
            ),
            "speaker_analysis_media": (
                str(diarization_media.resolve()) if diarization_media else ""
            ),
            "preparation_qc": relative_path(episode_qc_path, package),
            "srt_sha256": sha256_file(packaged_srt),
            "status": episode_qc["status"],
            "missing_frame_indices": missing_frames,
            "eligible_voice_galleries": voice_package.get("episode_gallery_map", {}).get(episode, []),
            "speech_dominant_audio": (
                str(speech_audio_reference)
                if speech_audio_reference and speech_audio_reference.is_file() else ""
            ),
            "speech_dominant_audio_sha256": (
                sha256_file(speech_audio_reference)
                if speech_audio_reference and speech_audio_reference.is_file() else ""
            ),
            "speech_dominant_audio_locator": (
                source_locator(
                    speech_audio_reference,
                    feature_root,
                    sha256_file(speech_audio_reference),
                )
                if speech_audio_reference and speech_audio_reference.is_file() else {}
            ),
        })

    whole_anonymous_tsv = anonymous_root / "whole_series_anonymous_script.tsv"
    whole_anonymous_xlsx = anonymous_root / "whole_series_anonymous_speaker_script.xlsx"
    if episodes and all(item.get("anonymous_script_tsv") for item in episodes):
        merge_rc = run_tool(anonymous_tool, [
            "merge", "--root", str(anonymous_root),
            "--episodes", ",".join(item["episode"] for item in episodes),
            "--out-tsv", str(whole_anonymous_tsv),
            "--out-xlsx", str(whole_anonymous_xlsx),
        ])
        if merge_rc != 0:
            blocking.append("whole-series anonymous speaker draft merge failed")

    ai_review_dir = package / REVIEW_DIR / "02_ai_review"
    user_approval_dir = package / REVIEW_DIR / "03_user_approval"
    ai_review_dir.mkdir(parents=True, exist_ok=True)
    user_approval_dir.mkdir(parents=True, exist_ok=True)
    preparation_report_path = package / REVIEW_DIR / "preparation_report.json"
    layout_path = package / HANDOFF_DIR / "package_layout.json"
    package_layout = {
        "schema_version": 1,
        "layout_name": "numbered_preparation_package",
        "folders": [
            {"number": "01", "path": FRAMES_DIR, "purpose": "Extracted in-cue video frames (3 frames per subtitle) and frames manifests for multimodal review."},
            {"number": "02", "path": SCRIPTS_DIR, "purpose": "Immutable source SRT copies, review queues, and editable preparation label TSVs."},
            {"number": "03", "path": CHARACTERS_DIR, "purpose": "Unverified client role source, internal character-reference draft, face gallery, relationship evidence, review material, and approved baseline snapshot."},
            {"number": "04", "path": VOICE_DIR, "purpose": "Mandatory episode-local Speaker01/Speaker02 drafts, voice enrollment templates, and confirmed voice galleries."},
            {"number": "05", "path": REVIEW_DIR, "purpose": "Episode preparation QC, AI sufficiency report, and explicit user approval."},
            {"number": "06", "path": HANDOFF_DIR, "purpose": "Copied episode manifest and package layout metadata."},
        ],
        "root_files": [
            {"path": "handoff_manifest.json", "purpose": "Single machine continuation entry point."},
        ],
        "source_material_policy": (
            "Discover and reference immutable source material in place under the current source-production root. "
            "Directory-role names are semantic hints, not a whitelist; inspect casting and new project-specific folders by content."
        ),
    }
    write_json(layout_path, package_layout)

    ready = bool(episodes) and not blocking and all(item["status"] == "ready" for item in episodes)
    handoff = {
        "schema_version": 11,
        "package_type": "dubbing-script-preparation",
        "workflow_stage": "beta",
        "workflow_version": "beta1.6",
        "workflow_track": args.workflow_track,
        "layout_version": 3,
        "package_layout": relative_path(layout_path, package),
        "preparation_ready": ready,
        "technical_preparation_ready": ready,
        "generation_authorized": False,
        "created_by": "dubbing-script-preparation",
        "path_anchor": {
            "kind": "preparation_inside_script",
            "script_root_from_package": "..",
            "feature_root_from_package": "../..",
            "source_search_scope": "feature_only",
            "known_directory_role_hints": [
                "feature", "finish", "video", "subtitle", "script",
                "casting", "sound", "vocal", "fix",
            ],
            "directory_discovery_policy": (
                "Role names are hints, not a whitelist. Inspect new directories by semantic name, "
                "hierarchy, file types, and content before deciding whether they are relevant."
            ),
        },
        "episode_manifest": relative_path(episode_manifest_copy, package),
        "source_episode_manifest": str(episode_manifest_copy.resolve()),
        "internal_character_reference": relative_path(roles_dest, package),
        "internal_character_reference_sha256": sha256_file(roles_dest),
        "internal_character_reference_status": "draft_pending_user_approval",
        "source_role_workbook": source_role_workbook_rel,
        "original_source_role_workbook": str(source_role_workbook_path or ""),
        "original_screen_text_workbook": str(Path(args.screen_text_xlsx).resolve()) if args.screen_text_xlsx else "",
        "relationship_graph": relative_path(relationship_graph, package),
        "face_reference_manifest": (
            relative_path(face_reference_manifest, package)
            if face_reference_manifest.is_file() else ""
        ),
        "face_reference_manifest_sha256": (
            sha256_file(face_reference_manifest) if face_reference_manifest.is_file() else ""
        ),
        "face_reference_report": (
            relative_path(face_reference_report, package)
            if face_reference_report.is_file() else ""
        ),
        "face_reference_report_sha256": (
            sha256_file(face_reference_report) if face_reference_report.is_file() else ""
        ),
        "face_reference_summary": face_reference_data,
        "voice_gallery": voice_package,
        "anonymous_speaker_draft": {
            "required": True,
            "speaker_scope": "episode_local",
            "identity_claim": False,
            "whole_series_tsv": (
                relative_path(whole_anonymous_tsv, package) if whole_anonymous_tsv.is_file() else ""
            ),
            "whole_series_xlsx": (
                relative_path(whole_anonymous_xlsx, package) if whole_anonymous_xlsx.is_file() else ""
            ),
            "rule": "Speaker labels are acoustic clusters only. Formal transcription maps them to characters using confirmed voice identity, dialogue logic, and supporting visual evidence.",
        },
        "speech_dominant_audio": {
            "manifest_column": "speech_dominant_audio",
            "legacy_manifest_column": "clean_voice_audio",
            "declared": speech_audio_declared,
            "supplied_episodes": [
                item["episode"] for item in episodes if item.get("speech_dominant_audio")
            ],
            "missing_episodes": [
                item["episode"] for item in episodes
                if speech_audio_declared and not item.get("speech_dominant_audio")
            ],
            "use_rule": (
            "This may be model-separated audio and is not assumed to be original dry voice. Use it for anonymous diarization "
            "and reviewed single-speaker acoustic evidence; fall back to original video audio when it is not supplied."
            ),
        },
        "japanese_name_workbook": name_map_rel,
        "screen_text_workbook": screen_text_rel,
        "preparation_report": relative_path(preparation_report_path, package),
        "episodes": episodes,
        "blocking_issues": blocking,
        "approval_gate": {
            "required": True,
            "ai_review": relative_path(ai_review_dir / "preparation_ai_review.json", package),
            "ai_review_markdown": relative_path(ai_review_dir / "preparation_ai_review.md", package),
            "user_approval": relative_path(user_approval_dir / "preparation_user_approval.json", package),
            "rule": (
                "A fresh AI preparation review and explicit user approval bound to the current "
                "package fingerprint are required before formal script generation."
            ),
        },
        "continuation": {
            "validation_command": f"{sys.executable} {tool} continue-from-preparation --package <package> --require-ready",
            "next_phase": "Map the approved anonymous Speaker draft to character identities, complete each affected episode, then use the episode-boundary confirmation gate.",
            "final_role_authority": "confirmed three-model voice identity plus reviewed dialogue logic; TalkNet and visible identity are supporting evidence only",
        },
    }
    write_json(package / "handoff_manifest.json", handoff)
    write_json(preparation_report_path, {
        "preparation_ready": ready,
        "episodes": len(episodes),
        "ready_episodes": sum(item["status"] == "ready" for item in episodes),
        "blocking_issues": blocking,
        "voice_gallery_ready": voice_package.get("gallery_ready", False),
        "voice_gallery_uncovered_episodes": voice_package.get("uncovered_episodes", []),
        "speech_dominant_audio_declared": speech_audio_declared,
        "speech_dominant_audio_ready_episodes": sum(
            bool(item.get("speech_dominant_audio")) for item in episodes
        ),
        "speech_dominant_audio_missing_episodes": [
            item["episode"] for item in episodes
            if speech_audio_declared and not item.get("speech_dominant_audio")
        ],
        "anonymous_speaker_draft_ready": whole_anonymous_xlsx.is_file(),
        "face_reference_images": int(face_reference_data.get("reference_images", 0)),
        "face_reference_roles": face_reference_data.get("roles_with_images", []),
        "face_reference_missing_roles": face_reference_data.get("missing_roles", []),
        "generation_authorized": False,
        "approval_gate_status": "pending_ai_review",
        "package_layout": str(layout_path),
        "handoff_manifest": str(package / "handoff_manifest.json"),
    })
    print(json.dumps(handoff, ensure_ascii=False, indent=2))
    return 0 if ready else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a dubbing script preparation handoff package")
    parser.add_argument(
        "--episode-manifest",
        help=(
            "TSV with required episode, video, and srt columns; optionally add speech_dominant_audio "
            "(legacy alias: clean_voice_audio) for model-separated speech media"
        ),
    )
    parser.add_argument(
        "--roles",
        help="Internal character-reference draft (.txt/.csv/.tsv/.xlsx); approval happens after package review",
    )
    parser.add_argument("--source-role-workbook", help="Original user-supplied project workbook retained as source evidence")
    parser.add_argument(
        "--extract-frames", action="store_true", default=True,
        help="Extract video frames (3 frames per subtitle) into the preparation package for every episode (default: True)"
    )
    parser.add_argument(
        "--no-extract-frames", action="store_false", dest="extract_frames",
        help="Skip video frame extraction during package preparation"
    )
    parser.add_argument(
        "--frames-per-subtitle", type=int, default=3, choices=[1, 3],
        help="Frames per subtitle entry (default: 3: early, middle, late)"
    )
    parser.add_argument("--out-dir")
    parser.add_argument("--feature-root", help="Current source-production root; defaults to the script directory parent")
    parser.add_argument("--script-root", help="Current script directory containing the preparation package")
    parser.add_argument("--refresh-internal-reference", help="Refresh schema 9 internal role context after editing the draft; invalidates review and approval without changing SRTs, anonymous drafts or reviewed labels")
    parser.add_argument("--refresh-package", help="Rebuild voice galleries in an existing preparation package after in-package edits")
    parser.add_argument("--name-map-xlsx")
    parser.add_argument("--screen-text-xlsx", help="Optional user-supplied 画面字 workbook copied into the preparation package")
    parser.add_argument("--main-skill-dir")
    parser.add_argument(
        "--anonymous-diarization-pipeline",
        help="Required local WeSpeaker or pyannote diarization pipeline for the preparation-stage Speaker01/Speaker02 draft",
    )
    parser.add_argument("--anonymous-device", default="auto")
    parser.add_argument(
        "--workflow-track", choices=["standard", "experimental"], default="standard",
        help="standard: original beta workflow; experimental: subtitle-guided anonymous segmentation (see references/experimental-track.md)",
    )
    parser.add_argument("--identity-model", action="append", default=[], help="Required three-model configuration: name=/absolute/path; repeat for campplus, ecapa512, resnet34")
    parser.add_argument(
        "--beta-voice-dir", "--diagnostic-voice-dir", dest="beta_voice_dir",
        help=(
            "Optional user-owned folder containing one subfolder per approved role and one or more "
            "user-confirmed dry audio files. Builds Beta supporting voice evidence; "
            "--diagnostic-voice-dir is a legacy alias."
        ),
    )
    parser.add_argument(
        "--voice-enrollment-manifest", "--manual-voice-enrollment-manifest",
        dest="voice_enrollment_manifest", action="append",
        help=(
            "User-supplied, manually labeled enrollment TSV; repeat with a disjoint manifest for "
            "leakage-safe cross-fit coverage. Pre-cut audio_path rows may omit start/end."
        ),
    )
    parser.add_argument("--voice-required-roles", help="Optional role/name list; defaults to every approved named role")
    parser.add_argument(
        "--require-face-references", action="store_true",
        help="Block preparation when any named draft role lacks an embedded client-source screenshot",
    )
    parser.add_argument("--require-voice-gallery", action="store_true", help="Block preparation unless every episode has a leakage-safe gallery")
    parser.add_argument("--voice-python", help="Python executable containing the speaker-embedding runtime")
    parser.add_argument("--voice-model", help="Local speaker-embedding model path")
    parser.add_argument("--ffmpeg", help="ffmpeg executable for enrollment audio extraction")
    parser.add_argument("--voice-device", default="auto")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        if args.refresh_internal_reference:
            return refresh_internal_reference(args)
        if args.refresh_package:
            print("WARNING: --refresh-package is a legacy gallery compatibility operation, not the current Beta preparation workflow", file=sys.stderr)
            return refresh_package(args)
        missing = [name for name in ("--episode-manifest", "--roles", "--out-dir") if not getattr(args, name[2:].replace("-", "_"))]
        if missing:
            parser.error("the following arguments are required for a new package: " + ", ".join(missing))
        return build_package(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
