"""Tune thresholds on the dev half, report on the held-out test half, simulate batches against the 10% rule.

Usage: python evaluate.py FEATURES_JSONL OUT_DIR
"""
import itertools, json, os, sys, random
from collections import Counter, defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prototype"))
from qc.checks import Thresholds, check_recording  # noqa: E402


def load(path):
    items = [json.loads(l) for l in open(path, encoding="utf-8")]
    for it in items:
        it["sig"] = SimpleNamespace(**it["signal"])
        it["asrr"] = SimpleNamespace(**it["asr"])
    return items


def verdict(it, th, use_asr=True):
    ok, reasons, usable = check_recording(it["sig"], it["prompt"], it["lang"], it["asrr"] if use_asr else None, th)
    return ok, reasons, usable


def metrics(items, th, use_asr=True):
    tp = fp = tn = fn = 0
    for it in items:
        ok, _, _ = verdict(it, th, use_asr)
        bad = it["label"] == "reject"
        if bad and not ok: tp += 1
        elif bad and ok: fn += 1
        elif not bad and not ok: fp += 1
        else: tn += 1
    rec = tp / max(1, tp + fn); fpr = fp / max(1, fp + tn); prec = tp / max(1, tp + fp)
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn, "recall": rec, "false_reject_rate": fpr, "precision": prec}


def tune(dev):
    best = None
    grid = itertools.product([8, 10, 12], [0.35, 0.45], [0.2, 0.5], [0.0, 0.02, 0.05], [0.05, 0.1, 0.2],
                             [0.25, 0.35, 0.45], [0.55, 0.7, 0.85], [0.35, 0.45, 0.55])
    for snr, spr, lp_es, lp_kk, lp_ur, cer_es, cer_kk, cer_ur in grid:
        th = Thresholds(min_snr_db=snr, min_speech_ratio=spr,
                        min_lang_prob={"es": lp_es, "kk": lp_kk, "ur": lp_ur},
                        max_cer={"es": cer_es, "kk": cer_kk, "ur": cer_ur})
        m = metrics(dev, th)
        key = (m["false_reject_rate"] <= 0.05, m["recall"], -m["false_reject_rate"])
        if best is None or key > best[0]:
            best = (key, th, m)
    return best[1], best[2]


def by_group(items, th, field):
    out = defaultdict(lambda: [0, 0])
    for it in items:
        ok, _, _ = verdict(it, th)
        correct = (not ok) == (it["label"] == "reject")
        out[it[field]][0] += correct; out[it[field]][1] += 1
    return {k: {"correct": v[0], "n": v[1], "share": round(v[0] / v[1], 3)} for k, v in sorted(out.items())}


def batch_sim(items, th, base_rates=(0.02, 0.05, 0.10, 0.20), n=100, trials=4000, seed=1):
    rnd = random.Random(seed)
    good = [it for it in items if it["label"] == "accept"]
    bad = [it for it in items if it["label"] == "reject"]
    v = {it["id"]: verdict(it, th)[0] for it in items}
    res = {}
    for r in base_rates:
        over_without = over_with = 0; shipped_rej = []
        for _ in range(trials):
            batch = [rnd.choice(bad) if rnd.random() < r else rnd.choice(good) for _ in range(n)]
            rej = sum(it["label"] == "reject" for it in batch)
            over_without += rej / n > 0.10
            shipped = [it for it in batch if v[it["id"]]]
            srej = sum(it["label"] == "reject" for it in shipped) / max(1, len(shipped))
            shipped_rej.append(srej); over_with += srej > 0.10
        res[f"{int(r*100)}%"] = {"batches_over_10pct_without_qc": round(over_without / trials, 3),
                                 "batches_over_10pct_with_qc": round(over_with / trials, 3),
                                 "avg_reject_rate_shipped": round(sum(shipped_rej) / trials, 4)}
    return res


def main(path, out):
    items = load(path)
    dev = [i for i in items if i["split"] == "dev"]; test = [i for i in items if i["split"] == "test"]
    th, dev_m = tune(dev)
    res = {
        "n": {"dev": len(dev), "test": len(test)},
        "thresholds": th.as_dict(),
        "dev": dev_m,
        "test_full": metrics(test, th, True),
        "test_signal_only": metrics(test, th, False),
        "test_by_variant": by_group(test, th, "variant"),
        "test_by_language": by_group(test, th, "lang"),
        "batch_simulation_test": batch_sim(test, th),
    }
    reasons = Counter()
    usable = defaultdict(list)
    for it in test:
        ok, rs, u = verdict(it, th)
        reasons.update(rs)
        if ok: usable[it["lang"]].append(u)
    res["test_reason_counts"] = dict(reasons)
    res["usable_seconds_accepted"] = {k: round(sum(v) / len(v), 2) for k, v in usable.items()}
    os.makedirs(out, exist_ok=True)
    json.dump(res, open(os.path.join(out, "results.json"), "w"), indent=2, ensure_ascii=False)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
