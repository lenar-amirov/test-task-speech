# How the speech auto-QC was measured

**Result on a fresh set the tuning never saw: 97% of bad recordings caught, 2% of good ones wrongly rejected.**

| | Caught (bad → re-record) | Wrongly rejected (good → re-record) |
|---|---|---|
| Fresh set, phone + server (headline) | 97% (145/150) | 2% (3/150) |
| Fresh set, phone checks only | 51% | 1% |
| Build set, held-out half* | 96% | 6% |

\* That half was looked at while fixing the checks (level normalisation, Urdu/Hindi), so it is not clean.

By language (fresh, share judged right): Spanish 98%, Kazakh 95%, Urdu 99%.
Weakest case: heavy noise, 80% caught. Batches of 100 with 10% bad submitted: 42% break the buyer's 10% rule
without the check, 0% with it; bad share in what we ship 0.4%.

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

## Limits

- Planted defects, not buyer decisions. Next: Common Voice reviewer decisions (gated download), then Buyer B's pilot.
- The same generator made the defects in both sets; a defect type we did not think of is not measured.
- Quiet-room read speech. Car noise, phone microphones and spontaneous speech will be harder.
