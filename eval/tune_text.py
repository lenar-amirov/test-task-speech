"""Set the per-language text limit (max_cer) on the deployed Whisper large-v3, from the dev-set rows only.

Rule, fixed before looking at the fresh and hard sets: for each language, take the lowest limit on a 0.05 grid
that wrongly rejects at most 2% of that language's good dev recordings for their text (signal checks unchanged).
A lower limit catches more partial and wrong readings; the 2% cap keeps the false-reject target (<=5%) with
room for the signal checks' own false rejects.

Usage: python tune_text.py results/dev_rows.jsonl results/text_limits.json
"""
import json, math, sys

GRID = [round(0.05 * i, 2) for i in range(2, 13)]  # 0.10 .. 0.60
CAP = 0.02
TEXT = {"does not match the prompt", "wrong language"}


def main(rows_path, out_path):
    rows = [json.loads(l) for l in open(rows_path, encoding="utf-8")]
    out = {}
    for lang in sorted({r["lang"] for r in rows}):
        rs = [r for r in rows if r["lang"] == lang and r["cer"] is not None]
        sig_ok = lambda r: not (set(r["reasons"]) - TEXT)         # passes every signal check
        good = [r for r in rs if r["label"] == "accept"]
        bad = [r for r in rs if r["label"] == "reject"]
        table = []
        for lim in GRID:
            fr = sum(sig_ok(r) and r["cer"] > lim for r in good)
            caught = sum((not sig_ok(r)) or r["cer"] > lim for r in bad)
            skipped = [r for r in bad if r["variant"] == "skipped_words"]
            table.append({"limit": lim, "good_text_rejects": fr, "good": len(good), "bad_caught": caught, "bad": len(bad),
                          "skipped_caught": sum(r["cer"] > lim for r in skipped), "skipped": len(skipped)})
        allowed = math.floor(CAP * len(good))
        pick = next(t for t in table if t["good_text_rejects"] <= allowed)
        good_cers = sorted(r["cer"] for r in good if sig_ok(r))
        out[lang] = {"max_cer": pick["limit"], "allowed_good_text_rejects": allowed, "grid": table,
                     "good_cer_p50": good_cers[len(good_cers) // 2], "good_cer_max": good_cers[-1]}
        print(lang, "->", pick, "| good CER median", out[lang]["good_cer_p50"], "max", out[lang]["good_cer_max"])
    json.dump(out, open(out_path, "w"), indent=2)


if __name__ == "__main__":
    main(*sys.argv[1:3])
