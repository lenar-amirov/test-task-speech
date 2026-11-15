"""Build the single-file demo page: phone-side checks live in the browser, plus measured results.

Usage: python build_page.py FEATURES_JSONL EVALSET_DIR RESULTS_JSON FRESH_JSON OUT_HTML
(samples come from FEATURES_JSONL / EVALSET_DIR: pass the fresh set so the page shows recordings tuning never saw)
"""
import base64, io, json, os, sys
import numpy as np
import soundfile as sf

HERE = os.path.dirname(__file__)
# (case, language) shown on the page; each is the first recording of that kind where the verdict matched the label.
# Misses are counted in the measured results, not shown as samples.
SAMPLES = [("clean", "es"), ("clean", "kk"), ("clean", "ur"), ("mild_noise", "es"), ("heavy_noise", "es"), ("clipping", "es"),
           ("long_silence", "kk"), ("wrong_language", "ur"), ("wrong_text", "es"), ("cut_off", "kk")]


def wav_b64(path, max_s=60):  # full length: trimming would change the silence checks
    x, sr = sf.read(path, dtype="float32")
    x = x[: int(max_s * sr)]
    buf = io.BytesIO()
    sf.write(buf, (np.clip(x, -1, 1) * 32767).astype("int16"), sr, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


def main(features, evalset, results, fresh, out):
    items = [json.loads(l) for l in open(features, encoding="utf-8")]
    res = json.load(open(results))
    fr = json.load(open(fresh)); fr.pop("errors", None)
    th = res["thresholds"]
    from types import SimpleNamespace
    sys.path.insert(0, os.path.join(HERE, ".."))
    from qc.checks import Thresholds, check_recording
    tt = Thresholds(**th)

    def judge(it):
        return check_recording(SimpleNamespace(**it["signal"]), it["prompt"], it["lang"], SimpleNamespace(**it["asr"]), tt)

    sample_js = []
    for v, lang in SAMPLES:
        cands = sorted((it for it in items if it["variant"] == v and it["lang"] == lang), key=lambda it: it["id"])
        cands = [it for it in cands if (not judge(it)[0]) == (it["label"] == "reject")]
        if not cands:
            continue
        it = cands[0]
        ok, reasons, usable = judge(it)
        sample_js.append({"id": it["id"], "variant": it["variant"], "label": it["label"], "lang": it["lang"], "prompt": it["prompt"],
                          "audio": wav_b64(os.path.join(evalset, it["path"])), "asr": it["asr"],
                          "server": {"accept": ok, "reasons": reasons, "usable_s": usable}})
    qcjs = open(os.path.join(HERE, "qc.js"), encoding="utf-8").read()
    tpl = open(os.path.join(HERE, "page_template.html"), encoding="utf-8").read()
    html = (tpl.replace("/*QCJS*/", qcjs)
               .replace("/*THRESHOLDS*/null", json.dumps(th))
               .replace("/*RESULTS*/null", json.dumps(res))
               .replace("/*FRESH*/null", json.dumps(fr))
               .replace("/*SAMPLES*/null", json.dumps(sample_js, ensure_ascii=False)))
    open(out, "w", encoding="utf-8").write(html)
    print("samples:", [s["variant"] + "/" + s["lang"] for s in sample_js], "size KB:", len(html) // 1024)


if __name__ == "__main__":
    main(*sys.argv[1:6])
