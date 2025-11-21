from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Optional


def _import_backend():
    try:
        from .beats_checkpoint import BeatsCheckpoint  # type: ignore
        return BeatsCheckpoint
    except Exception:
        proj_root = pathlib.Path(__file__).resolve().parent.parent
        if str(proj_root) not in sys.path:
            sys.path.insert(0, str(proj_root))
        from video_tool.beats_checkpoint import BeatsCheckpoint  # type: ignore
        return BeatsCheckpoint


def main(argv: Optional[list[str]] = None) -> int:
    BeatsCheckpoint = _import_backend()
    parser = argparse.ArgumentParser(description="Detect beat checkpoints from audio (Demucs + Librosa)")
    parser.add_argument("audio", help="Audio file path")
    parser.add_argument("--out-dir", dest="out_dir", default=None, help="Output directory for checkpoints (default: <audio_dir>/beats_meta)")
    parser.add_argument("--temp-dir", dest="temp_dir", default=None, help="Temporary directory (default: <audio_dir>/temp)")
    parser.add_argument("--model", dest="model", default="htdemucs", help="Demucs model name")
    parser.add_argument("--device", dest="device", default="gpu", choices=["cpu", "gpu"], help="Preferred device")
    parser.add_argument("--mode", dest="mode", default="default", choices=["default", "fast", "slow", "dynamic"], help="Interval filtering mode")
    parser.add_argument("--min-interval", dest="min_interval", type=float, default=None, help="Custom minimum interval in seconds (overrides mode except dynamic)")

    args = parser.parse_args(argv)

    bc = BeatsCheckpoint(audio_path=args.audio, output_dir=args.out_dir, temp_dir=args.temp_dir, model=args.model, device=args.device, interval_mode=args.mode, min_interval=args.min_interval)
    json_path, timestamps = bc.run()
    print(str(json_path))
    print("checkpoints_count=", len(timestamps))
    if timestamps:
        print("first_seconds=", ", ".join([f"{t:.3f}" for t in timestamps[:10]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())