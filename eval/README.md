# How the speech auto-QC was measured

**The live server, measured on a fresh set the tuning never saw: 95% of bad recordings caught (143/150),
4% of good ones wrongly rejected (6/150).** Targets were ≥90% and ≤5%. Thresholds were frozen before any of this ran.

| Fresh set, 300 recordings | Caught (bad → re-record) | Wrongly rejected (good → re-record) |
|---|---|---|
| **Live server: Whisper large-v3 (Groq), text limits tuned for it** | **95% (143/150), 95% CI 91–98%** | **4% (6/150), 95% CI 1.9–8.5%** |
| Live server with the old text limits (set on Whisper small) | 95% (143/150), CI 91–98% | 2% (3/150), CI 0.7–5.7% |
| Prototype: Whisper small + language-probability check | 97% (145/150), CI 92–99% | 2% (3/150), CI 0.7–5.7% |
| Phone checks only | 51% | 1% |

The upper end of the false-reject interval (8.5%) is above the 5% target: 150 good recordings cannot prove ≤5%.
At a 4% rate it takes about 2,000 good recordings to put the upper bound under 5%; at 3%, about 500. That is the next test,
and trimming trailing silence (below) should bring the rate down first.

By language (live server, share judged right): Spanish 96%, Kazakh 92%, Urdu 99%. Good recordings wrongly rejected:
Spanish 1/51, Urdu 1/50, Kazakh 4/49 (8%). Two of the Kazakh four are a trailing pause of about 4.2 s against the 4 s
limit; two are text (Whisper's Kazakh is weak). Trimming trailing silence before the check is the next fix.
Weakest defect: heavy noise, 18 of 25 caught (72%). The words still come through and the server's noise estimate reads
too high on babble noise (it measures 3–17 dB where 4 dB or less was planted), so the noise rule misses them; whether
a buyer would reject these is itself a question for the human-label test. Batches of 100 with 10% bad submitted: 43% break
the buyer's 10% rule without the check, 0% with it; bad share in what ships 0.5%.

## Harder defects (live server, frozen thresholds)

160 more recordings from the FLEURS test split with subtler defects (`hard_set.py`):

| Defect | Caught | Note |
|---|---|---|
| Kazakh sentence read in Russian (same sentence, another speaker) | 40/40 | all as "wrong language" |
| Light clipping, 0.2–1% of samples | 60/60 | |
| 20–30% of the sentence skipped in the middle | **55/60 (92%)** | was 23/60 with the old text limits; 4 of the 5 misses get the borderline flag |

Skipped words were the real gap with the old text limits (23/60). Those limits were set on Whisper small, whose
Kazakh is weak, so Kazakh's limit was 0.55 and that slack let skipped words through (Kazakh 1/20). On the deployed
large-v3 good readings sit much lower, so the text limits were re-tuned once: `dev_set.py` builds 390 recordings from
the FLEURS dev split (no recording shared with the fresh or hard sets, 30 per language with skipped words), they run
through `qc-eval` (`results/dev_rows.jsonl`), and `tune_text.py` picks per language the lowest limit that wrongly rejects
at most 2% of good dev recordings for their text: Spanish 0.15, Kazakh 0.25, Urdu 0.25 (`results/text_limits.json`).
Frozen, then the fresh and hard sets were run once. Now skipped words (20–30% of the audio cut, about a third of the words) are caught 55/60 (Spanish 20/20, Kazakh 18/20,
Urdu 17/20); with a person on borderline cases, 59/60. The price is 3 more good recordings rejected on the fresh set
(6/150 instead of 3/150: the new limits added four text rejections, and one old rejection, kk_056, went away because
hosted Whisper does not return exactly the same transcript every run; 5 of 460 transcripts differed between the two runs). The old run is kept: `results/server_rows_whisper_small_limits.jsonl`.
The skips here come from the same generator the limits were tuned on, and skipping one to three words was not
measured. A word-level alignment check (flag any run of missing words longer than ~15% of the sentence) would catch skips
without tightening the whole-sentence limit; that is the next step.

How the live server was measured: `server_eval.py` sends every recording to the `qc-eval` edge function, which runs
the same code as the live `qc-check` (shared `qc.ts` and `asr.ts`) but stores nothing and accepts only the eval
recordings (by PCM hash). Run by GitHub Actions in my working repository (workflow copied here: `ci/qc-eval.yml`, `ci/qc-dev.yml`; about 30 minutes, about 1.5 hours of audio,
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
