"""Signal-level checks that need no model: length, speech seconds, silence, clipping, loudness, noise.

These are the checks that can run on the contributor's phone before upload.
"""
from dataclasses import dataclass, asdict
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SR = 16000
FRAME = int(0.030 * SR)   # 30 ms
HOP = int(0.010 * SR)     # 10 ms


@dataclass
class SignalReport:
    duration_s: float
    speech_s: float          # "usable audio": seconds that contain voice
    lead_silence_s: float
    trail_silence_s: float
    max_gap_s: float         # longest pause inside the speech
    clip_ratio: float        # share of samples at full scale
    speech_dbfs: float       # loudness of the voiced part
    noise_dbfs: float
    snr_db: float

    def as_dict(self):
        return asdict(self)


def load_audio(path_or_array, sr=None):
    if isinstance(path_or_array, (str, bytes)) or hasattr(path_or_array, "read"):
        x, sr = sf.read(path_or_array, dtype="float32", always_2d=True)
        x = x.mean(axis=1)
    else:
        x = np.asarray(path_or_array, dtype="float32")
    if sr and sr != SR:
        g = gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g).astype("float32")
    return x


def _frame_db(x):
    if len(x) < FRAME:
        x = np.pad(x, (0, FRAME - len(x)))
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    rms = np.sqrt(np.mean(x[idx] ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-12)


def _runs(mask):
    """Start/end indices of True runs."""
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def analyze_signal(x):
    x = np.asarray(x, dtype="float32")
    duration = len(x) / SR
    db = _frame_db(x)
    noise = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    # voice = frames well above the noise floor, and not absurdly quiet
    thr = max(noise + 0.35 * (peak - noise), noise + 6.0, -55.0)
    voiced = db > thr
    # close short gaps (< 200 ms) and drop blips (< 100 ms)
    for s, e in _runs(~voiced):
        if 0 < s and e < len(voiced) and (e - s) < 20:
            voiced[s:e] = True
    for s, e in _runs(voiced):
        if (e - s) < 10:
            voiced[s:e] = False
    runs = _runs(voiced)
    hop_s = HOP / SR
    if runs:
        speech_s = float(voiced.sum() * hop_s)
        lead = runs[0][0] * hop_s
        trail = max(0.0, duration - runs[-1][1] * hop_s - FRAME / SR)
        gaps = [(runs[i + 1][0] - runs[i][1]) * hop_s for i in range(len(runs) - 1)]
        max_gap = float(max(gaps)) if gaps else 0.0
        speech_db = float(10 * np.log10(np.mean(10 ** (db[voiced] / 10))))
    else:
        speech_s, lead, trail, max_gap, speech_db = 0.0, duration, 0.0, 0.0, float(peak)
    unvoiced = db[~voiced]
    noise_db = float(np.percentile(unvoiced, 50)) if len(unvoiced) > 5 else noise
    clip_ratio = float(np.mean(np.abs(x) >= 0.98)) if len(x) else 0.0
    return SignalReport(
        duration_s=round(duration, 3),
        speech_s=round(speech_s, 3),
        lead_silence_s=round(lead, 3),
        trail_silence_s=round(trail, 3),
        max_gap_s=round(max_gap, 3),
        clip_ratio=round(clip_ratio, 5),
        speech_dbfs=round(speech_db, 2),
        noise_dbfs=round(noise_db, 2),
        snr_db=round(speech_db - noise_db, 2),
    )
