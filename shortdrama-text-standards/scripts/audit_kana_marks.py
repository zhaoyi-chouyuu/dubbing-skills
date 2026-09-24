import argparse
import io
import json
from pathlib import Path
import re
import sys
import unicodedata

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation):
        pass

LOOKALIKES = set('―—–−－ｰ-')


def inspect(text):
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for i, char in enumerate(line):
            if char not in LOOKALIKES:
                continue
            if char == '-' and i == 0 and len(line) > 1 and not line[1].isspace():
                continue  # legitimate speaker-prefix candidate
            hits.append({'line': line_no, 'column': i + 1, 'character': char,
                         'codepoint': f'U+{ord(char):04X}',
                         'unicode_name': unicodedata.name(char), 'text': line,
                         'decision': 'manual_context_review_required'})
    return hits

def scan(path):
    data = path.read_bytes()
    text = data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('utf-8-sig')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    hits = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit() or '-->' not in lines[1]:
            raise ValueError(f'Invalid SRT block in {path}: {block[:80]!r}')
        for hit in inspect('\n'.join(lines[2:])):
            hits.append({'file': str(path), 'cue': int(lines[0]), 'timecode': lines[1], **hit})
    return hits

def test():
    import tempfile
    cases = [('終末世界サイコ―!', 1), ('終末世界サイコー!', 0),
             ('コーヒー', 0), ('-先に行って\n-すぐ追いつく', 0),
             ('サイコ-', 1), ('サイコｰ', 1), ('サイコ—', 1),
             ('サイコ−', 1), ('サイコ－', 1), ('待って―', 1),
             ('- 台詞', 1), ('A-B', 1)]
    for text, count in cases:
        assert len(inspect(text)) == count, text
    fixture = '25\r\n00:01:11,519 --> 00:01:14,239\r\n並ばなくていいとか\r終末世界サイコ―!\r\n'
    with tempfile.TemporaryDirectory() as temp:
        p = Path(temp) / 'test.srt'
        for encoding in ('utf-8-sig', 'utf-16'):
            p.write_bytes(fixture.encode(encoding))
            result = scan(p)
            assert len(result) == 1 and result[0]['cue'] == 25
            assert result[0]['codepoint'] == 'U+2015'
    print(json.dumps({'text_cases_passed': len(cases), 'encoding_cases_passed': 2}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='*', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        test()
    else:
        files = sorted(set(p for arg in args.paths for p in (arg.rglob('*.srt') if arg.is_dir() else [arg])))
        if not files:
            parser.error('No SRT files supplied; an empty scan is not a pass')
        hits = [hit for p in files for hit in scan(p)]
        print(json.dumps({'files_scanned': len(files), 'review_required': len(hits), 'hits': hits}, ensure_ascii=False, indent=2))
        raise SystemExit(1 if hits else 0)
