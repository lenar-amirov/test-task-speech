"""Measure the deployed pipeline: send every eval recording to the qc-eval function (the same signal checks,
Groq Whisper large-v3 and rules as the live qc-check) and score the verdicts against the planted labels.

Runs where Supabase is reachable (GitHub Actions: .github/workflows/qc-eval.yml). Resumable: rows already in
OUT_JSONL are skipped. Frozen thresholds; nothing is tuned here.

Usage: python server_eval.py DATA_DIR OUT_JSONL SUMMARY_JSON
"""
import csv, io, json, math, os, sys, time
import soundfile as sf
import requests

URL = "https://cpsdihiwdbaccxoowzht.supabase.co/functions/v1/qc-eval"
KEY = os.environ.get("SUPABASE_ANON_KEY", "")   # the public key, same one the demo page ships
PACE_S = float(os.environ.get("PACE_S", "4"))
SPLITS = os.environ.get("SPLITS", "fresh,hard").split(",")   # dev = the tuning set (dev_set.py)


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0, c - h), 4), round(min(1, c + h), 4)]


def wav_bytes(path):
    x, sr = sf.read(path, dtype="int16")
    b = io.BytesIO()
    sf.write(b, x, sr, format="WAV", subtype="PCM_16")
    return b.getvalue()


def check(item, path):
    for attempt in range(8):
        r = requests.post(URL, headers={"Authorization": f"Bearer {KEY}", "apikey": KEY},
                          data={"lang": item["lang"], "prompt": item["prompt"]},
                          files={"audio": ("rec.wav", wav_bytes(path), "audio/wav")}, timeout=120)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 403 and attempt < 2:   # a warm function may still hold the old allowlist
            time.sleep(30); continue
        if r.status_code in (429, 500, 502, 503, 504, 546):
            wait = min(120, 10 * 2 ** attempt)
            print(f"  {item['id']}: {r.status_code} {r.text[:120]}; retry in {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"{item['id']}: {r.status_code} {r.text[:300]}")
    raise RuntimeError(f"{item['id']}: gave up after retries")


def fixed_by_trim(r):
    """Planted edge silence (5-8 s before or after the speech) is cut off the delivered file, not rejected: an accepted
    one ships as a good recording, so it counts as handled."""
    return r["label"] == "reject" and r["variant"] == "long_silence" and r["accept"]


def handled(r):
    return (not r["accept"]) or fixed_by_trim(r)


def summarize(rows):
    out = {}
    for split in sorted({r["split_set"] for r in rows}):
        rs = [r for r in rows if r["split_set"] == split]
        bad = [r for r in rs if r["label"] == "reject"]
        good = [r for r in rs if r["label"] == "accept"]
        caught = sum(handled(r) for r in bad)
        wrong = sum(not r["accept"] for r in good)
        s = {"n": len(rs), "bad": len(bad), "caught": caught, "rejected": sum(not r["accept"] for r in bad),
             "fixed_by_trim": sum(fixed_by_trim(r) for r in bad), "recall": round(caught / len(bad), 4) if bad else None,
             "recall_ci95": wilson(caught, len(bad)),
             "good": len(good), "wrongly_rejected": wrong,
             "false_reject_rate": round(wrong / len(good), 4) if good else None,
             "false_reject_ci95": wilson(wrong, len(good)),
             "borderline_flagged": sum(bool(r.get("borderline")) for r in rs)}
        by = {}
        for key in ("variant", "lang"):
            g = {}
            for r in rs:
                k = r[key]
                g.setdefault(k, {"n": 0, "right": 0})
                g[k]["n"] += 1
                g[k]["right"] += int(handled(r) == (r["label"] == "reject"))
            by[key] = g
        s["by_variant"], s["by_language"] = by["variant"], by["lang"]
        s["errors"] = [{"id": r["id"], "variant": r["variant"], "label": r["label"], "reasons": r["reasons"],
                        "cer": r["cer"], "detected_lang": r["detected_lang"], "transcript": r["transcript"][:160]}
                       for r in rs if handled(r) != (r["label"] == "reject")]
        out[split] = s
    return out


def main(data, out_jsonl, summary):
    done = {}
    if os.path.exists(out_jsonl):
        for line in open(out_jsonl, encoding="utf-8"):
            r = json.loads(line); done[(r["split_set"], r["id"])] = r
    with open(out_jsonl, "a", encoding="utf-8") as f:
        for split in SPLITS:
            items = list(csv.DictReader(open(os.path.join(data, split, "manifest.csv"), encoding="utf-8")))
            for i, it in enumerate(items):
                if (split, it["id"]) in done:
                    continue
                t = time.time()
                v = check(it, os.path.join(data, split, it["path"]))
                row = {"split_set": split, **{k: it[k] for k in ("id", "lang", "variant", "label")}, "accept": v["accept"],
                       "reasons": v["reasons"], "cer": v["cer"], "borderline": v.get("borderline"),
                       "detected_lang": v["detected_lang"], "transcript": v["transcript"], "signal": v["signal"],
                       "asr_model": v["asr_model"]}
                f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush()
                done[(split, it["id"])] = row
                print(f"{split} {i + 1}/{len(items)} {it['id']} {it['label']} -> {'accept' if v['accept'] else 'reject'} {v['reasons']}", flush=True)
                time.sleep(max(0, PACE_S - (time.time() - t)))
    s = summarize(list(done.values()))
    s["edge_silence"] = "trimmed, not rejected: only a pause inside the recording longer than max_gap_s rejects; caught = rejected + fixed_by_trim"
    s["thresholds"] = "signal limits frozen (eval/results/results.json), text limits re-tuned on the dev set and frozen (eval/results/text_limits.json); server copy in app/supabase/functions/qc-check/qc.ts"
    json.dump(s, open(summary, "w"), indent=2, ensure_ascii=False)
    print(json.dumps({k: {kk: v[kk] for kk in ("n", "caught", "bad", "recall", "recall_ci95", "wrongly_rejected", "good",
                                                  "false_reject_rate", "false_reject_ci95")} for k, v in s.items() if isinstance(v, dict)}, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:4])
