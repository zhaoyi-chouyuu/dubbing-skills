"""Offline file-structure/package check; never loads ASR or processes media."""
import argparse
import importlib.metadata as metadata
import json
import math
import os
from pathlib import Path
import platform
import struct


def inspect_weights(path):
    """Check readable safetensors header, payload bounds and declared tensor sizes."""
    path = Path(path)
    with path.open('rb') as f:
        first = f.read(8)
        if len(first) != 8:
            raise ValueError('missing safetensors header length')
        size = struct.unpack('<Q', first)[0]
        if not 2 <= size <= 64 * 1024 * 1024:
            raise ValueError('invalid safetensors header length')
        raw = f.read(size)
        if len(raw) != size:
            raise ValueError('truncated header')
        header = json.loads(raw)
        total = f.seek(0, 2)
        if total <= 8 + size:
            raise ValueError('missing tensor payload')
        f.seek(-1, 2)
        if len(f.read(1)) != 1:
            raise ValueError('payload is not readable')
    widths = {'F64': 8, 'F32': 4, 'F16': 2, 'BF16': 2, 'I64': 8,
              'I32': 4, 'I16': 2, 'I8': 1, 'U8': 1, 'BOOL': 1}
    tensors = {k: v for k, v in header.items() if k != '__metadata__'}
    if not tensors:
        raise ValueError('no tensors')
    intervals = []
    for name, tensor in tensors.items():
        shape = tensor['shape']
        if any(type(x) is not int or x < 0 for x in shape):
            raise ValueError('invalid tensor shape: ' + name)
        start, end = tensor['data_offsets']
        if not 0 <= start <= end <= total - 8 - size:
            raise ValueError('truncated/out-of-range tensor: ' + name)
        width = widths.get(tensor['dtype'])
        if width and end - start != math.prod(shape) * width:
            raise ValueError('tensor size disagrees with dtype/shape: ' + name)
        if end > start:
            intervals.append((start, end))
    intervals.sort()
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise ValueError('overlapping tensor payloads')
    return {'path': str(path), 'bytes': total, 'tensors': len(tensors),
            'weights_structure_readable': True, 'full_hash_verified': False}


def inspect_model(folder, repo):
    folder = Path(folder)
    result = {'model_id': repo, 'path': str(folder), 'model_loaded': False,
              'revision': None, 'errors': []}
    try:
        result['config'] = json.loads((folder / 'config.json').read_text(encoding='utf-8'))['model_type']
        if not any((folder / n).is_file() for n in ['preprocessor_config.json', 'processor_config.json']):
            raise ValueError('missing processor/preprocessor configuration')
        if not (folder / 'tokenizer_config.json').is_file():
            raise ValueError('missing tokenizer configuration')
        index = folder / 'model.safetensors.index.json'
        if index.is_file():
            names = sorted(set(json.loads(index.read_text(encoding='utf-8'))['weight_map'].values()))
            for name in names:
                if Path(name).name != name:
                    raise ValueError('unexpected shard path')
        else:
            names = ['model.safetensors']
        result['weights'] = [inspect_weights(folder / n) for n in names]
        cached = folder / '.cache/huggingface/download/config.json.metadata'
        if cached.is_file():
            result['revision'] = cached.read_text(encoding='utf-8').splitlines()[0]
        elif folder.parent.name == 'snapshots':
            result['revision'] = folder.name
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        result['errors'].append(str(exc))
    return result


TURBO_REPO = 'mobiuslabsgmbh/faster-whisper-large-v3-turbo'


def default_turbo_path():
    hub = (Path('E:/AI_Models/huggingface/hub') if os.name == 'nt'
           else Path.home() / '.cache/huggingface/hub')
    repo = hub / ('models--' + TURBO_REPO.replace('/', '--'))
    # Resolve only local cache metadata; never fall back to a Hub download.
    try:
        revision = (repo / 'refs/main').read_text(encoding='utf-8').strip()
        if not revision or any(c not in '0123456789abcdef' for c in revision):
            raise ValueError('invalid cached revision')
    except (OSError, ValueError):
        return repo / 'snapshots' / 'missing-local-revision'
    return repo / 'snapshots' / revision


def inspect_ct2_model(folder, repo):
    folder = Path(folder)
    result = {'model_id': repo, 'path': str(folder), 'format': 'ctranslate2',
              'model_loaded': False, 'revision': None, 'errors': []}
    try:
        # CTranslate2 config is not a Transformers config with model_type.
        for name in ['config.json', 'preprocessor_config.json', 'tokenizer.json']:
            data = json.loads((folder / name).read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not data:
                raise ValueError('empty/invalid configuration: ' + name)
        weight = folder / 'model.bin'
        with weight.open('rb') as stream:
            total = stream.seek(0, 2)
            if total < 16:
                raise ValueError('empty/truncated model.bin')
            stream.seek(0)
            if len(stream.read(8)) != 8:
                raise ValueError('unreadable model.bin header')
            stream.seek(-8, 2)
            if len(stream.read(8)) != 8:
                raise ValueError('unreadable model.bin tail')
        result['weights'] = [{'path': str(weight), 'bytes': total,
                              'weights_file_readable': True,
                              'tensor_structure_validated': False,
                              'full_hash_verified': False}]
        if folder.parent.name == 'snapshots':
            result['revision'] = folder.name
    except (OSError, ValueError, TypeError) as exc:
        result['errors'].append(str(exc))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--turbo-model', default=str(default_turbo_path()))
    parser.add_argument('--anime-model', default=str(Path('E:/AI_Models/anime-whisper') if os.name == 'nt' else Path.home() / 'models/anime-whisper'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    packages = {}
    for name in ['torch', 'transformers', 'faster-whisper', 'whisperx', 'ctranslate2', 'numpy']:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    turbo = inspect_ct2_model(args.turbo_model, TURBO_REPO)
    anime = inspect_model(args.anime_model, 'litagin/anime-whisper')
    turbo['role'] = 'primary_transcript_coarse_location'
    anime['role'] = 'text_review_only_no_segmentation_or_timing'
    required = ['torch', 'transformers', 'faster-whisper', 'ctranslate2', 'numpy']
    missing = [n for n in required if packages[n] is None]
    blocked = bool(turbo['errors'] or anime['errors'] or missing)
    result = {'status': 'blocked_dependency' if blocked else 'preflight_only',
              'python': platform.python_version(), 'packages_current_interpreter': packages,
              'missing_packages_current_interpreter': missing, 'asr_a': turbo, 'asr_b': anime,
              'model_families_distinct': False, 'timing_backend': 'V26',
              'media_processed': False, 'models_loaded': False, 'cuda_checked': False,
              'note': 'Other isolated environments require their own check; this does not establish inference readiness.'}
    data = json.dumps(result, ensure_ascii=True, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(data + '\n', encoding='utf-8')
    print(data)
    return 2 if blocked else 0


if __name__ == '__main__':
    raise SystemExit(main())
