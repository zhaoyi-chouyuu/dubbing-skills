from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _default_ai_models_root() -> str:
    return os.environ.get("AI_MODELS_ROOT", "/mnt/e/AI_Models")


def _default_audio_sep_bin(ai_models_root: str) -> str:
    return os.environ.get(
        "AUDIO_SEP_BIN",
        f"{ai_models_root}/miniconda_envs/cosyvoice310/bin/audio-separator",
    )


def _run_cmd(cmd: str) -> None:
    print(f"[*] Executing: {cmd}")
    result = subprocess.run(cmd, shell=True, executable="/bin/bash")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}")


def _ensemble_average_wav(file1: Path, file2: Path, output_file: Path) -> None:
    # Lazy import so `--help` works even if deps aren't installed in the current python.
    import soundfile as sf

    print(f"[*] Ensembling:\n  1: {file1.name}\n  2: {file2.name}")
    a1, sr1 = sf.read(file1)
    a2, sr2 = sf.read(file2)
    if sr1 != sr2:
        raise RuntimeError(f"Sample rate mismatch: {sr1} vs {sr2}")

    min_len = min(a1.shape[0], a2.shape[0])
    ensembled = (a1[:min_len] + a2[:min_len]) / 2.0

    output_file.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_file, ensembled, sr1, subtype="PCM_16")
    print("[+] Ensemble saved successfully!")


def main(argv: list[str] | None = None) -> int:
    ai_models_root = _default_ai_models_root()

    parser = argparse.ArgumentParser(description="Automated Dual-Model Vocal Separation & Ensemble (WSL paths)")
    parser.add_argument("-i", "--input", required=True, help="Path to mixed audio (WSL path)")
    parser.add_argument("-o", "--outdir", required=True, help="Output directory (WSL path)")
    parser.add_argument(
        "--audio-sep-bin",
        default=_default_audio_sep_bin(ai_models_root),
        help="WSL path to audio-separator binary",
    )
    parser.add_argument(
        "--model-dir-bs",
        default=os.environ.get("MODEL_DIR_BS", f"{ai_models_root}/UVR/models/roformer/vocal_317"),
        help="WSL directory containing BS-Roformer model",
    )
    parser.add_argument(
        "--model-name-bs",
        default=os.environ.get("MODEL_NAME_BS", "model_bs_roformer_ep_317_sdr_12.9755.ckpt"),
        help="BS-Roformer model filename",
    )
    parser.add_argument(
        "--model-dir-mel",
        default=os.environ.get("MODEL_DIR_MEL", f"{ai_models_root}/UVR/models/roformer/vocal_melband"),
        help="WSL directory containing MelBand Roformer model",
    )
    parser.add_argument(
        "--model-name-mel",
        default=os.environ.get("MODEL_NAME_MEL", "vocals_mel_band_roformer.ckpt"),
        help="MelBand Roformer model filename",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    audio_sep_bin = Path(args.audio_sep_bin)
    model_bs = Path(args.model_dir_bs) / args.model_name_bs
    model_mel = Path(args.model_dir_mel) / args.model_name_mel

    missing: list[str] = []
    if not audio_sep_bin.exists():
        missing.append(f"audio-separator: {audio_sep_bin}")
    if not model_bs.exists():
        missing.append(f"BS model: {model_bs}")
    if not model_mel.exists():
        missing.append(f"Mel model: {model_mel}")
    if missing:
        print("[-] Missing required files:")
        for m in missing:
            print(f"    - {m}")
        print("[-] Fix paths via CLI flags or environment variables.")
        return 2

    print("\n=======================================================")
    print("  Ultimate Vocal Separation Pipeline")
    print(f"  Target: {input_path.name}")
    print("=======================================================\n")

    try:
        # 1) BS-Roformer
        print(">>> [Phase 1/3] Extracting via BS-Roformer <<<")
        bs_cmd = (
            f"'{audio_sep_bin}' '{input_path}' "
            f"--model_file_dir '{Path(args.model_dir_bs)}' "
            f"--model_filename '{args.model_name_bs}' "
            f"--output_dir '{outdir}' --output_format wav"
        )
        _run_cmd(bs_cmd)

        # 2) MelBand Roformer
        print("\n>>> [Phase 2/3] Extracting via MelBand Roformer <<<")
        mel_cmd = (
            f"'{audio_sep_bin}' '{input_path}' "
            f"--model_file_dir '{Path(args.model_dir_mel)}' "
            f"--model_filename '{args.model_name_mel}' "
            f"--output_dir '{outdir}' --output_format wav"
        )
        _run_cmd(mel_cmd)

        # 3) Ensemble
        print("\n>>> [Phase 3/3] Ensembling Results <<<")
        bs_model_stem = args.model_name_bs.replace(".ckpt", "")
        mel_model_stem = args.model_name_mel.replace(".ckpt", "")

        bs_candidates = list(outdir.glob(f"{input_path.stem}*{bs_model_stem}*.wav"))
        mel_candidates = list(outdir.glob(f"{input_path.stem}*{mel_model_stem}*.wav"))

        if not bs_candidates:
            print("[-] Could not find BS-Roformer vocal output. Did it fail?")
            return 3
        if not mel_candidates:
            print("[-] Could not find MelBand vocal output. Did it fail?")
            return 3

        final_output = outdir / f"{input_path.stem}_Ultimate_Ensemble.wav"
        _ensemble_average_wav(bs_candidates[0], mel_candidates[0], final_output)

        print("\n=======================================================")
        print("[***] SUCCESS: Ultimate vocal track generated at:")
        print(f"      -> {final_output}")
        print("=======================================================\n")
        return 0
    except Exception as e:
        print(f"[-] Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

