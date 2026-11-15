"""Command line for the DataTasks auto-QC prototype.

Check one recording:
  python qc_cli.py check rec.wav --prompt "Texto que se leyó" --lang es
Check a batch and decide ship / hold against the buyer's 10% rule:
  python qc_cli.py batch manifest.csv   # columns: path,prompt,lang
"""
import argparse, csv, json, math, os, sys

sys.path.insert(0, os.path.dirname(__file__))
from qc.signal import load_audio, analyze_signal  # noqa: E402
from qc.checks import Thresholds, check_recording  # noqa: E402

TUNED = os.path.join(os.path.dirname(__file__), "..", "eval", "results", "results.json")


def thresholds():
    if os.path.exists(TUNED):
        return Thresholds(**json.load(open(TUNED))["thresholds"])
    return Thresholds()


def run_one(path, prompt, lang, th, use_asr=True):
    x = load_audio(path)
    sig = analyze_signal(x)
    asr = None
    if use_asr:
        from qc.asr import transcribe
        asr = transcribe(x, prompt, lang)
    ok, reasons, usable = check_recording(sig, prompt, lang, asr, th)
    return {"file": path, "verdict": "accept" if ok else "re-record", "reasons": reasons,
            "usable_seconds": round(usable, 2), "signal": sig.as_dict(), "asr": asr.as_dict() if asr else None}


def wilson_upper(k, n, z=1.64):
    if n == 0:
        return 1.0
    p = k / n
    return (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("path"); c.add_argument("--prompt", required=True); c.add_argument("--lang", required=True)
    c.add_argument("--no-asr", action="store_true")
    b = sub.add_parser("batch"); b.add_argument("manifest"); b.add_argument("--residual", type=float, default=0.03,
                                                                            help="expected share of rejects our check misses")
    a = ap.parse_args()
    th = thresholds()
    if a.cmd == "check":
        print(json.dumps(run_one(a.path, a.prompt, a.lang, th, not a.no_asr), ensure_ascii=False, indent=2))
        return
    rows = list(csv.DictReader(open(a.manifest, encoding="utf-8")))
    base = os.path.dirname(os.path.abspath(a.manifest))
    out = [run_one(os.path.join(base, r["path"]), r["prompt"], r["lang"], th) for r in rows]
    shipped = [o for o in out if o["verdict"] == "accept"]
    # expected buyer rejects among shipped files, with a one-sided 95% upper bound
    k = round(a.residual * len(shipped))
    upper = wilson_upper(k, len(shipped))
    print(json.dumps({"files": len(out), "accepted": len(shipped), "re-record": len(out) - len(shipped),
                      "usable_seconds": round(sum(o["usable_seconds"] for o in out), 1),
                      "expected_buyer_reject_upper": round(upper, 3),
                      "release": "ship" if upper < 0.07 else "hold"}, indent=2))


if __name__ == "__main__":
    main()
