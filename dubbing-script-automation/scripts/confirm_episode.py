#!/usr/bin/env python3
"""Standardized Episode Confirmation & Closeout Script.

Ensures atomic state transition across all artifacts upon human confirmation:
1. TSV row-level status: status='manual', identity_status='confirmed'/'generic', confidence='1.00', review_confidence='1.00'
2. JSON review queue: item status='confirmed'
3. Checkpoint: status='full_episode_mapping_confirmed', user_mapping_confirmed=True
4. Synchronizes both staging and external project directories
5. Performs rigorous post-write audit
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import load_workbook

from dubbing_tool import is_generic_role


APPROVAL_MODES = {"corrected_workbook", "confirmed_unchanged"}


def canonical_episode(raw: str | int) -> str:
    cleaned = str(raw).strip()
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    if not digits:
        raise ValueError(f"Invalid episode identifier: {raw!r}")
    return f"{int(digits):04d}"


def parse_episode_list(expr: str) -> List[str]:
    episodes = []
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int("".join(c for c in start_str if c.isdigit()))
            end = int("".join(c for c in end_str if c.isdigit()))
            for ep in range(start, end + 1):
                episodes.append(f"{ep:04d}")
        else:
            episodes.append(canonical_episode(part))
    return sorted(list(dict.fromkeys(episodes)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_line_endings(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def load_confirmation_workbook(path: Path, ep_id: str) -> List[Dict[str, str]]:
    """Read one user-approved three-column 総合脚本 episode workbook."""
    if not path.is_file():
        raise FileNotFoundError(f"Confirmation workbook not found: {path}")
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if "総合脚本" not in workbook.sheetnames:
            raise ValueError(f"Confirmation workbook must contain 総合脚本: {path}")
        sheet = workbook["総合脚本"]
        headers = [sheet.cell(1, column).value for column in range(1, 4)]
        if headers != ["名前", "タイムコード", "台詞"]:
            raise ValueError(
                "Confirmation workbook headers must be exactly 名前 | タイムコード | 台詞"
            )

        episode_title = f"第{ep_id}話"
        active = False
        current_role = ""
        approved: List[Dict[str, str]] = []
        for row_number in range(2, sheet.max_row + 1):
            raw_role = sheet.cell(row_number, 1).value
            raw_timecode = sheet.cell(row_number, 2).value
            raw_text = sheet.cell(row_number, 3).value
            role_text = str(raw_role).strip() if raw_role not in (None, "") else ""
            if role_text.startswith("第") and role_text.endswith("話") and raw_timecode in (None, ""):
                if active and role_text != episode_title:
                    break
                active = role_text == episode_title
                current_role = ""
                continue
            if not active:
                continue
            if raw_timecode in (None, ""):
                if any(value not in (None, "") for value in (raw_role, raw_text)):
                    raise ValueError(f"Unexpected non-dialogue row {row_number} in {path}")
                continue
            if raw_text is None:
                raise ValueError(f"Missing dialogue at workbook row {row_number} in {path}")
            if role_text:
                current_role = role_text
            if not current_role:
                raise ValueError(f"Missing speaker before workbook row {row_number} in {path}")
            approved.append({
                "workbook_row": str(row_number),
                "role": current_role,
                "start": str(raw_timecode),
                "text": normalized_line_endings(str(raw_text)),
            })
        if not active:
            raise ValueError(f"Confirmation workbook does not contain {episode_title}: {path}")
        if not approved:
            raise ValueError(f"Confirmation workbook contains no dialogue rows for {episode_title}: {path}")
        return approved
    finally:
        workbook.close()



def apply_user_confirmation(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    confirmation_xlsx: Path,
    ep_id: str,
    approval_mode: str,
    approval_note: str,
) -> Dict[str, Any]:
    approval_mode = approval_mode.strip()
    approval_note = approval_note.strip()
    if approval_mode not in APPROVAL_MODES:
        raise ValueError(
            f"Invalid approval mode {approval_mode!r}; expected one of {sorted(APPROVAL_MODES)}"
        )
    if not approval_note:
        raise ValueError("An explicit --approval-note is required; file existence is not user approval")
    approved = load_confirmation_workbook(confirmation_xlsx, ep_id)
    if len(approved) != len(rows):
        raise ValueError(
            f"Confirmation row-count mismatch for episode {ep_id}: "
            f"workbook={len(approved)}, TSV={len(rows)}"
        )
    workbook_sha256 = sha256_file(confirmation_xlsx)
    corrections: List[Dict[str, str]] = []
    approved_rows: List[Dict[str, str]] = []
    for position, (row, accepted) in enumerate(zip(rows, approved), start=1):
        source_index = str(row.get("source_index", "")).strip() or str(position)
        actual_start = str(row.get("start", ""))
        actual_text = normalized_line_endings(str(row.get("text", "")))
        if actual_start != accepted["start"]:
            raise ValueError(
                f"Confirmation timecode mismatch in episode {ep_id}, source row {source_index}: "
                f"workbook={accepted['start']!r}, TSV={actual_start!r}"
            )
        if actual_text != accepted["text"]:
            raise ValueError(
                f"Confirmation dialogue mismatch in episode {ep_id}, source row {source_index}"
            )
        old_role = str(row.get("role", "")).strip()
        accepted_role = accepted["role"]
        row["role"] = accepted_role
        row["user_locked"] = "true"
        row["user_locked_role"] = accepted_role
        row["user_confirmation_sha256"] = workbook_sha256
        row["user_confirmation_row"] = accepted["workbook_row"]
        row["user_confirmation_source"] = confirmation_xlsx.name
        approved_row = {
            "source_index": source_index,
            "workbook_row": accepted["workbook_row"],
            "start": actual_start,
            "text": actual_text,
            "role": accepted_role,
        }
        approved_rows.append(approved_row)
        if old_role != accepted_role:
            corrections.append({
                "source_index": source_index,
                "workbook_row": accepted["workbook_row"],
                "start": actual_start,
                "from_role": old_role,
                "to_role": accepted_role,
            })
    if approval_mode == "confirmed_unchanged" and corrections:
        raise ValueError(
            "confirmed_unchanged was selected, but the workbook changes "
            f"{len(corrections)} role(s); use corrected_workbook with the corrected workbook"
        )
    if approval_mode == "corrected_workbook" and not corrections:
        raise ValueError(
            "corrected_workbook was selected, but the workbook contains no role changes; "
            "use confirmed_unchanged only after the user explicitly confirms the episode is unchanged"
        )
    for field in (
        "user_locked", "user_locked_role", "user_confirmation_sha256",
        "user_confirmation_row", "user_confirmation_source",
    ):
        if field not in fieldnames:
            fieldnames.append(field)
    return {
        "schema_version": 2,
        "episode": ep_id,
        "approval_mode": approval_mode,
        "approval_note": approval_note,
        "approval_is_explicit": True,
        "source_workbook": {
            "path": str(confirmation_xlsx.resolve()),
            "name": confirmation_xlsx.name,
            "sha256": workbook_sha256,
            "bytes": confirmation_xlsx.stat().st_size,
        },
        "row_count": len(approved_rows),
        "approved_rows": approved_rows,
        "correction_count": len(corrections),
        "corrections": corrections,
    }


def audit_episode(ep_dir: Path, staging_dir: Optional[Path], ep_id: str) -> Dict[str, Any]:
    tsv_candidates = [
        ep_dir / f"episode{ep_id}_complete_draft.tsv",
        ep_dir / "complete_episode_draft.tsv",
        ep_dir / f"episode{ep_id}_role_confirmation.tsv",
        ep_dir / "episode_role_confirmation.tsv",
    ]
    if staging_dir and staging_dir.exists():
        tsv_candidates.extend([
            staging_dir / f"episode{ep_id}_complete_draft.tsv",
            staging_dir / f"episode{ep_id}_role_confirmation.tsv",
        ])

    existing_tsvs = [p for p in tsv_candidates if p.exists()]
    cp_path = ep_dir / f"episode{ep_id}_checkpoint.json"
    if not cp_path.exists():
        cp_path = ep_dir / "episode_checkpoint.json"

    res = {
        "episode": ep_id,
        "tsvs_found": len(existing_tsvs),
        "checkpoint_found": cp_path.exists(),
        "checkpoint_status": "missing",
        "user_mapping_confirmed": False,
        "row_count": 0,
        "status_distribution": {},
        "identity_status_distribution": {},
        "confirmation_manifest_found": False,
        "user_locked_rows": 0,
        "is_fully_confirmed": False,
        "issues": [],
    }

    manifest_path = ep_dir / "user_confirmation_manifest.json"
    manifest_data: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            res["confirmation_manifest_found"] = True
            approval_mode = str(manifest_data.get("approval_mode", ""))
            approval_note = str(manifest_data.get("approval_note", "")).strip()
            correction_count = int(manifest_data.get("correction_count", -1))
            if approval_mode not in APPROVAL_MODES or not approval_note:
                res["issues"].append("Confirmation manifest lacks explicit approval mode or note")
            elif approval_mode == "confirmed_unchanged" and correction_count != 0:
                res["issues"].append("Unchanged approval manifest contains role corrections")
            elif approval_mode == "corrected_workbook" and correction_count < 1:
                res["issues"].append("Corrected-workbook approval manifest contains no role corrections")
            source = manifest_data.get("source_workbook") or {}
            source_path = Path(str(source.get("path", "")))
            expected_hash = str(source.get("sha256", ""))
            if not source_path.is_file():
                res["issues"].append(f"Confirmation workbook recorded by manifest is missing: {source_path}")
            elif not expected_hash or sha256_file(source_path).casefold() != expected_hash.casefold():
                res["issues"].append("Confirmation workbook hash differs from user_confirmation_manifest.json")
        except Exception as exc:
            res["issues"].append(f"Invalid user confirmation manifest: {exc}")
    else:
        res["issues"].append("Missing user_confirmation_manifest.json")

    if cp_path.exists():
        try:
            cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
            res["checkpoint_status"] = cp_data.get("status", "unknown")
            res["user_mapping_confirmed"] = bool(cp_data.get("user_mapping_confirmed", False))
            res["checkpoint_row_count"] = cp_data.get("row_count")
        except Exception as e:
            res["issues"].append(f"Invalid checkpoint JSON: {e}")

    if existing_tsvs:
        p = existing_tsvs[0]
        try:
            with p.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter="\t")
                rows = list(reader)
            res["row_count"] = len(rows)
            st_counts: Dict[str, int] = {}
            id_counts: Dict[str, int] = {}
            for r in rows:
                st = r.get("status", "")
                st_counts[st] = st_counts.get(st, 0) + 1
                ids = r.get("identity_status", "")
                id_counts[ids] = id_counts.get(ids, 0) + 1
            locked_rows = sum(
                str(r.get("user_locked", "")).strip().casefold() == "true"
                and str(r.get("role", "")) == str(r.get("user_locked_role", ""))
                for r in rows
            )
            res["user_locked_rows"] = locked_rows
            res["status_distribution"] = st_counts
            res["identity_status_distribution"] = id_counts

            valid_identity = {"confirmed", "generic"}
            all_confirmed = (
                len(rows) > 0
                and st_counts.get("manual", 0) == len(rows)
                and sum(id_counts.get(k, 0) for k in valid_identity) == len(rows)
                and res["checkpoint_status"] in {"full_episode_mapping_confirmed", "first_episode_confirmed_no_reliable_gallery"}
                and res["user_mapping_confirmed"]
                and res["confirmation_manifest_found"]
                and locked_rows == len(rows)
                and not res["issues"]
            )
            res["is_fully_confirmed"] = all_confirmed
            if not all_confirmed:
                if st_counts.get("manual", 0) != len(rows):
                    res["issues"].append(f"TSV row status mismatch: {st_counts}")
                if sum(id_counts.get(k, 0) for k in valid_identity) != len(rows):
                    res["issues"].append(f"TSV identity_status mismatch: {id_counts}")
                if res["checkpoint_status"] != "full_episode_mapping_confirmed":
                    res["issues"].append(f"Checkpoint status: {res['checkpoint_status']}")
                if locked_rows != len(rows):
                    res["issues"].append(
                        f"User lock mismatch: locked={locked_rows}, rows={len(rows)}"
                    )
        except Exception as e:
            res["issues"].append(f"Failed reading TSV {p.name}: {e}")
    else:
        res["issues"].append("No TSVs found")

    return res


def close_episode(
    ep_dir: Path,
    staging_dir: Optional[Path],
    ep_id: str,
    confirmation_xlsx: Path,
    approval_mode: str,
    approval_note: str,
    note: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    # 1. Identify primary TSV
    primary_tsv = ep_dir / f"episode{ep_id}_complete_draft.tsv"
    if not primary_tsv.exists():
        primary_tsv = ep_dir / "complete_episode_draft.tsv"
    if not primary_tsv.exists() and staging_dir:
        primary_tsv = staging_dir / f"episode{ep_id}_complete_draft.tsv"

    if not primary_tsv.exists():
        raise FileNotFoundError(f"Cannot find complete draft TSV for episode {ep_id}")

    with primary_tsv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows:
        raise ValueError(f"Episode {ep_id} TSV {primary_tsv} is empty")

    confirmation_manifest = apply_user_confirmation(
        rows, fieldnames, confirmation_xlsx, ep_id, approval_mode, approval_note,
    )

    # 2. Update rows in memory
    for r in rows:
        r["status"] = "manual"
        r["confidence"] = "1.00"
        r["review_confidence"] = "1.00"
        r["identity_status"] = "generic" if is_generic_role(r.get("role", "")) else "confirmed"
        if not (r.get("identity_evidence") or "").strip():
            r["identity_evidence"] = f"User approved the complete episode mapping: {approval_note}"
        if not (r.get("reviewed_by") or "").strip():
            r["reviewed_by"] = "claude_code_then_user"
        if not (r.get("reviewer_note") or "").strip():
            r["reviewer_note"] = approval_note
    for field in ("identity_evidence", "reviewed_by", "reviewer_note"):
        if field not in fieldnames:
            fieldnames.append(field)

    # Targets to write
    tsv_targets = [
        ep_dir / f"episode{ep_id}_complete_draft.tsv",
        ep_dir / "complete_episode_draft.tsv",
        ep_dir / f"episode{ep_id}_role_confirmation.tsv",
        ep_dir / "episode_role_confirmation.tsv",
    ]
    if staging_dir and staging_dir.exists():
        tsv_targets.extend([
            staging_dir / f"episode{ep_id}_complete_draft.tsv",
            staging_dir / f"episode{ep_id}_role_confirmation.tsv",
        ])

    # 3. Checkpoint targets
    cp_targets = [
        ep_dir / f"episode{ep_id}_checkpoint.json",
        ep_dir / "episode_checkpoint.json",
    ]
    if staging_dir and staging_dir.exists():
        cp_targets.extend([
            staging_dir / f"episode{ep_id}_checkpoint.json",
            staging_dir / "episode_checkpoint.json",
        ])

    cp_primary = ep_dir / f"episode{ep_id}_checkpoint.json"
    if not cp_primary.exists() and staging_dir:
        cp_primary = staging_dir / f"episode{ep_id}_checkpoint.json"

    cp_data = {}
    if cp_primary.exists():
        try:
            cp_data = json.loads(cp_primary.read_text(encoding="utf-8"))
        except Exception:
            pass

    cp_data["status"] = "full_episode_mapping_confirmed"
    cp_data["user_mapping_confirmed"] = True
    cp_data["workflow_stage"] = "beta"
    cp_data["workflow_version"] = "beta1.5wsl"
    cp_data["episode"] = ep_id
    cp_data["row_count"] = len(rows)
    cp_data["user_confirmation_manifest"] = {
        "path": str((ep_dir / "user_confirmation_manifest.json").resolve()),
        "source_workbook_sha256": confirmation_manifest["source_workbook"]["sha256"],
        "locked_rows": len(rows),
        "correction_count": confirmation_manifest["correction_count"],
        "approval_mode": confirmation_manifest["approval_mode"],
        "approval_note": confirmation_manifest["approval_note"],
    }
    cp_data["note"] = note or approval_note

    # 4. JSON review queue targets
    json_targets = [
        ep_dir / f"episode{ep_id}_role_confirmation.json",
    ]
    if staging_dir and staging_dir.exists():
        json_targets.append(staging_dir / f"episode{ep_id}_role_confirmation.json")

    json_primary = ep_dir / f"episode{ep_id}_role_confirmation.json"
    if not json_primary.exists() and staging_dir:
        json_primary = staging_dir / f"episode{ep_id}_role_confirmation.json"

    review_json_data = []
    if json_primary.exists():
        try:
            review_json_data = json.loads(json_primary.read_text(encoding="utf-8"))
            for item in review_json_data:
                item["status"] = "confirmed"
        except Exception:
            pass

    if dry_run:
        return {
            "episode": ep_id,
            "dry_run": True,
            "rows_to_update": len(rows),
            "tsv_targets": [str(p) for p in tsv_targets],
            "checkpoint_targets": [str(p) for p in cp_targets],
            "confirmation_manifest": confirmation_manifest,
        }

    # Write TSVs
    for p in tsv_targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    manifest_text = json.dumps(confirmation_manifest, ensure_ascii=False, indent=2) + "\n"
    manifest_targets = [ep_dir / "user_confirmation_manifest.json"]
    if staging_dir and staging_dir.exists():
        manifest_targets.append(staging_dir / f"episode{ep_id}_user_confirmation_manifest.json")
    for p in manifest_targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(manifest_text, encoding="utf-8")

    # Write Checkpoints
    cp_text = json.dumps(cp_data, ensure_ascii=False, indent=2) + "\n"
    for p in cp_targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cp_text, encoding="utf-8")

    # Write JSON review queue if existed
    if review_json_data:
        json_text = json.dumps(review_json_data, ensure_ascii=False, indent=2) + "\n"
        for p in json_targets:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json_text, encoding="utf-8")

    # Audit writeback
    post_audit = audit_episode(ep_dir, staging_dir, ep_id)
    if not post_audit["is_fully_confirmed"]:
        raise RuntimeError(f"Post-write audit failed for episode {ep_id}: {post_audit['issues']}")

    return {
        "episode": ep_id,
        "status": "confirmed",
        "rows": len(rows),
        "confirmation_manifest": confirmation_manifest,
        "post_audit": post_audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Standardized episode confirmation & closure script")
    parser.add_argument("--project-dir", type=Path, required=True,
                        help="Root path of the project or 01_feature directory")
    parser.add_argument("--staging-dir", type=Path,
                        help="Optional staging directory")
    parser.add_argument("--episode", type=str, help="Single episode (e.g. '0015' or '15')")
    parser.add_argument("--episodes", type=str, help="Multiple episodes or ranges (e.g. '11-20' or '15,16,17')")
    parser.add_argument("--audit-all", action="store_true", help="Audit all existing episodes under episodes directory")
    parser.add_argument(
        "--confirmation-xlsx", type=Path,
        help="Exact user-approved three-column 総合脚本 workbook; required when closing one episode",
    )
    parser.add_argument(
        "--approval-mode", choices=sorted(APPROVAL_MODES),
        help="Required: corrected_workbook for an edited return, or confirmed_unchanged after no-change approval/continue instruction",
    )
    parser.add_argument(
        "--approval-note", type=str,
        help="Required explicit user approval/correction statement; file existence alone never counts",
    )
    parser.add_argument("--note", type=str, help="Optional custom checkpoint note")
    parser.add_argument("--dry-run", action="store_true", help="Inspect without modifying")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    # Locate base episodes directory
    feature_dir = args.project_dir
    if feature_dir.name != "01_feature" and (feature_dir / "01_feature").exists():
        feature_dir = feature_dir / "01_feature"

    episodes_dir = feature_dir / "03_script/02_script_work/episodes"
    if not episodes_dir.exists():
        sys.stderr.write(f"Error: episodes directory not found at {episodes_dir}\n")
        return 1

    staging_dir = args.staging_dir if (args.staging_dir and args.staging_dir.exists()) else None

    # Handle audit-all mode
    if args.audit_all:
        ep_dirs = sorted([d for d in episodes_dir.iterdir() if d.is_dir() and any(c.isdigit() for c in d.name)])
        results = []
        for d in ep_dirs:
            ep_id = canonical_episode(d.name)
            res = audit_episode(d, staging_dir, ep_id)
            results.append(res)

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(f"{'Episode':<10} {'Rows':<8} {'Status':<15} {'User Conf':<12} {'Audit Result'}")
            print("-" * 65)
            for r in results:
                st = r.get("checkpoint_status", "missing")
                uc = "YES" if r.get("user_mapping_confirmed") else "NO"
                passed = "PASS (confirmed)" if r["is_fully_confirmed"] else f"FAIL: {'; '.join(r['issues'])}"
                print(f"{r['episode']:<10} {r['row_count']:<8} {st:<15} {uc:<12} {passed}")
        return 0

    # Determine target episodes
    target_eps = []
    if args.episode:
        target_eps.append(canonical_episode(args.episode))
    elif args.episodes:
        target_eps.extend(parse_episode_list(args.episodes))
    else:
        sys.stderr.write("Error: either --episode, --episodes, or --audit-all must be specified.\n")
        return 1

    if len(target_eps) != 1:
        sys.stderr.write("Error: confirmation closeout accepts exactly one episode at a time.\n")
        return 1
    if args.confirmation_xlsx is None:
        sys.stderr.write("Error: --confirmation-xlsx is required for confirmation closeout.\n")
        return 1
    if args.approval_mode is None or not (args.approval_note or "").strip():
        sys.stderr.write("Error: --approval-mode and a non-empty --approval-note are required for confirmation closeout.\n")
        return 1

    results = []
    for ep_id in target_eps:
        ep_dir = episodes_dir / ep_id
        if not ep_dir.exists():
            sys.stderr.write(f"Warning: episode directory {ep_dir} does not exist, skipping.\n")
            continue
        try:
            res = close_episode(
                ep_dir, staging_dir, ep_id, args.confirmation_xlsx,
                approval_mode=args.approval_mode, approval_note=args.approval_note,
                note=args.note, dry_run=args.dry_run,
            )
            results.append(res)
            if not args.json:
                mode_str = "[DRY-RUN] " if args.dry_run else ""
                print(f"✓ {mode_str}Episode {ep_id}: successfully closed and verified {res.get('rows', 0)} rows.")
        except Exception as exc:
            sys.stderr.write(f"✗ Episode {ep_id} failed: {exc}\n")
            return 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
