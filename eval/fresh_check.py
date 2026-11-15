"""Apply frozen thresholds to a fresh set nobody tuned on or looked at: other speakers, other sentences
(FLEURS test split), another random seed for the planted defects. No tuning happens here.

Usage: python fresh_check.py FEATURES_JSONL FROZEN_RESULTS_JSON OUT_JSON
"""
import json, sys
from collections import Counter

from evaluate import load, metrics, by_group, batch_sim, verdict
from qc.checks import Thresholds


def main(features, frozen, out):
    items = load(features)
    th = Thresholds(**json.load(open(frozen))["thresholds"])
    res = {"n": len(items), "thresholds": th.as_dict(),
           "full": metrics(items, th, True), "signal_only": metrics(items, th, False),
           "by_variant": by_group(items, th, "variant"), "by_language": by_group(items, th, "lang"),
           "batch_simulation": batch_sim(items, th)}
    res["errors"] = [{"id": it["id"], "variant": it["variant"], "label": it["label"], "reasons": verdict(it, th)[1]}
                     for it in items if (not verdict(it, th)[0]) != (it["label"] == "reject")]
    res["reason_counts"] = dict(Counter(r for it in items for r in verdict(it, th)[1]))
    json.dump(res, open(out, "w"), indent=2, ensure_ascii=False)
    print(json.dumps({k: res[k] for k in ["full", "signal_only", "by_variant", "by_language"]}, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:4])
