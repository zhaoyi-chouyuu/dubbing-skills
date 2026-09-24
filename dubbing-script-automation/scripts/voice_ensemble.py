#!/usr/bin/env python3
"""Merge two or three independent voice-model evidence tables without assigning roles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_MODELS = ("campplus", "ecapa512", "resnet34")
KEY_FIELDS = ("episode", "source_indices", "semantic_unit", "acoustic_turn_id")
OUTPUT_FIELDS = [
    *KEY_FIELDS,
    "expected_role",
    "ensemble_status",
    "ensemble_candidate",
    "agreeing_models",
    "eligible_models",
    "model_candidates_json",
    "failed_models_json",
    "ensemble_reason",
]


class EnsembleError(RuntimeError):
    pass


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise EnsembleError(f"Evidence TSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise EnsembleError(f"Evidence TSV is empty: {path}")
    return rows


def row_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple((row.get(field) or "").strip() for field in KEY_FIELDS)


def cmd_merge(args: argparse.Namespace) -> int:
    sources: dict[str, Path] = {}
    for value in args.evidence:
        if "=" not in value:
            raise EnsembleError("--evidence must use model=/absolute/path.tsv")
        name, raw_path = value.split("=", 1)
        name = name.strip().casefold()
        if name in sources:
            raise EnsembleError(f"Duplicate evidence model: {name}")
        sources[name] = Path(raw_path).resolve()
    source_names = set(sources)
    extra = sorted(source_names - set(REQUIRED_MODELS))
    if extra or len(source_names) not in {2, 3}:
        raise EnsembleError(
            f"Expected two or three distinct models from {REQUIRED_MODELS}; "
            f"received={sorted(source_names)}, extra={extra}"
        )

    failed_reports: dict[str, dict[str, Any]] = {}
    for value in args.failed_model:
        if "=" not in value:
            raise EnsembleError("--failed-model must use model=/absolute/gallery.report.json")
        name, raw_path = value.split("=", 1)
        name = name.strip().casefold()
        path = Path(raw_path).resolve()
        if name in failed_reports:
            raise EnsembleError(f"Duplicate failed model report: {name}")
        if name not in REQUIRED_MODELS:
            raise EnsembleError(f"Unknown failed model: {name}")
        if not path.is_file():
            raise EnsembleError(f"Failed-model report not found: {path}")
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            report.get("model_kind") != name
            or report.get("gallery_ready") is not False
            or report.get("role_scoring_ready") is True
        ):
            raise EnsembleError(
                f"{name} failed-model report must declare model_kind={name}, gallery_ready=false, "
                "and no scoreable role subset"
            )
        failed_reports[name] = {
            "report_path": str(path),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "gallery_issues": report.get("gallery_issues", []),
        }

    missing_models = set(REQUIRED_MODELS) - source_names
    if len(source_names) == 2 and set(failed_reports) != missing_models:
        raise EnsembleError(
            "Two-model mode requires one failed gallery report for the attempted third model; "
            f"missing_models={sorted(missing_models)}, failed_reports={sorted(failed_reports)}"
        )
    if len(source_names) == 3 and failed_reports:
        raise EnsembleError("--failed-model is only valid when exactly two evidence tables are supplied")

    if len(set(sources.values())) != len(sources):
        raise EnsembleError("Each model must have its own independently generated evidence file")
    file_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources.values()]
    if len(set(file_hashes)) != len(file_hashes):
        raise EnsembleError("Identical evidence content cannot represent independent models")
    active_models = tuple(name for name in REQUIRED_MODELS if name in sources)
    indexed: dict[str, dict[tuple[str, ...], dict[str, str]]] = {}
    key_sets: list[set[tuple[str, ...]]] = []
    for name in active_models:
        rows = read_tsv(sources[name])
        table: dict[tuple[str, ...], dict[str, str]] = {}
        model_fingerprints = {(row.get("voice_model_fingerprint") or "").strip() for row in rows}
        if len(model_fingerprints) != 1 or "" in model_fingerprints:
            raise EnsembleError(f"{name} must contain one non-empty model fingerprint")
        for row in rows:
            if row.get("voice_model_kind") != name:
                raise EnsembleError(f"{name} evidence declares another or missing voice_model_kind")
            if not row.get("episode", "").strip() or not row.get("voice_gallery_id", "").strip():
                raise EnsembleError(f"{name} evidence lacks episode or gallery provenance")
            key = row_key(row)
            if key in table:
                raise EnsembleError(f"{name} contains duplicate evidence key: {key}")
            table[key] = row
        indexed[name] = table
        key_sets.append(set(table))
    fingerprints = [next(iter(table.values()))["voice_model_fingerprint"] for table in indexed.values()]
    if len(set(fingerprints)) != len(active_models):
        raise EnsembleError("Independent model fingerprints are required")
    if any(keys != key_sets[0] for keys in key_sets[1:]):
        raise EnsembleError("The evidence tables do not contain the same acoustic-turn keys")

    output: list[dict[str, Any]] = []
    for key in sorted(key_sets[0]):
        model_rows = {name: indexed[name][key] for name in active_models}
        eligible = {
            name: (row.get("voice_candidate") or "").strip()
            for name, row in model_rows.items()
            if (row.get("voice_status") or "").strip() == "eligible"
            and (row.get("voice_candidate") or "").strip()
        }
        votes = Counter(eligible.values())
        winner, vote_count = votes.most_common(1)[0] if votes else ("", 0)
        if len(active_models) == 3 and vote_count == 3:
            status, reason = "consensus_3_of_3", "all three eligible models agree"
        elif len(active_models) == 3 and vote_count == 2:
            status, reason = "consensus_2_of_3", "two eligible models agree"
        elif len(active_models) == 2 and vote_count == 2:
            status, reason = "consensus_2_of_3", "two eligible models agree; third model was attempted and abstained after role-level QC"
        elif len(eligible) >= 2:
            status, winner, reason = "model_disagreement", "", "eligible models disagree"
        else:
            status, winner, reason = "insufficient_models", "", "fewer than two eligible model votes"
        agreeing = [name for name, candidate in eligible.items() if winner and candidate == winner]
        expected_roles = {
            (row.get("expected_role") or "").strip()
            for row in model_rows.values()
            if (row.get("expected_role") or "").strip()
        }
        if len(expected_roles) > 1:
            raise EnsembleError(f"Expected-role mismatch across models for key {key}")
        output.append({
            **dict(zip(KEY_FIELDS, key)),
            "expected_role": next(iter(expected_roles), ""),
            "ensemble_status": status,
            "ensemble_candidate": winner,
            "agreeing_models": "|".join(agreeing),
            "eligible_models": "|".join(eligible),
            "model_candidates_json": json.dumps({
                name: {
                    "status": row.get("voice_status", ""),
                    "candidate": row.get("voice_candidate", ""),
                    "similarity": row.get("voice_similarity", ""),
                    "margin": row.get("voice_margin", ""),
                    **{field: value for field, value in row.items() if field.startswith("voice_")},
                    "evidence_path": str(sources[name]),
                }
                for name, row in model_rows.items()
            }, ensure_ascii=False, sort_keys=True),
            "failed_models_json": json.dumps(failed_reports, ensure_ascii=False, sort_keys=True),
            "ensemble_reason": reason,
        })

    out = Path(args.out_tsv).resolve()
    if out.exists() and not args.overwrite:
        raise EnsembleError(f"Output exists: {out}. Use --overwrite or a new path.")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(output)
    counts = Counter(str(row["ensemble_status"]) for row in output)
    print(json.dumps({
        "output": str(out),
        "rows": len(output),
        "statuses": dict(counts),
        "evidence_models": list(active_models),
        "failed_models": sorted(failed_reports),
        "automatic_identity_assignment": False,
        "workflow_version": "beta1.5wsl",
        "wavlm_used": False,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge two or three of CAM++, ECAPA512, and ResNet34 voice evidence")
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--failed-model", action="append", default=[])
    parser.add_argument("--out-tsv", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        return cmd_merge(args)
    except (OSError, ValueError, EnsembleError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
