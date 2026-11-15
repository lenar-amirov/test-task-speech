"""Server-side check: speech recognition against the prompt, and language detection.

Uses faster-whisper (open Whisper weights, runs on CPU). The model is loaded once.
"""
import os
import re
import unicodedata
from dataclasses import dataclass, asdict

_MODEL = None


def get_model(size="small"):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=int(os.environ.get("QC_THREADS", "0")))
    return _MODEL


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _edit(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref, hyp):
    ref, hyp = normalize(ref).replace(" ", ""), normalize(hyp).replace(" ", "")
    if not ref:
        return 1.0
    return _edit(ref, hyp) / len(ref)


@dataclass
class AsrReport:
    transcript: str
    language: str
    language_prob: float
    lang_prob_expected: float
    cer: float

    def as_dict(self):
        return asdict(self)


def transcribe(audio, prompt, expected_lang, model_size="small"):
    model = get_model(model_size)
    # 1) which language does the model hear?
    lang, lang_p, all_probs = model.detect_language(audio)
    probs = dict(all_probs or [])
    p_expected = float(probs.get(expected_lang, lang_p if lang == expected_lang else 0.0))
    # 2) transcribe in the expected language and compare with the prompt
    segments, _ = model.transcribe(audio, language=expected_lang, beam_size=1,
                                   vad_filter=False, condition_on_previous_text=False)
    text = " ".join(s.text for s in segments).strip()
    return AsrReport(text, lang, round(float(lang_p), 3), round(p_expected, 3), round(cer(prompt, text), 3))
