# DataTasks speech auto-QC

Live demo: **https://lenar-amirov.github.io/test-task-speech/**

The check that decides whether a read-speech recording is good enough to pay for and ship to a buyer,
before any human looks at it. Built for the inDrive DataTasks Staff PM home task (the Build question).

**Measured result (the live server):** on 300 real recordings kept out of all tuning, 95% of bad recordings stopped
(143/150, 95% CI 91–98%) and 2.7% of good ones wrongly rejected (4/150, CI 1.0–6.7%). Targets were ≥90% and ≤5%;
the false-reject upper bound is above 5%, so a larger test is next. Signal limits were frozen before any check ran. Text limits were re-tuned once
for the deployed Whisper large-v3 on a separate FLEURS dev-split set, after a first run on these sets, then frozen; both runs are published. Measured languages: Spanish, Kazakh and Urdu.
Cutting 20–30% of a recording (about a third of the words) is caught 92% of the time. Silence at the start or end is cut off, not rejected (a rule changed after the test and re-scored on the same server answers; the earlier 4% run is published). Weak spot: Kazakh, 2 of 49 good recordings rejected for their text.
Details: [`eval/README.md`](eval/README.md).

**Built with:** Claude (Anthropic) as my coding partner for the page, the server code and the measurement;
GitHub Pages and Actions; Supabase (edge functions, Postgres, storage); Groq running OpenAI's Whisper large-v3.

## Try it

Open the live demo, pick a language (Spanish, Kazakh and Urdu are the measured ones) and a case (read it right, read a different sentence, stop halfway,
pause, another language, whisper), record, and get the verdict from the phone checks and then from the server.
English and Russian are for trying it yourself; they reuse the Spanish text limit and are not part of the measured numbers.

## Architecture

```
Phone (browser)                      Supabase
───────────────                      ─────────────────────────────────────────────
record 16 kHz WAV                    edge function qc-check
phone checks: length, silence,  ──►    1. re-check the signal (never trust the phone)
clipping, loudness, noise              2. store the recording   → Storage bucket qc-recordings (private)
(bad take: re-record on the spot)      3. speech recognition    → Whisper large-v3 (Groq): language + words
                                       4. decide: language matches, text error ≤ limit
                                       5. log the decision      → Postgres schema qc, table checks (not exposed)
                               ◄──   verdict: accept / re-record, reason, usable seconds we pay for
```

At scale the same function sits behind a queue; borderline recordings (target ≤5% of volume) go to human review,
and accepted recordings are packed into buyer batches that must stay under the buyer's 10% rejection rule.

| Folder | What |
|---|---|
| `docs/` | The web page (GitHub Pages) and the browser port of the phone checks |
| `supabase/` | Edge functions `qc-check` (live) and `qc-eval` (measurement, same code) and the SQL migrations |
| `prototype/` | The original Python checks and CLI (`prototype/README.md`) |
| `eval/` | How it was measured: data build, tuning, fresh-set check, results |

## Limits

- Measured on planted defects in real recordings, not on real buyer decisions.
- FLEURS is read in quiet rooms; car noise, phone microphones and spontaneous speech will be harder.
- Text limits were re-tuned once for the deployed Whisper large-v3 (they had been set on Whisper small). The trade: skipped words caught 23/60 → 55/60, good recordings wrongly rejected 3/150 → 6/150. Then edge silence was switched from rejecting to trimming: 6/150 → 4/150. All runs are kept in `eval/results/`.
