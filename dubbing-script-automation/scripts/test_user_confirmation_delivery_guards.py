#!/usr/bin/env python3
"""Regression checks for user-confirmation locks and atomic delivery promotion."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook

from confirm_episode import close_episode
from dubbing_tool import USER_CONFIRMATION_FIELDS, read_label_rows, write_label_rows
from finalize_japanese_delivery import (
    DeliveryError,
    commit_staged_output,
    validate_formal_output_target,
    verify_user_confirmation_locks,
)


FIELDS = [
    "episode", "source_index", "start", "end", "role", "confidence",
    "status", "frame", "text", "provider", "model", "review_confidence",
    "identity_status", "identity_evidence", "semantic_unit", "visual_class",
    "semantic_evidence", "coarse_audio_status", "audio_conflict", "reviewed_by",
    "reviewer_note",
]


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def build_confirmation(path: Path, roles: tuple[str, str]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "総合脚本"
    sheet.append(["名前", "タイムコード", "台詞"])
    sheet.append(["第0003話", None, None])
    sheet.append([roles[0], "00:00:09,033", "皇后様のお越しです..."])
    sheet.append([roles[1], "00:00:24,380", "裴貴妃"])
    workbook.save(path)


def fixture_rows(first_role: str, second_role: str) -> list[dict[str, str]]:
    common = {
        "confidence": "0.80", "status": "review", "frame": "",
        "provider": "regression", "model": "fixture", "review_confidence": "0.80",
        "identity_status": "uncertain", "visual_class": "not_reviewed",
        "coarse_audio_status": "not_required", "audio_conflict": "false",
        "reviewed_by": "claude-code", "reviewer_note": "episode-specific structured review",
    }
    return [
        {
            **common, "episode": "0003", "source_index": "4", "start": "00:00:09,033",
            "end": "00:00:12,000", "role": first_role,
            "text": "皇后様のお越しです...", "semantic_unit": "episode0003-unit-01",
            "semantic_evidence": "Arrival announcement identifies the speaking attendant context.",
            "identity_evidence": "Initial draft identity evidence for the first speaking turn.",
        },
        {
            **common, "episode": "0003", "source_index": "7", "start": "00:00:24,380",
            "end": "00:00:25,000", "role": second_role,
            "text": "裴貴妃", "semantic_unit": "episode0003-unit-02",
            "semantic_evidence": "Direct address occurs in a separate later speaking turn.",
            "identity_evidence": "Initial draft identity evidence for the second speaking turn.",
        },
    ]


def write_source_srt(path: Path) -> None:
    path.write_text(
        "4\n00:00:09,033 --> 00:00:12,000\n皇后様のお越しです...\n\n"
        "7\n00:00:24,380 --> 00:00:25,000\n裴貴妃\n",
        encoding="utf-8",
    )


def assert_qc_passes(episode_dir: Path) -> None:
    report = episode_dir / "qc_report.json"
    completed = subprocess.run(
        [
            sys.executable, str(Path(__file__).with_name("dubbing_tool.py")), "qc",
            "--tsv", str(episode_dir / "complete_episode_draft.tsv"),
            "--srt", str(episode_dir / "source.srt"),
            "--report", str(report), "--require-reviewed-all", "--fail-on-issues",
        ],
        check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(f"QC failed:\n{completed.stdout}\n{completed.stderr}")
    summary = json.loads(report.read_text(encoding="utf-8"))
    assert summary["production_ready"] is True
    assert summary["independent_audit_passed"] is True


def test_confirmation_and_lock(root: Path) -> None:
    episode_dir = root / "episodes" / "0003"
    rows = fixture_rows("皇后付き侍女", "皇后侍女")
    write_tsv(episode_dir / "complete_episode_draft.tsv", rows)
    write_source_srt(episode_dir / "source.srt")
    (episode_dir / "episode_checkpoint.json").write_text(
        json.dumps({"episode": "0003"}), encoding="utf-8",
    )
    confirmation = root / "6600_第0003話_話者確認_用户红字回写.xlsx"
    build_confirmation(confirmation, ("宦官（モブ）", "皇后付き侍女"))

    result = close_episode(
        episode_dir, None, "0003", confirmation,
        approval_mode="corrected_workbook",
        approval_note="用户红字修正第0003话后要求继续作业",
    )
    assert result["post_audit"]["is_fully_confirmed"] is True
    confirmed = read_tsv(episode_dir / "complete_episode_draft.tsv")
    assert [row["role"] for row in confirmed] == ["宦官（モブ）", "皇后付き侍女"]
    assert all(row["user_locked"] == "true" for row in confirmed)
    assert [row["user_locked_role"] for row in confirmed] == ["宦官（モブ）", "皇后付き侍女"]
    assert all(row["status"] == "manual" for row in confirmed)
    assert all(row["identity_status"] == "confirmed" for row in confirmed)
    assert result["confirmation_manifest"]["approval_mode"] == "corrected_workbook"
    assert_qc_passes(episode_dir)

    rewritten = episode_dir / "rewritten_by_dubbing_tool.tsv"
    write_label_rows(rewritten, confirmed, overwrite=False)
    preserved = read_label_rows(rewritten)
    assert all(field in preserved[0] for field in USER_CONFIRMATION_FIELDS)
    assert [row["user_locked_role"] for row in preserved] == ["宦官（モブ）", "皇后付き侍女"]

    overwritten = [dict(row) for row in confirmed]
    overwritten[0]["role"] = "宦官"
    try:
        write_label_rows(episode_dir / "must_not_write.tsv", overwritten, overwrite=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("dubbing_tool accepted an overwrite of a user-confirmed role")

    final_roles = episode_dir / "final_roles.tsv"
    final_roles.write_bytes((episode_dir / "complete_episode_draft.tsv").read_bytes())
    verify_user_confirmation_locks(episode_dir, read_tsv(final_roles))

    tampered = read_tsv(final_roles)
    tampered[0]["role"] = "宦官"
    all_fields = list(tampered[0])
    with final_roles.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(tampered)
    try:
        verify_user_confirmation_locks(episode_dir, read_tsv(final_roles))
    except DeliveryError:
        pass
    else:
        raise AssertionError("Tampered user-approved role was not rejected")


def test_continue_means_confirmed_unchanged(root: Path) -> None:
    episode_dir = root / "unchanged" / "episodes" / "0003"
    expected_roles = ("宦官（モブ）", "皇后付き侍女")
    write_tsv(
        episode_dir / "complete_episode_draft.tsv",
        fixture_rows(*expected_roles),
    )
    write_source_srt(episode_dir / "source.srt")
    confirmation = root / "unchanged" / "6600_第0003話_話者確認.xlsx"
    confirmation.parent.mkdir(parents=True, exist_ok=True)
    build_confirmation(confirmation, expected_roles)

    result = close_episode(
        episode_dir, None, "0003", confirmation,
        approval_mode="confirmed_unchanged",
        approval_note="用户收到普通话者确认文件后指示继续往下作业",
    )
    assert result["confirmation_manifest"]["correction_count"] == 0
    assert result["confirmation_manifest"]["approval_mode"] == "confirmed_unchanged"
    assert [row["role"] for row in read_tsv(episode_dir / "complete_episode_draft.tsv")] == list(expected_roles)
    assert_qc_passes(episode_dir)


def test_unchanged_mode_rejects_changed_workbook(root: Path) -> None:
    episode_dir = root / "mismatch" / "episodes" / "0003"
    write_tsv(
        episode_dir / "complete_episode_draft.tsv",
        fixture_rows("皇后付き侍女", "皇后侍女"),
    )
    confirmation = root / "mismatch" / "6600_第0003話_話者確認.xlsx"
    confirmation.parent.mkdir(parents=True, exist_ok=True)
    build_confirmation(confirmation, ("宦官（モブ）", "皇后付き侍女"))
    try:
        close_episode(
            episode_dir, None, "0003", confirmation,
            approval_mode="confirmed_unchanged",
            approval_note="用户指示继续往下作业",
        )
    except ValueError as exc:
        assert "changes 2 role" in str(exc)
    else:
        raise AssertionError("Changed workbook was accepted as confirmed_unchanged")
    assert read_tsv(episode_dir / "complete_episode_draft.tsv")[0]["role"] == "皇后付き侍女"


def test_atomic_promotion(root: Path) -> None:
    names = ["作品_総合脚本.xlsx", "作品_香盤表.xlsx", "作品_登場人物設定表.xlsx"]
    output_dir = root / "03_final_delivery"
    output_dir.mkdir()
    for name in names:
        (output_dir / name).write_text("old", encoding="utf-8")
    validate_formal_output_target(output_dir, names, overwrite=True)

    staging_dir = root / ".03_final_delivery.staging-test"
    staging_dir.mkdir()
    for name in names:
        (staging_dir / name).write_text("new", encoding="utf-8")
    commit_staged_output(staging_dir, output_dir)
    assert sorted(path.name for path in output_dir.iterdir()) == sorted(names)
    assert all((output_dir / name).read_text(encoding="utf-8") == "new" for name in names)

    rollback_stage = root / ".03_final_delivery.staging-rollback"
    rollback_stage.mkdir()
    for name in names:
        (rollback_stage / name).write_text("must-rollback", encoding="utf-8")

    def fail_after_commit() -> None:
        raise RuntimeError("simulated report failure")

    try:
        commit_staged_output(rollback_stage, output_dir, post_commit=fail_after_commit)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Late delivery failure did not abort the transaction")
    assert all((output_dir / name).read_text(encoding="utf-8") == "new" for name in names)

    (output_dir / "unexpected.xlsx").write_text("bad", encoding="utf-8")
    try:
        validate_formal_output_target(output_dir, names, overwrite=True)
    except DeliveryError:
        pass
    else:
        raise AssertionError("Extra formal-delivery file was not rejected")
    assert (output_dir / "作品_総合脚本.xlsx").read_text(encoding="utf-8") == "new"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dubbing-confirmation-regression-") as temp:
        root = Path(temp)
        test_confirmation_and_lock(root)
        test_continue_means_confirmed_unchanged(root)
        test_unchanged_mode_rejects_changed_workbook(root)
        test_atomic_promotion(root)
    print("PASS: corrected/unchanged user confirmation, QC locks, and atomic delivery guards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
