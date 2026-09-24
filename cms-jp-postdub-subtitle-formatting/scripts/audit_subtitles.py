#!/usr/bin/env python3
"""Text-only audit for CMS Japanese post-dubbing SRT/ASS subtitles."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
JP_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
TIME_SRT_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$"
)
TIME_ASS_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.](\d{2})$")
ASS_TAG_RE = re.compile(r"\{[^{}]*\}")

PROTECTED_BOUNDARIES = (
    (re.compile(r"て$"), re.compile(r"^(いる|いく|くる|しまう|みる|見せる|ください|ほしい|やる|おく|ある|ない)"), "て形・補助表現を分断"),
    (re.compile(r"で$"), re.compile(r"^も"), "助词组合「でも」を分断"),
    (re.compile(r"ダ$"), re.compile(r"^メ"), "单词「ダメ」を分断"),
    (re.compile(r"なけ$"), re.compile(r"^れば"), "固定结构「なければ」を分断"),
    (re.compile(r"なく$"), re.compile(r"^なる"), "固定结构「なくなる」を分断"),
)


@dataclass
class Issue:
    episode: str
    filename: str
    cue_index: str
    timecode: str
    severity: str
    rule_id: str
    original_text: str
    proposed_text: str
    reason: str


@dataclass
class Cue:
    index: str
    timecode: str
    lines: list[str]


def decode_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("subtitle", data, 0, min(1, len(data)), "unsupported encoding")


def cpp_width(text: str) -> int:
    width = 0
    for char in ASS_TAG_RE.sub("", text):
        if char in "\r\n":
            continue
        if ord(char) < 128 or unicodedata.east_asian_width(char) == "H":
            width += 1
        else:
            width += 2
    return width


def japanese_count(text: str) -> int:
    return len(JP_CHAR_RE.findall(ASS_TAG_RE.sub("", text)))


def srt_time_seconds(timecode: str) -> float | None:
    match = TIME_SRT_RE.match(timecode.strip())
    if not match:
        return None
    values = [int(value) for value in match.groups()]
    start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
    end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
    return max(0.0, end - start)


def ass_timestamp_seconds(value: str) -> float | None:
    match = TIME_ASS_RE.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds, centiseconds = (int(item) for item in match.groups())
    return hours * 3600 + minutes * 60 + seconds + centiseconds / 100


def cue_duration(timecode: str) -> float | None:
    if "-->" in timecode:
        return srt_time_seconds(timecode)
    if " --> " not in timecode and "--" in timecode:
        return None
    if "|" in timecode:
        start, end = timecode.split("|", 1)
        start_s = ass_timestamp_seconds(start)
        end_s = ass_timestamp_seconds(end)
        if start_s is None or end_s is None:
            return None
        return max(0.0, end_s - start_s)
    return None


def parse_srt(path: Path, text: str) -> tuple[list[Cue], list[Issue]]:
    cues: list[Cue] = []
    issues: list[Issue] = []
    blocks = re.split(r"\r?\n[ \t]*\r?\n", text.strip()) if text.strip() else []
    for block_no, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            issues.append(
                Issue("", path.name, str(block_no), "", "阻断", "SRT_STRUCTURE", block, "", "字幕块结构或时间码格式异常")
            )
            continue
        cues.append(Cue(lines[0].strip(), lines[1].strip(), lines[2:]))
    return cues, issues


def parse_ass(path: Path, text: str) -> tuple[list[Cue], list[Issue]]:
    cues: list[Cue] = []
    issues: list[Issue] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(":", 1)[1].lstrip().split(",", 9)
        if len(parts) != 10:
            issues.append(
                Issue("", path.name, str(line_no), "", "阻断", "ASS_STRUCTURE", line, "", "Dialogue 行字段不足")
            )
            continue
        start, end, body = parts[1].strip(), parts[2].strip(), parts[9]
        cues.append(Cue(str(line_no), f"{start}|{end}", re.split(r"\\[Nn]", body)))
    return cues, issues


def episode_from_path(path: Path) -> str:
    match = re.search(r"(?:_|\b)(\d{1,4})(?=\.(?:srt|ass)$)", path.name, re.IGNORECASE)
    return str(int(match.group(1))) if match else ""


def issue_for(path: Path, cue: Cue, severity: str, rule_id: str, reason: str, proposed: str = "") -> Issue:
    return Issue(
        episode_from_path(path),
        path.name,
        cue.index,
        cue.timecode,
        severity,
        rule_id,
        "\\n".join(cue.lines),
        proposed,
        reason,
    )


def is_two_speaker(lines: Sequence[str]) -> bool:
    nonempty = [line.strip() for line in lines if line.strip()]
    return len(nonempty) == 2 and all(line.startswith("-") for line in nonempty)


def is_screen_text(lines: Sequence[str]) -> bool:
    merged = "".join(line.strip() for line in lines)
    return len(merged) >= 2 and merged.startswith("[") and merged.endswith("]")


def audit_cue(path: Path, cue: Cue, line_limit: int) -> list[Issue]:
    issues: list[Issue] = []
    original = "\n".join(cue.lines)
    merged = "".join(line.strip() for line in cue.lines)
    total_width = cpp_width(merged)

    if "。" in original:
        issues.append(issue_for(path, cue, "警告", "JP_PERIOD", "句号应删除", original.replace("。", "")))
    if "、" in original:
        issues.append(issue_for(path, cue, "警告", "JP_COMMA", "顿号应替换为一个半角空格", original.replace("、", " ")))
    if re.search(r"[０-９]", original):
        issues.append(issue_for(path, cue, "阻断", "FULLWIDTH_DIGIT", "禁止全角阿拉伯数字", original.translate(FULLWIDTH_DIGITS)))
    if re.search(r"……|\.{2,}|．{2,}", original):
        proposed = re.sub(r"……|\.{2,}|．{2,}", "…", original)
        issues.append(issue_for(path, cue, "警告", "ELLIPSIS", "省略号应统一为单个「…」", proposed))
    if re.search(r"(^|\n)[ \t]+|[ \t]+($|\n)", original):
        issues.append(issue_for(path, cue, "警告", "EDGE_SPACE", "行首或行尾存在多余空格"))
    if re.search(r" {2,}", original):
        issues.append(issue_for(path, cue, "警告", "MULTI_SPACE", "连续半角空格应压缩为一个"))

    if len(cue.lines) > 2 and not is_screen_text(cue.lines):
        issues.append(issue_for(path, cue, "阻断", "TOO_MANY_LINES", "普通对白超过两行"))

    if is_two_speaker(cue.lines) and any(not line.strip().startswith("- ") for line in cue.lines):
        proposed = "\n".join(re.sub(r"^-\s*", "- ", line.strip()) for line in cue.lines)
        issues.append(issue_for(path, cue, "警告", "DIALOGUE_DASH_SPACE", "双人对白横杆后应保留一个半角空格", proposed))

    for line_no, line in enumerate(cue.lines, 1):
        width = cpp_width(line)
        if width > line_limit:
            issues.append(issue_for(path, cue, "阻断", "LINE_WIDTH", f"第{line_no}行宽度{width}超过上限{line_limit}"))

    if total_width > 48:
        issues.append(issue_for(path, cue, "阻断", "CUE_WIDTH", f"单条总宽度{total_width}超过上限48"))

    if len(cue.lines) > 1 and total_width <= line_limit and not is_two_speaker(cue.lines) and not is_screen_text(cue.lines):
        issues.append(issue_for(path, cue, "警告", "UNNEEDED_LINE_BREAK", "合并后未超单行上限，疑似多余换行；无文本证据时需人工确认"))

    if len(cue.lines) > 1:
        for line_no, line in enumerate(cue.lines, 1):
            if japanese_count(line) <= 2:
                issues.append(issue_for(path, cue, "警告", "ORPHAN_LINE", f"第{line_no}行仅含1-2个日文字，疑似孤行"))
        for upper, lower in zip(cue.lines, cue.lines[1:]):
            upper_clean, lower_clean = upper.rstrip(), lower.lstrip()
            for end_pattern, start_pattern, reason in PROTECTED_BOUNDARIES:
                if end_pattern.search(upper_clean) and start_pattern.search(lower_clean):
                    issues.append(issue_for(path, cue, "阻断", "PROTECTED_BREAK", reason))
            if re.match(r"^は(?!るか|ず|い|じめ|っ|め|な|や|ら|ん)", lower_clean) or re.match(
                r"^も(?!う|っと|し|の|ら|て|った)", lower_clean
            ) or re.match(r"^と(?!んだ|ころ|てもかく)", lower_clean) or lower_clean.startswith("を"):
                issues.append(issue_for(path, cue, "人工复核", "LINE_START_PARTICLE", "下行以助词开始，需检查是否拆开语法单位"))

    if re.search(r"わかった(?=(いいだろう|そうだ|任せろ|行くぞ|もう))", merged):
        proposed = re.sub(r"わかった(?=(いいだろう|そうだ|任せろ|行くぞ|もう))", "わかった ", merged)
        issues.append(issue_for(path, cue, "警告", "SEMANTIC_SPACE", "疑似两个独立短句黏连；请结合相邻字幕确认", proposed))
    if re.search(r"挨拶はいい(?=[\u3040-\u30ff\u3400-\u9fff])", merged):
        proposed = re.sub(r"挨拶はいい(?=[\u3040-\u30ff\u3400-\u9fff])", "挨拶はいい\n", merged)
        issues.append(issue_for(path, cue, "警告", "SEMANTIC_BOUNDARY", "已确认的独立意群边界；请结合相邻字幕复核", proposed))
    if re.search(r"なさい(?=[\u3040-\u30ff\u3400-\u9fff])", merged):
        issues.append(issue_for(path, cue, "人工复核", "COMMAND_BOUNDARY", "命令句后疑似接新意群；请检查是否需要换行"))

    duration = cue_duration(cue.timecode)
    if duration is not None and duration > 0:
        actual_cps = (total_width / 2) / duration
        if actual_cps >= 14.9999:
            issues.append(issue_for(path, cue, "阻断", "CPS", f"实际CPS约{actual_cps:.1f}，达到15以上；只报告，不改词或时间码"))
        elif actual_cps > 10.0001:
            issues.append(issue_for(path, cue, "警告", "CPS", f"实际CPS约{actual_cps:.1f}，超过主要标准10；只报告"))

    return issues


def discover_inputs(inputs: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item).expanduser()
        if path.is_dir():
            candidates = path.rglob("*")
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.is_file() or candidate.name.startswith("._"):
                continue
            if candidate.suffix.lower() in {".srt", ".ass"}:
                files.append(candidate)
    return sorted(set(files), key=lambda value: str(value))


def audit_file(path: Path, line_limit: int) -> list[Issue]:
    text = decode_text(path)
    if path.suffix.lower() == ".srt":
        cues, issues = parse_srt(path, text)
    else:
        cues, issues = parse_ass(path, text)
    for cue in cues:
        issues.extend(audit_cue(path, cue, line_limit))
    return issues


def write_report(path: Path, issues: Sequence[Issue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "episode": "集数",
        "filename": "文件名",
        "cue_index": "字幕编号",
        "timecode": "时间码",
        "severity": "严重级别",
        "rule_id": "规则编号",
        "original_text": "原文本",
        "proposed_text": "建议文本",
        "reason": "说明",
    }
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields.values()))
        writer.writeheader()
        for issue in issues:
            row = asdict(issue)
            writer.writerow({column: row[key] for key, column in fields.items()})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="SRT/ASS files or directories")
    parser.add_argument("--screen", choices=("vertical", "horizontal"), default="vertical")
    parser.add_argument("--report", required=True, help="Output CSV path")
    parser.add_argument("--fail-on-error", action="store_true", help="Return exit code 2 when blockers exist")
    args = parser.parse_args(argv)

    files = discover_inputs(args.inputs)
    if not files:
        parser.error("No SRT/ASS files found")

    line_limit = 24 if args.screen == "vertical" else 32
    issues: list[Issue] = []
    for path in files:
        try:
            issues.extend(audit_file(path, line_limit))
        except Exception as exc:  # Keep batch reporting intact.
            issues.append(Issue("", path.name, "", "", "阻断", "FILE_READ", "", "", str(exc)))

    report_path = Path(args.report).expanduser()
    write_report(report_path, issues)
    blockers = sum(issue.severity == "阻断" for issue in issues)
    warnings = sum(issue.severity == "警告" for issue in issues)
    reviews = sum(issue.severity == "人工复核" for issue in issues)
    print(f"files={len(files)} issues={len(issues)} blockers={blockers} warnings={warnings} reviews={reviews}")
    print(f"report={report_path}")
    return 2 if args.fail_on_error and blockers else 0


if __name__ == "__main__":
    sys.exit(main())
