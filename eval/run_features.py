"""Run signal checks and speech recognition on every item of the eval set; cache results as JSON lines.

Usage: python run_features.py EVALSET_DIR OUT_JSONL [model_size] [limit]
"""
import csv, json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prototype"))
from qc.signal import load_audio, analyze_signal  # noqa: E402
from qc.asr import transcribe  # noqa: E402


def main(root, out, model_size="small", limit=None):
    rows = list(csv.DictReader(open(os.path.join(root, "manifest.csv"), encoding="utf-8")))
    if limit:
        rows = rows[: int(limit)]
    done = set()
    if os.path.exists(out):
        done = {json.loads(l)["id"] for l in open(out, encoding="utf-8")}
    t0 = time.time()
    with open(out, "a", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            if r["id"] in done:
                continue
            x = load_audio(os.path.join(root, r["path"]))
            sig = analyze_signal(x)
            asr = transcribe(x, r["prompt"], r["lang"], model_size)
            f.write(json.dumps({**r, "signal": sig.as_dict(), "asr": asr.as_dict()}, ensure_ascii=False) + "\n")
            f.flush()
            if i % 20 == 0:
                print(i, r["id"], f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3], *(sys.argv[3:4] or ["small"]), *(sys.argv[4:5] or [None]))
