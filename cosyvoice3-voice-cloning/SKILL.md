---
name: cosyvoice3-voice-cloning
description: "Start the CosyVoice3 voice-cloning and TTS Gradio WebUI under WSL Ubuntu using the local model repository. Use when an interactive CosyVoice3 WebUI is needed."
---

# CosyVoice3 Voice Cloning (WebUI)

This skill starts the Gradio WebUI from:

- Code: `E:\AI_Models\CosyVoice_code` (WSL: `/mnt/e/AI_Models/CosyVoice_code`)
- Default model weights: `E:\AI_Models\Fun-CosyVoice3-0.5B-2512` (WSL: `/mnt/e/AI_Models/Fun-CosyVoice3-0.5B-2512`)

## Run (PowerShell)

```powershell
& 'C:\Users\zhao-\.claude\skills\cosyvoice3-voice-cloning\scripts\start_webui.ps1' -Port 8000
```

Override model dir:

```powershell
& 'C:\Users\zhao-\.claude\skills\cosyvoice3-voice-cloning\scripts\start_webui.ps1' -Port 8000 -ModelDir "E:\AI_Models\Fun-CosyVoice3-0.5B-2512"
```

## Notes

- The WebUI is a long-running server: start it with the PowerShell tool's `run_in_background: true` so it keeps running, then open `http://127.0.0.1:8000` for the user in the built-in browser pane (`preview_start` with that URL).
- This is a UI-start skill: do generation inside the browser UI.
- vLLM is not the default path here (your `cosyvoice310` env currently has Gradio but no `vllm`).
