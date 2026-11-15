# How the speech auto-QC was measured

**The live server, measured on a fresh set the tuning never saw: 95% of bad recordings caught (143/150),
2% of good ones wrongly rejected (3/150).** Targets were ≥90% and ≤5%. Thresholds were frozen before any of this ran.

| Fresh set, 300 recordings | Caught (bad → re-record) | Wrongly rejected (good → re-record) |
|---|---|---|
| **Live server: Whisper large-v3 (Groq) + server rules** | **95% (143/150), 95% CI 91–98%** | **2% (3/150), 95% CI 0.7–5.7%** |
| Prototype: Whisper small + language-probability check | 97% (145/150), CI 92–99% | 2% (3/150), CI 0.7–5.7% |
| Phone checks only | 51% | 1% |

The upper end of the false-reject interval (5.7%) is above the 5% target: 150 good recordings cannot prove ≤5%.
At the same 2% rate, about 250 good recordings put the upper bound under 5% (4.6%); 400 give 3.9%. That is the next test.

By language (live server, share judged right): Spanish 97%, Kazakh 93%, Urdu 100%.
Weakest case: heavy noise, 18 of 25 caught (72%). Large-v3 hears through the noise, so the words still match; whether a
buyer would reject these is itself a question for the human-label test. Batches of 100 with 10% bad submitted: 42% break
the buyer's 10% rule without the check, 0% with it; bad share in what ships 0.5%.

## Harder defects (live server, frozen thresholds)

160 more recordings from the FLEURS test split with subtler defects (`hard_set.py`):

| Defect | Caught | Note |
|---|---|---|
| Kazakh sentence read in Russian (same sentence, another speaker) | 40/40 | all as "wrong language" |
| Light clipping, 0.2–1% of samples | 60/60 | |
| 20–30% of the sentence skipped in the middle | **23/60 (38%)** | Spanish 13/20, Urdu 9/20, Kazakh 1/20 |

Skipped words are the real gap. One character-error limit per language cannot tell "skipped a quarter of the
sentence" from "a recogniser that struggles with the language": Kazakh's limit is 0.55 because Whisper's Kazakh is
weak, and that same slack lets skipped words through. 21 of the 60 land within ±0.05 of the limit and get the borderline
flag; a person listening to those would raise the catch to 35/60. The fix is a word-level alignment check (flag any run of
missing words longer than ~15% of the sentence), tuned on a build set and re-checked on a fresh one. Not done here,
because changing the rules after seeing these results would break the frozen-threshold discipline.

How the live server was measured: `server_eval.py` sends every recording to the `qc-eval` edge function, which runs
the same code as the live `qc-check` (shared `qc.ts` and `asr.ts`) but stores nothing and accepts only the eval
recordings (by PCM hash). Run by GitHub Actions (`.github/workflows/qc-eval.yml`, about 30 minutes, about 1.5 hours of audio,
about $0.20 of Groq time). Every verdict with its transcript: `results/server_rows.jsonl`; totals and errors: `results/server_results.json`.

## Steps

1. **Data.** Google FLEURS read speech (CC BY 4.0): Spanish (Latin America), Kazakh, Urdu.
   Each source recording is brought to one level (-26 dBFS, as a phone's gain control does), then kept acceptable
   (clean, background talk, a bit quieter) or given one defect a buyer rejects (wrong sentence, heavy noise, clipping,
   cut off, other language, long silence, too quiet). `build_set.py`.
2. **Build set** (FLEURS dev split, 300, seed 7). Features: `run_features.py`. Thresholds tuned on the dev half
   for the highest catch rate with ≤5% wrongly rejected, then frozen: `evaluate.py` → `results/results.json`
   (committed before step 3 ran).
3. **Fresh set** (FLEURS test split: no sentence shared with the build set, checked; 300, seed 11).
   Checked once with frozen thresholds, no tuning: `fresh_check.py` → `results/fresh_results.json` (lists every error).
   Rebuild exactly: `python build_set.py FLEURS_DIR OUT 100 11 test` (same items, same 8 errors by id).
4. **Live server** on the fresh set and the hard set: `server_eval.py` (above).

## Limits

- Planted defects, not buyer decisions. Next: Common Voice reviewer decisions (gated download), then Buyer B's pilot.
- The same generator made the defects in both sets; a defect type we did not think of is not measured.
- Quiet-room read speech. Car noise, phone microphones and spontaneous speech will be harder.
- No frontier baseline yet: the obvious comparison is an audio LLM judging the same 460 recordings (cost and accuracy).
- Fraud: the same file sent twice is caught (PCM hash). Replay of someone else's voice, text-to-speech and one voice
  behind several accounts are not; they need speaker embeddings and a synthetic-voice detector.

## What the buyer gets with each batch (next step)

The decision log (`qc.checks`) already holds per recording: verdict, reasons, text error, usable seconds, model,
signal measurements, source (mic or upload), attempt number and audio hash. A batch report adds what a buyer's spec
asks for: contributor_id, age band, gender, country and accent, so it can show quotas met, recordings per person
(one person, one recording where the spec says so), duplicates removed, and the auto-QC reject rate with reasons.
Buyer blind audit target: under 10% rejected per batch.
