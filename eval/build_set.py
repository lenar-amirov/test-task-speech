"""Build the evaluation set from real FLEURS recordings (Spanish LatAm, Kazakh, Urdu).

Each real recording gets one variant: either kept acceptable (clean, mild background talk, a bit quieter)
or given one defect a buyer would reject (wrong sentence, heavy noise, clipping, cut off, wrong language,
long silence, too quiet). All sources are first brought to one level (-26 dBFS). The label is known because we planted the defect.

Usage: python build_set.py FLEURS_DIR OUT_DIR [N_PER_LANG] [SEED]
"""
import csv, hashlib, os, random, sys
import numpy as np
import soundfile as sf

LANGS = {"es_419": "es", "kk_kz": "kk", "ur_pk": "ur"}
VARIANTS = [  # (name, label, weight)
    ("clean", "accept", 35), ("mild_noise", "accept", 10), ("quieter", "accept", 5),
    ("wrong_text", "reject", 10), ("heavy_noise", "reject", 8), ("clipping", "reject", 8),
    ("cut_off", "reject", 8), ("wrong_language", "reject", 8), ("long_silence", "reject", 4), ("too_quiet", "reject", 4),
]
SR = 16000


def read_tsv(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                rows.append({"sid": p[0], "file": p[1], "text": p[2]})
    return rows


def rms(x):
    return float(np.sqrt(np.mean(x ** 2) + 1e-12))


def mix_at_snr(x, noise, snr_db):
    if len(noise) < len(x):
        noise = np.tile(noise, int(np.ceil(len(x) / len(noise))))
    noise = noise[: len(x)]
    gain = rms(x) / (rms(noise) * 10 ** (snr_db / 20))
    y = x + gain * noise
    return y / max(1.0, np.abs(y).max() / 0.95)


def level(x, dbfs=-26.0):
    """Bring a recording to a common level, as a phone's automatic gain does. FLEURS Kazakh is ~25 dB quieter than Spanish."""
    return x * (10 ** (dbfs / 20) / max(rms(x), 1e-6))


def babble(pool, rng, n=4):
    parts = [sf.read(rng.choice(pool), dtype="float32")[0] for _ in range(n)]
    L = max(len(p) for p in parts)
    b = np.zeros(L, dtype="float32")
    for p in parts:
        b[: len(p)] += p
    pink = np.cumsum(rng.standard_normal(L)).astype("float32")
    pink -= np.convolve(pink, np.ones(400) / 400, mode="same")
    return b / rms(b) + 0.5 * pink / rms(pink)


def main(src, out, n_per_lang=100, seed=7):
    rng = np.random.default_rng(seed)
    rnd = random.Random(seed)
    data = {}
    for folder, code in LANGS.items():
        rows = read_tsv(os.path.join(src, folder, "dev.tsv"))
        for r in rows:
            r["path"] = os.path.join(src, folder, "dev", r["file"])
        data[code] = [r for r in rows if os.path.exists(r["path"])]
    names = [v[0] for v in VARIANTS]
    weights = [v[2] for v in VARIANTS]
    labels = {v[0]: v[1] for v in VARIANTS}
    os.makedirs(out, exist_ok=True)
    manifest = []
    for code, rows in data.items():
        rnd.shuffle(rows)
        seen, picked = set(), []
        for r in rows:                      # one recording per sentence where possible
            if r["sid"] not in seen:
                picked.append(r); seen.add(r["sid"])
            if len(picked) == n_per_lang:
                break
        pool_same = [r["path"] for r in rows]
        others = [r for c, rr in data.items() if c != code for r in rr]
        os.makedirs(os.path.join(out, code), exist_ok=True)
        for i, r in enumerate(picked):
            variant = rnd.choices(names, weights)[0]
            x, sr = sf.read(r["path"], dtype="float32")
            x = level(x)
            prompt = r["text"]
            if variant == "mild_noise":
                x = mix_at_snr(x, babble(pool_same, rng), 22)
            elif variant == "quieter":
                x = x * 0.5
            elif variant == "wrong_text":
                prompt = rnd.choice([o["text"] for o in rows if o["sid"] != r["sid"]])
            elif variant == "heavy_noise":
                x = mix_at_snr(x, babble(pool_same, rng), rnd.uniform(-3, 4))
            elif variant == "clipping":
                x = np.clip(x * (1.0 / max(1e-3, np.percentile(np.abs(x), 97))) , -1, 1) * 0.999
            elif variant == "cut_off":
                x = x[: int(len(x) * rnd.uniform(0.25, 0.45))]
            elif variant == "wrong_language":
                o = rnd.choice(others)
                x = level(sf.read(o["path"], dtype="float32")[0])
            elif variant == "long_silence":
                pad = np.zeros(int(SR * rnd.uniform(5, 8)), dtype="float32")
                x = np.concatenate([pad, x]) if rnd.random() < 0.5 else np.concatenate([x, pad])
            elif variant == "too_quiet":
                x = level(x, -56.0)          # 30 dB below normal: speech barely above the phone's noise floor
            name = f"{code}_{i:03d}.wav"
            sf.write(os.path.join(out, code, name), x.astype("float32"), SR)
            split = "dev" if int(hashlib.md5(name.encode()).hexdigest(), 16) % 2 == 0 else "test"
            manifest.append({"id": name, "lang": code, "path": os.path.join(code, name), "prompt": prompt,
                             "variant": variant, "label": labels[variant], "split": split})
    with open(os.path.join(out, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader(); w.writerows(manifest)
    print(len(manifest), "items")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 100, int(sys.argv[4]) if len(sys.argv) > 4 else 7)
