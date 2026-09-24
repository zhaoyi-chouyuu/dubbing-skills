---
name: style-bert-vits2-tts
description: "Use a local Style-Bert-VITS2 FastAPI service to synthesize configurable speech to WAV, starting the WSL service automatically when needed."
---

# Style-Bert-VITS2 TTS

This skill provides a CLI client that:

1. Checks server health (`/info`)
2. Starts the server in WSL if it is not running
3. Calls `/voice` to generate a WAV

## Defaults

- API: `http://127.0.0.1:5000`
- WSL distro: `Ubuntu`
- WSL python: `/home/zhaoyi/miniconda/envs/stable-ai/bin/python`
- Model root (WSL): `/mnt/e/AI_Models/StyleBertVITS2`
- Default output (Windows): `E:\AI_Models\StyleBertVITS2\outputs\...wav`

## Examples

Each PowerShell tool call is a fresh session, so include both variable lines in every call. Use this Python (it has `requests`); do not use the bare `python` (Python 3.14) or `python3` (Store launcher).

Health check:

```powershell
$PYTHON = 'E:\AI_Models\envs\windows\speaker-evidence312\Scripts\python.exe'
$TTS = 'C:\Users\zhao-\.claude\skills\style-bert-vits2-tts\scripts\style_bert_vits2_tts.py'
& $PYTHON $TTS --health
```

Synthesize:

```powershell
& $PYTHON $TTS --text "hello" --speaker-id 0 --style Neutral
```

Specify output path:

```powershell
& $PYTHON $TTS --text "hello" --out "E:\AI_Models\StyleBertVITS2\outputs\test.wav"
```
