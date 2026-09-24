from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Config:
    api_base_url: str
    wsl_distro: str
    python_path: str
    model_root: str


def _lazy_requests():
    import requests  # type: ignore

    return requests


def is_server_running(api_base_url: str) -> bool:
    requests = _lazy_requests()
    try:
        resp = requests.get(f"{api_base_url}/info", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def start_server(cfg: Config) -> bool:
    print("[*] Starting StyleBertVITS2 server in WSL...")
    cmd = f'wsl -d {cfg.wsl_distro} sh -c "cd {cfg.model_root} && {cfg.python_path} server_fastapi.py"'
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(30):
        if is_server_running(cfg.api_base_url):
            print("[+] Server started.")
            return True
        time.sleep(2)
    return False


def synthesize(
    cfg: Config,
    *,
    text: str,
    speaker_id: int,
    style: str,
    save_path: Optional[str],
) -> dict:
    requests = _lazy_requests()

    if not is_server_running(cfg.api_base_url):
        if not start_server(cfg):
            return {"error": "Failed to start server. Check WSL env and model_root."}

    params = {"text": text, "model_id": speaker_id, "style": style, "encoding": "utf-8"}
    try:
        resp = requests.get(f"{cfg.api_base_url}/voice", params=params, timeout=120)
        if resp.status_code != 200:
            return {"error": f"TTS failed: {resp.status_code} {resp.text}"}

        if not save_path:
            save_path = f"E:/AI_Models/StyleBertVITS2/outputs/output_{int(time.time())}.wav"

        out_dir = os.path.dirname(save_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return {"success": True, "path": save_path}
    except Exception as e:
        return {"error": str(e)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="style_bert_vits2_tts", description="Style-Bert-VITS2 TTS client (auto-starts WSL server).")
    p.add_argument("--api-base-url", default="http://127.0.0.1:5000")
    p.add_argument("--wsl-distro", default="Ubuntu")
    p.add_argument("--python-path", default="/home/zhaoyi/miniconda/envs/stable-ai/bin/python")
    p.add_argument("--model-root", default="/mnt/e/AI_Models/StyleBertVITS2")

    p.add_argument("--health", action="store_true", help="Only check /info health and exit.")
    p.add_argument("--start-server", action="store_true", help="Start server if not running, then exit.")

    p.add_argument("--text", help="Text to synthesize.")
    p.add_argument("--speaker-id", type=int, default=0)
    p.add_argument("--style", default="Neutral")
    p.add_argument("--out", dest="out_path", help="Output wav path (Windows path is fine).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config(
        api_base_url=args.api_base_url,
        wsl_distro=args.wsl_distro,
        python_path=args.python_path,
        model_root=args.model_root,
    )

    if args.health:
        ok = is_server_running(cfg.api_base_url)
        print(json.dumps({"ok": ok}, ensure_ascii=False))
        return 0 if ok else 2

    if args.start_server:
        if is_server_running(cfg.api_base_url):
            print(json.dumps({"ok": True, "already_running": True}, ensure_ascii=False))
            return 0
        ok = start_server(cfg)
        print(json.dumps({"ok": ok}, ensure_ascii=False))
        return 0 if ok else 2

    if not args.text:
        print("Missing --text (or use --health / --start-server).", file=sys.stderr)
        return 2

    result = synthesize(
        cfg,
        text=args.text,
        speaker_id=args.speaker_id,
        style=args.style,
        save_path=args.out_path,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())

