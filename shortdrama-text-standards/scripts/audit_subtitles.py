#!/usr/bin/env python3
"""Text-only compliance audit for Japanese short-drama SRT/ASS subtitles.

Enforces Compliance v1.2.0 standards:
- Strictly below 12 characters per line (max 11 displayed characters)
- Maximum 2 lines per cue, maximum 22 total characters per cue
- All punctuation half-width except middle dot ・ inside foreign names
- Sentence-final periods forbidden
- In-sentence commas/顿号 forbidden, replaced by single half-width space
- Ellipses must be ASCII '...' (counts as 3 chars), '…' forbidden
- Dual speakers format must be '-台词' (no space after hyphen)
- Arabic digits must be ASCII 0-9
- Kana prolonged mark 'ー' protected from lookalike dashes
- CPS <= 20 target, >= 30 blocking
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation):
        pass



FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
JP_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
TIME_SRT_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$"
)
TIME_ASS_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.](\d{2})$")
ASS_TAG_RE = re.compile(r"\{[^{}]*\}")
DASH_LOOKALIKES = set("―—–−－ｰ")

TABOO_TERMS = (
    ("陳虎", "NAME_TABOO", "人名「陳虎」谐音不雅脏话，标准规范要求替换为「陳豹」等"),
    ("妻よ", "TITLE_TABOO", "日常台词中称谓「妻よ」生硬不自然，标准推荐直接叫名字"),
    ("ア強", "TITLE_TABOO", "汉语前缀「阿」禁止音译为「ア」，标准推荐为「強」或「強さん」"),
    ("東子", "TITLE_TABOO", "男性角色「东子」避免译为「東子」（多为女名），推荐「東」或「東ちゃん」"),
    ("顔を叩く", "GLOSSARY_TABOO", "「打脸」严禁字面直译为「顔を叩く」，标准推荐「見返す」"),
    ("白い月光", "GLOSSARY_TABOO", "「白月光」严禁直译为「白い月光」，标准推荐「忘れられない人/初恋の人」"),
    ("普通で自信のある男", "GLOSSARY_TABOO", "「普信男」严禁字面直译，标准推荐「勘違い男/痛い男」"),
    ("三十年河の東", "GLOSSARY_TABOO", "谚语严禁字面直译，标准推荐「世の中は変わるものだ」"),
    ("木が倒れると猿が散る", "GLOSSARY_TABOO", "谚语严禁字面直译，标准推荐「勢いがなくなれば人も去る」"),
)

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


def clean_line_text(line: str) -> str:
    return ASS_TAG_RE.sub("", line).strip("\r\n")


def displayed_length(line: str) -> int:
    clean = clean_line_text(line)
    return len(clean)


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


def audit_cue(path: Path, cue: Cue, line_char_cap: int = 11) -> list[Issue]:
    issues: list[Issue] = []
    original = "\n".join(cue.lines)

    # 1. 标点合规检查 (全面半角化标准)
    if "。" in original:
        issues.append(issue_for(path, cue, "阻断", "JP_PERIOD", "严禁全角句号「。」，末尾句号必须删除", original.replace("。", "")))
    if re.search(r"\.\s*$", original):
        issues.append(issue_for(path, cue, "阻断", "SENTENCE_FINAL_DOT", "对白末尾禁止句号「.」", re.sub(r"\.\s*$", "", original)))

    if any(c in original for c in ("、", "，")):
        issues.append(issue_for(path, cue, "阻断", "JP_COMMA", "严禁中文/日文逗号（「、」「，」），需停顿请使用单个半角空格", re.sub(r"[、，]", " ", original)))
    if "," in original:
        issues.append(issue_for(path, cue, "阻断", "ASCII_COMMA", "台词中严禁逗号「,」，需停顿请使用单个半角空格", original.replace(",", " ")))

    if any(c in original for c in ("？", "！")):
        proposed = original.replace("？", "?").replace("！", "!")
        issues.append(issue_for(path, cue, "阻断", "FULLWIDTH_QUESTION_EXCLAMATION", "问号与感叹号必须为半角「?」「!」，严禁全角「？」「！」", proposed))

    if re.search(r"[?!]{2,}|! \?|\? !", original):
        issues.append(issue_for(path, cue, "阻断", "STACKED_PUNCTUATION", "严禁多重感叹号/问号堆叠（如 !?、!!、?? 等）"))

    if any(c in original for c in ("「", "」", "『", "』")):
        proposed = original.replace("「", "｢").replace("」", "｣").replace("『", "｢").replace("』", "｣")
        issues.append(issue_for(path, cue, "阻断", "FULLWIDTH_QUOTES", "严禁全角引号「」『』，必要时请使用半角直角括号｢｣或直接删除", proposed))

    if "…" in original or re.search(r"．{2,}", original):
        proposed = original.replace("…", "...").replace("．", ".")
        issues.append(issue_for(path, cue, "阻断", "FULLWIDTH_ELLIPSIS", "严禁全角省略号「…」，必须使用3个半角点「...」", proposed))
    elif re.search(r"\.{4,}", original):
        proposed = re.sub(r"\.{4,}", "...", original)
        issues.append(issue_for(path, cue, "警告", "OVERLONG_ELLIPSIS", "省略号应为恰好3个半角点「...」", proposed))

    if re.search(r"[０-９]", original):
        issues.append(issue_for(path, cue, "阻断", "FULLWIDTH_DIGIT", "禁止全角阿拉伯数字「０–９」，必须转为半角「0–9」", original.translate(FULLWIDTH_DIGITS)))

    # 假名长音符 vs 破折号/减号混淆检查
    for line_no, line in enumerate(cue.lines, 1):
        for i, char in enumerate(line):
            if char in DASH_LOOKALIKES:
                # 若为行首减号且是双人前缀，跳过
                if char == "-" and i == 0 and len(line) > 1 and not line[1].isspace():
                    continue
                issues.append(issue_for(path, cue, "阻断", "LOOKALIKE_DASH", f"第{line_no}行检测到破折号/横杠 '{char}'(U+{ord(char):04X})，片假名长音必须使用「ー」(U+30FC)"))

    if not is_screen_text(cue.lines) and re.search(r"[（）()]", original):
        issues.append(issue_for(path, cue, "阻断", "PARENTHESIS_IN_DIALOGUE", "字幕正文中严禁使用圆括号（含内心独白），括号仅限画面字人名首次注音"))

    # 空格检查
    if re.search(r"(^|\n)[ \t]+|[ \t]+($|\n)", original):
        issues.append(issue_for(path, cue, "警告", "EDGE_SPACE", "行首或行尾存在多余空格"))
    if re.search(r" {2,}", original):
        issues.append(issue_for(path, cue, "警告", "MULTI_SPACE", "连续半角空格应压缩为一个"))

    # 禁忌词
    for term, rule_id, reason in TABOO_TERMS:
        if term in original:
            issues.append(issue_for(path, cue, "警告", rule_id, reason))

    # 2. 行数与行宽检查 (单行严格 < 12 字符，单条 <= 22 字符，最多 2 行)
    if len(cue.lines) > 2 and not is_screen_text(cue.lines):
        issues.append(issue_for(path, cue, "阻断", "TOO_MANY_LINES", "普通对白最多两行，绝对禁止出现3行字幕"))

    total_displayed_chars = sum(displayed_length(line) for line in cue.lines)

    for line_no, line in enumerate(cue.lines, 1):
        disp_len = displayed_length(line)
        if disp_len > line_char_cap:
            issues.append(issue_for(path, cue, "阻断", "LINE_WIDTH", f"第{line_no}行显示字符数{disp_len}达到或超过12字符（必须严格<12，即最多11字符）"))

    if total_displayed_chars > 22:
        issues.append(issue_for(path, cue, "阻断", "CUE_WIDTH", f"单条总显示字符数{total_displayed_chars}超过上限22字符（2行×11字符）"))

    # 3. 双人对话格式检查 (必须为 -台词，无空格)
    if is_two_speaker(cue.lines):
        for line_no, line in enumerate(cue.lines, 1):
            sline = line.strip()
            if sline.startswith("- "):
                proposed = "\n".join(re.sub(r"^-\s*", "-", l.strip()) for l in cue.lines)
                issues.append(issue_for(path, cue, "阻断", "DUAL_SPEAKER_SPACE", f"双人对白横杠后严禁有空格，必须为「-台词」", proposed))

    # 4. 语义断行受保护单元与孤行
    if len(cue.lines) > 1:
        for line_no, line in enumerate(cue.lines, 1):
            if japanese_count(line) <= 2 and not is_two_speaker(cue.lines):
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

    # 5. CPS 检查 (<=20 达标，20~29 例外，>=30 阻断)
    duration = cue_duration(cue.timecode)
    if duration is not None and duration > 0:
        cps = total_displayed_chars / duration
        if cps >= 29.9999:
            issues.append(issue_for(path, cue, "阻断", "CPS", f"CPS达到{cps:.1f}（>=30），严重超标强制阻断；必须精简措辞或调整时轴"))
        elif cps > 20.0001:
            issues.append(issue_for(path, cue, "警告", "CPS", f"CPS达到{cps:.1f}（20~29），属于偶发例外范围；已记录"))

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


def audit_file(path: Path, line_char_cap: int = 11) -> list[Issue]:
    text = decode_text(path)
    if path.suffix.lower() == ".srt":
        cues, issues = parse_srt(path, text)
    else:
        cues, issues = parse_ass(path, text)
    for cue in cues:
        issues.extend(audit_cue(path, cue, line_char_cap))
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
    parser.add_argument("--report", required=True, help="Output CSV path")
    parser.add_argument("--line-cap", type=int, default=11, help="Max displayed characters per line (default: 11, strictly < 12)")
    parser.add_argument("--fail-on-error", action="store_true", help="Return exit code 2 when blockers exist")
    args = parser.parse_args(argv)

    files = discover_inputs(args.inputs)
    if not files:
        parser.error("No SRT/ASS files found")

    issues: list[Issue] = []
    for path in files:
        try:
            issues.extend(audit_file(path, args.line_cap))
        except Exception as exc:
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
