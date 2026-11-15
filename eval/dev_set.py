"""Tuning set for the text limits on the deployed Whisper large-v3 (built from the FLEURS dev split only,
so it shares no recordings with the fresh and hard sets, which come from the test split).

- 100 recordings per language with the same planted variants as the fresh set (build_set.py, dev split, seed 7)
- plus 30 per language with 20-30% of the sentence skipped in the middle (hard_set.skip_middle), because the
  text limit is exactly the line between a good reading and a partial one

Usage: python dev_set.py FLEURS_DIR OUT_DIR [SEED]
"""
import csv, os, random, sys
import soundfile as sf

import build_set
from build_set import LANGS, SR, audio_dir, level, read_tsv
from hard_set import skip_middle

N_SKIPPED = 30


def main(src, out, seed=7):
    build_set.main(src, out, 100, seed, "dev")
    man = os.path.join(out, "manifest.csv")
    rows = list(csv.DictReader(open(man, encoding="utf-8")))
    used = {r["prompt"] for r in rows}
    rnd = random.Random(seed + 1000)
    for folder, code in LANGS.items():
        pool = [r for r in read_tsv(os.path.join(src, folder, "dev.tsv")) if r["text"] not in used]
        rnd.shuffle(pool)
        seen, picked = set(), []
        for r in pool:
            p = os.path.join(audio_dir(src, folder, "dev"), r["file"])
            if r["sid"] in seen or not os.path.exists(p):
                continue
            seen.add(r["sid"]); picked.append((r, p))
            if len(picked) == N_SKIPPED:
                break
        for i, (r, p) in enumerate(picked):
            x = skip_middle(level(sf.read(p, dtype="float32")[0]), rnd)
            name = f"{code}_skip_{i:03d}.wav"
            sf.write(os.path.join(out, code, name), x.astype("float32"), SR)
            rows.append({"id": name, "lang": code, "path": os.path.join(code, name), "prompt": r["text"],
                         "variant": "skipped_words", "label": "reject", "split": "dev"})
    for r in rows:
        r["split"] = "dev"
    with open(man, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(len(rows), "items")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 7)
