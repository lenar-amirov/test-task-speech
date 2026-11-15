"""Re-score the measured rows after one rule change: silence at the start or end of a recording is now trimmed off the
delivered file instead of rejecting it (qc.ts trimEdges). Nothing else changed: the signal measurements, transcripts and
limits are the ones the live server returned, so the new verdict is the old one without an edge-only "long silence".
A pause inside the recording longer than max_gap_s (3 s) still rejects.

Usage: python rescore_edge_trim.py BEFORE_JSONL OUT_JSONL SUMMARY_JSON
"""
import json, sys
from server_eval import summarize

MAX_GAP_S = 3.0


def rescore(r):
    r = dict(r)
    reasons = list(r["reasons"])
    if "long silence" in reasons and r["signal"]["max_gap_s"] <= MAX_GAP_S:
        reasons.remove("long silence")
        r["edge_silence_trimmed"] = True
    r["reasons"], r["accept"] = reasons, not reasons
    return r


def main(before, out_jsonl, summary):
    rows = [rescore(json.loads(l)) for l in open(before, encoding="utf-8")]
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    s = summarize(rows)
    s["edge_silence"] = "trimmed, not rejected: only a pause inside the recording longer than 3 s rejects; caught = rejected + fixed_by_trim"
    s["source"] = f"re-scored from {before} (live-server rows) by rescore_edge_trim.py"
    s["thresholds"] = "signal limits frozen (eval/results/results.json), text limits re-tuned on the dev set and frozen (eval/results/text_limits.json); server copy in app/supabase/functions/qc-check/qc.ts"
    json.dump(s, open(summary, "w"), indent=2, ensure_ascii=False)
    for k, v in s.items():
        if isinstance(v, dict):
            print(k, {kk: v[kk] for kk in ("n", "bad", "caught", "rejected", "fixed_by_trim", "recall", "recall_ci95", "good",
                                          "wrongly_rejected", "false_reject_rate", "false_reject_ci95")})
            print("   by language", v["by_language"])


if __name__ == "__main__":
    main(*sys.argv[1:4])
