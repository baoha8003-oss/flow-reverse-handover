"""Measure the median speaking pitch of every packaged voice sample.

The point is calibration, not a new feature: the 30 Gemini samples have a gender
the exe states outright, so they say whether this method can be trusted before
it is pointed at the 67 CapCut/GPT samples, whose gender nobody has written
down anywhere.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
FFMPEG = Path("D:/TOOL_VIDEO/TOOL/ffmpeg.exe")
VOICE_DIR = Path("D:/TOOL_VIDEO/TOOL/voice")
RATE = 16000
FRAME = 1024
HOP = 512
F0_LO, F0_HI = 70, 400


def decode(path: Path) -> np.ndarray:
    proc = subprocess.run(
        [str(FFMPEG), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return np.array([], dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0


def median_f0(x: np.ndarray) -> tuple[float | None, int]:
    """Median F0 over voiced frames, by autocorrelation."""
    if x.size < FRAME * 4:
        return None, 0
    lo, hi = RATE // F0_HI, RATE // F0_LO
    energies, f0s = [], []
    for start in range(0, x.size - FRAME, HOP):
        frame = x[start:start + FRAME]
        energy = float(np.sqrt(np.mean(frame ** 2)))
        if energy < 0.01:
            continue
        frame = frame - frame.mean()
        corr = np.correlate(frame, frame, mode="full")[FRAME - 1:]
        if corr[0] <= 0:
            continue
        corr = corr / corr[0]
        window = corr[lo:hi]
        if window.size == 0:
            continue
        peak = int(np.argmax(window))
        # A weak peak means the frame is noise or an unvoiced consonant.
        if window[peak] < 0.35:
            continue
        energies.append(energy)
        f0s.append(RATE / (lo + peak))
    if len(f0s) < 10:
        return None, len(f0s)
    return float(np.median(f0s)), len(f0s)


def scan(directory: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".wav", ".mp3"):
            continue
        f0, voiced = median_f0(decode(path))
        out[path.stem] = {"f0": round(f0, 1) if f0 else None, "voiced": voiced}
        print(f"  {path.stem:<28} f0={f0 and round(f0)} frames={voiced}", flush=True)
    return out


result = {}
for label, directory in (
    ("gemini", VOICE_DIR),
    ("gpt", VOICE_DIR / "GPT_VOICE"),
    ("capcut_vi", VOICE_DIR / "VOICE_CAPCUT"),
    ("capcut_en", VOICE_DIR / "VOICE_CAPCUT_ENGLISH"),
):
    print(f"=== {label} ===", flush=True)
    result[label] = scan(directory)

(HERE / "voice-pitch.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print("written voice-pitch.json", file=sys.stderr)
