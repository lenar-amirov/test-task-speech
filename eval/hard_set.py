"""Harder defects than the build and fresh sets, checked on frozen thresholds (no tuning).

The fresh set plants obvious defects: a whole other sentence, heavy noise, a cut-off half. Real fraud and
sloppy work are subtler, so this set asks how the frozen rules hold up on three harder cases:

- skipped_words: the reader skips 20-30% of the sentence in the middle (the rest is read correctly)
- kk_read_in_russian: the Kazakh prompt, but the speaker reads the same sentence in Russian
  (FLEURS is parallel: the same sentence id has a Russian reading by another speaker)
- mild_clipping: only 0.2-1% of samples clipped, not the heavy distortion of the fresh set

Every item is a defect (label reject); false rejects stay measured on the fresh set.
Recordings come from the FLEURS test split, skipping the ones the fresh set already used.

Usage: python hard_set.py FLEURS_DIR OUT_DIR FRESH_MANIFEST [SEED]
"""
import csv, os, random, sys
import numpy as np
import soundfile as sf

from build_set import LANGS, SR, audio_dir, level, read_tsv

PLAN = {"es": [("skipped_words", 20), ("mild_clipping", 20)],
        "kk": [("skipped_words", 20), ("mild_clipping", 20), ("kk_read_in_russian", 40)],
        "ur": [("skipped_words", 20), ("mild_clipping", 20)]}


def load(src, folder):
    rows = read_tsv(os.path.join(src, folder, "test.tsv"))
    for r in rows:
        r["path"] = os.path.join(audio_dir(src, folder, "test"), r["file"])
    return [r for r in rows if os.path.exists(r["path"])]


def skip_middle(x, rnd):
    share = rnd.uniform(0.20, 0.30)
    start = int(len(x) * rnd.uniform(0.30, 0.70 - share))
    return np.concatenate([x[:start], x[start + int(len(x) * share):]])


def mild_clip(x, rnd):
    target = rnd.uniform(0.002, 0.01)          # share of all samples that hit the rail
    gain = 1.0 / max(np.quantile(np.abs(x), 1 - target), 1e-4)
    return np.clip(x * gain, -1, 1) * 0.999


def main(src, out, fresh_manifest, seed=23):
    rnd = random.Random(seed)
    used = {r["prompt"] for r in csv.DictReader(open(fresh_manifest, encoding="utf-8"))}
    data = {code: [r for r in load(src, folder) if r["text"] not in used] for folder, code in LANGS.items()}
    ru = {}
    for r in load(src, "ru_ru"):
        ru.setdefault(r["sid"], []).append(r)
    manifest = []
    for code, plan in PLAN.items():
        rows = data[code][:]
        rnd.shuffle(rows)
        seen, pool = set(), []
        for r in rows:
            if r["sid"] not in seen:
                pool.append(r); seen.add(r["sid"])
        os.makedirs(os.path.join(out, code), exist_ok=True)
        i = 0
        for variant, n in plan:
            k = 0
            while k < n:
                r = pool.pop()
                prompt = r["text"]
                if variant == "kk_read_in_russian":
                    if r["sid"] not in ru:
                        continue
                    x = level(sf.read(rnd.choice(ru[r["sid"]])["path"], dtype="float32")[0])
                else:
                    x = level(sf.read(r["path"], dtype="float32")[0])
                    x = skip_middle(x, rnd) if variant == "skipped_words" else mild_clip(x, rnd)
                name = f"hard_{code}_{i:03d}.wav"
                sf.write(os.path.join(out, code, name), x.astype("float32"), SR)
                manifest.append({"id": name, "lang": code, "path": os.path.join(code, name), "prompt": prompt,
                                 "variant": variant, "label": "reject", "split": "hard"})
                i += 1; k += 1
    with open(os.path.join(out, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader(); w.writerows(manifest)
    print(len(manifest), "items")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 23)
