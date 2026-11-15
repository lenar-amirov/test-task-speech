"""Recompute signal features on a rebuilt eval set and reuse recognition results where only the gain changed.

Whisper normalises the log-mel spectrum, so a gain change barely moves its output; check with --verify N.
Usage: python reuse_asr.py OLD_FEATURES EVALSET_DIR OUT_JSONL [--verify N]
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prototype"))
from qc.signal import load_audio, analyze_signal  # noqa: E402


def main(old, root, out, verify=0):
    items = [json.loads(l) for l in open(old, encoding="utf-8")]
    with open(out, "w", encoding="utf-8") as f:
        for it in items:
            it["signal"] = analyze_signal(load_audio(os.path.join(root, it["path"]))).as_dict()
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    if verify:
        from qc.asr import transcribe
        picks = [it for it in items if it["lang"] == "kk"][:verify // 2] + [it for it in items if it["variant"] == "too_quiet"][:verify - verify // 2]
        for it in picks:
            new = transcribe(load_audio(os.path.join(root, it["path"])), it["prompt"], it["lang"]).as_dict()
            print(it["id"], it["variant"], "cer", round(it["asr"]["cer"], 3), "->", round(new["cer"], 3),
                  "| p(lang)", round(it["asr"]["lang_prob_expected"], 3), "->", round(new["lang_prob_expected"], 3))


if __name__ == "__main__":
    a = sys.argv[1:]
    v = int(a[a.index("--verify") + 1]) if "--verify" in a else 0
    main(a[0], a[1], a[2], v)
