"""Turn signal and recognition reports into a verdict: accept or re-record, with reasons."""
from dataclasses import dataclass, asdict, field

# Spoken Urdu and Hindi are one language (Hindustani); Whisper often labels Urdu speech as Hindi.
SAME_SPOKEN = {"ur": {"hi"}}


def expected_lang_prob(asr, lang):
    p = asr.lang_prob_expected
    if asr.language in SAME_SPOKEN.get(lang, ()):
        p += asr.language_prob
    return p


@dataclass
class Thresholds:
    min_speech_ratio: float = 0.45      # speech seconds vs. what the prompt needs
    chars_per_s: float = 14.0           # typical read-speech pace
    max_clip_ratio: float = 0.001
    min_speech_dbfs: float = -42.0
    min_snr_db: float = 12.0
    max_edge_silence_s: float = 4.0
    max_gap_s: float = 3.0
    # probability the model gives the expected language; Kazakh is weak in Whisper small, so the text check carries it
    min_lang_prob: dict = field(default_factory=lambda: {"es": 0.30, "kk": 0.02, "ur": 0.10})
    max_cer: dict = field(default_factory=lambda: {"es": 0.35, "kk": 0.70, "ur": 0.45})

    def as_dict(self):
        return asdict(self)


def check_recording(sig, prompt, lang, asr=None, th=None):
    """Return (accept: bool, reasons: list[str], usable_s: float)."""
    th = th or Thresholds()
    reasons = []
    expected_s = max(1.0, len(prompt) / th.chars_per_s)
    if sig.speech_s < th.min_speech_ratio * expected_s:
        reasons.append("too short or cut off")
    if sig.clip_ratio > th.max_clip_ratio:
        reasons.append("clipping")
    if sig.speech_dbfs < th.min_speech_dbfs:
        reasons.append("too quiet")
    if sig.snr_db < th.min_snr_db:
        reasons.append("too noisy")
    if sig.lead_silence_s > th.max_edge_silence_s or sig.trail_silence_s > th.max_edge_silence_s or sig.max_gap_s > th.max_gap_s:
        reasons.append("long silence")
    if asr is not None:
        if expected_lang_prob(asr, lang) < th.min_lang_prob.get(lang, 0.3):
            reasons.append("wrong language")
        elif asr.cer > th.max_cer.get(lang, 0.5):
            reasons.append("does not match the prompt")
    accept = not reasons
    return accept, reasons, (sig.speech_s if accept else 0.0)
