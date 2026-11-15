# Speech auto-QC — the prototype

The piece of the pipeline built for real: the check that decides whether a read-speech
recording is good enough to pay for and ship, before any human looks at it.

Why this piece: in the plan, automatic checks must take ≥95% of the decisions so human review
stays ≤5% of volume. If this check is weak, either the buyer rejects batches (the 10% rule) or
review cost eats the margin.

## Two layers

| Layer | Runs on | Checks | Code |
|---|---|---|---|
| Phone | the contributor's phone, before upload | speech seconds vs. prompt length, clipping, loudness, noise (SNR), silence at start / end / long pauses | `qc/signal.py`, `web/qc.js` (same logic) |
| Server | after upload | language (Whisper language ID), words match the prompt (character error rate) | `qc/asr.py` (faster-whisper small, CPU int8) |

Verdict: accept or re-record, with a reason the contributor sees, plus *usable seconds* — the
speech we pay for. Thresholds: `qc/checks.py`, tuned values in `../eval/results/results.json`.

## Run

```bash
pip install faster-whisper soundfile scipy numpy
```

```bash
python qc_cli.py check rec.wav --prompt "Texto que se leyó" --lang es
```

```bash
python qc_cli.py batch manifest.csv   # columns: path,prompt,lang → ship / hold vs. the 10% rule
```

Demo page (phone checks live in the browser, server results for real samples, measured results):
`web/build_page.py` builds one HTML file from `web/page_template.html` + `web/qc.js`.

## How it was measured

See `../eval/`: 300 real FLEURS recordings (Spanish LatAm, Kazakh, Urdu; CC BY 4.0), each kept
clean or given one planted defect a buyer rejects. Thresholds tuned on the dev half, numbers
reported on the held-out test half, then batches of 100 simulated against the buyer's 10% rule.

```bash
python ../eval/build_set.py FLEURS_DIR EVALSET_DIR 100
```

```bash
python ../eval/run_features.py EVALSET_DIR features.jsonl small
```

```bash
python ../eval/evaluate.py features.jsonl ../eval/results
```

Limits: planted defects, not real buyer decisions; quiet-room read speech, not car noise.
