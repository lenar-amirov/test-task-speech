"""Human-labelled check set from Mozilla Common Voice (CC0): clips other people listened to and voted on.

validated  (more up-votes than down-votes)  -> label accept
invalidated (more down-votes than up-votes) -> label reject

Takes every invalidated clip and as many validated clips, at most MAX_PER_SPEAKER per speaker in each group so a few
heavy contributors do not dominate. Audio is kept as recorded (no level change): browser recordings, any microphone.
Clips are decoded to 16 kHz mono and stored as FLAC. Also prints the PCM hashes to add to the qc-eval allowlist.

Usage: python cv_set.py CV_LANG_DIR OUT_DIR LANG [SEED]
"""
import csv, hashlib, io, os, random, subprocess, sys
import numpy as np
import soundfile as sf

MAX_PER_SPEAKER = 6


def read(path):
    return list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE))


def pick(rows, n, rnd):
    rnd.shuffle(rows)
    per, out = {}, []
    for r in rows:
        if per.get(r["client_id"], 0) >= MAX_PER_SPEAKER or not r["sentence"].strip():
            continue
        per[r["client_id"]] = per.get(r["client_id"], 0) + 1
        out.append(r)
        if len(out) == n:
            break
    return out


def decode(mp3):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", mp3, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype="<i2")


def pcm_hash(x16):
    # what qc-eval hashes: the int16 PCM of the WAV server_eval.py sends (sf.read int16 of this FLAC)
    return hashlib.sha256(x16.astype("<i2").tobytes()).hexdigest()[:16]


def main(src, out, lang, seed=5):
    rnd = random.Random(int(seed))
    bad = pick(read(os.path.join(src, "invalidated.tsv")), 10 ** 6, rnd)
    good = pick(read(os.path.join(src, "validated.tsv")), len(bad), rnd)
    os.makedirs(os.path.join(out, lang), exist_ok=True)
    manifest, hashes = [], []
    for label, rows in (("reject", bad), ("accept", good)):
        for r in rows:
            x = decode(os.path.join(src, "clips", r["path"]))
            if len(x) < 1600:
                continue
            name = r["path"].replace(".mp3", ".flac")
            sf.write(os.path.join(out, lang, name), x, 16000, subtype="PCM_16")
            back, _ = sf.read(os.path.join(out, lang, name), dtype="int16")
            hashes.append(pcm_hash(back))
            manifest.append({"id": name, "lang": lang, "path": f"{lang}/{name}", "prompt": r["sentence"].strip(),
                             "variant": "cv_validated" if label == "accept" else "cv_invalidated", "label": label,
                             "split": "cv", "up_votes": r["up_votes"], "down_votes": r["down_votes"],
                             "speaker": r["client_id"][:12]})
    with open(os.path.join(out, "manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader(); w.writerows(manifest)
    json_out = os.path.join(out, "hashes.txt")
    open(json_out, "w").write("\n".join(hashes) + "\n")
    print(len(manifest), "clips:", sum(m["label"] == "reject" for m in manifest), "rejected by people,",
          sum(m["label"] == "accept" for m in manifest), "accepted;", len({m["speaker"] for m in manifest}), "speakers")


if __name__ == "__main__":
    main(*sys.argv[1:])
