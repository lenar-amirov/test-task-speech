// Speech auto-QC, server side. Same logic as prototype/qc (signal.py, asr.py, checks.py), ported to TypeScript
// so the server re-checks the recording itself instead of trusting what the phone reports.

export type Signal = {
  duration_s: number; speech_s: number; lead_silence_s: number; trail_silence_s: number; max_gap_s: number;
  clip_ratio: number; speech_dbfs: number; noise_dbfs: number; snr_db: number;
};

export type Thresholds = {
  min_speech_ratio: number; chars_per_s: number; max_clip_ratio: number; min_speech_dbfs: number; min_snr_db: number;
  max_edge_silence_s: number; max_gap_s: number; max_cer: Record<string, number>;
};

// Frozen values from eval/results/results.json (tuned on the build set before the fresh-set check).
// English and Russian are demo languages: they reuse the Spanish text limit and are not part of the measured results.
export const TH: Thresholds = {
  min_speech_ratio: 0.35, chars_per_s: 14.0, max_clip_ratio: 0.001, min_speech_dbfs: -42.0, min_snr_db: 10,
  max_edge_silence_s: 4.0, max_gap_s: 3.0,
  max_cer: { es: 0.25, kk: 0.55, ur: 0.35, en: 0.25, ru: 0.25 },
};

const SR = 16000, FRAME = 480, HOP = 160;

/** Decode a 16-bit PCM mono WAV (what the page uploads) to floats at 16 kHz. */
export function decodeWav(buf: Uint8Array): Float32Array {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const tag = (o: number) => String.fromCharCode(buf[o], buf[o + 1], buf[o + 2], buf[o + 3]);
  if (buf.length < 44 || tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("not a WAV file");
  let o = 12, fmt: { ch: number; sr: number; bits: number } | null = null;
  while (o + 8 <= buf.length) {
    const id = tag(o), size = dv.getUint32(o + 4, true), body = o + 8;
    if (id === "fmt ") fmt = { ch: dv.getUint16(body + 2, true), sr: dv.getUint32(body + 4, true), bits: dv.getUint16(body + 14, true) };
    if (id === "data") {
      if (!fmt || fmt.bits !== 16 || fmt.ch !== 1 || fmt.sr !== SR) throw new Error("expected 16 kHz mono 16-bit WAV");
      const n = Math.floor(Math.min(size, buf.length - body) / 2), x = new Float32Array(n);
      for (let i = 0; i < n; i++) x[i] = dv.getInt16(body + 2 * i, true) / 32768;
      return x;
    }
    o = body + size + (size & 1);
  }
  throw new Error("WAV has no data");
}

function percentile(arr: ArrayLike<number>, p: number): number {
  const a = Float64Array.from(arr).sort();
  if (!a.length) return 0;
  const i = (a.length - 1) * p / 100, lo = Math.floor(i), hi = Math.ceil(i);
  return a[lo] + (a[hi] - a[lo]) * (i - lo);
}

function frameDb(x: Float32Array): Float64Array {
  if (x.length < FRAME) { const y = new Float32Array(FRAME); y.set(x); x = y; }
  const n = 1 + Math.floor((x.length - FRAME) / HOP), db = new Float64Array(n);
  for (let f = 0; f < n; f++) {
    let s = 0; const o = f * HOP;
    for (let k = 0; k < FRAME; k++) { const v = x[o + k]; s += v * v; }
    db[f] = 20 * Math.log10(Math.sqrt(s / FRAME + 1e-12) + 1e-12);
  }
  return db;
}

function runs(mask: boolean[]): [number, number][] {
  const r: [number, number][] = []; let s = -1;
  for (let i = 0; i <= mask.length; i++) {
    const v = i < mask.length && mask[i];
    if (v && s < 0) s = i;
    if (!v && s >= 0) { r.push([s, i]); s = -1; }
  }
  return r;
}

export function analyzeSignal(x: Float32Array): Signal {
  const duration = x.length / SR, db = frameDb(x);
  const noise = percentile(db, 10), peak = percentile(db, 95);
  const thr = Math.max(noise + 0.35 * (peak - noise), noise + 6, -55);
  const voiced = Array.from(db, (v) => v > thr);
  for (const [s, e] of runs(voiced.map((v) => !v))) if (s > 0 && e < voiced.length && e - s < 20) for (let i = s; i < e; i++) voiced[i] = true;
  for (const [s, e] of runs(voiced)) if (e - s < 10) for (let i = s; i < e; i++) voiced[i] = false;
  const rr = runs(voiced), hop = HOP / SR;
  let speech = 0, lead = duration, trail = 0, gap = 0, speechDb = peak;
  if (rr.length) {
    speech = voiced.filter(Boolean).length * hop;
    lead = rr[0][0] * hop;
    trail = Math.max(0, duration - rr[rr.length - 1][1] * hop - FRAME / SR);
    for (let i = 0; i < rr.length - 1; i++) gap = Math.max(gap, (rr[i + 1][0] - rr[i][1]) * hop);
    let e = 0, c = 0; db.forEach((v, i) => { if (voiced[i]) { e += Math.pow(10, v / 10); c++; } });
    speechDb = 10 * Math.log10(e / c);
  }
  const un: number[] = []; db.forEach((v, i) => { if (!voiced[i]) un.push(v); });
  const noiseDb = un.length > 5 ? percentile(un, 50) : noise;
  let clip = 0; for (let i = 0; i < x.length; i++) if (Math.abs(x[i]) >= 0.98) clip++;
  return { duration_s: duration, speech_s: speech, lead_silence_s: lead, trail_silence_s: trail, max_gap_s: gap,
    clip_ratio: x.length ? clip / x.length : 0, speech_dbfs: speechDb, noise_dbfs: noiseDb, snr_db: speechDb - noiseDb };
}

export function signalReasons(sig: Signal, prompt: string, th: Thresholds = TH): string[] {
  const reasons: string[] = [], expected = Math.max(1, prompt.length / th.chars_per_s);
  if (sig.speech_s < th.min_speech_ratio * expected) reasons.push("too short or cut off");
  if (sig.clip_ratio > th.max_clip_ratio) reasons.push("clipping");
  if (sig.speech_dbfs < th.min_speech_dbfs) reasons.push("too quiet");
  if (sig.snr_db < th.min_snr_db) reasons.push("too noisy");
  if (sig.lead_silence_s > th.max_edge_silence_s || sig.trail_silence_s > th.max_edge_silence_s || sig.max_gap_s > th.max_gap_s) reasons.push("long silence");
  return reasons;
}

export function normalize(text: string): string {
  return text.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();
}

function edit(a: string[], b: string[]): number {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
    prev = cur;
  }
  return prev[b.length];
}

/** Character error rate of the transcript against the prompt, spaces and punctuation ignored. */
export function cer(ref: string, hyp: string): number {
  const r = [...normalize(ref).replace(/ /g, "")], h = [...normalize(hyp).replace(/ /g, "")];
  if (!r.length) return 1;
  return edit(r, h) / r.length;
}

// Whisper (Groq) reports the detected language by name.
const LANG_NAMES: Record<string, string[]> = {
  en: ["en", "english"], ru: ["ru", "russian"], es: ["es", "spanish"], kk: ["kk", "kazakh"], ur: ["ur", "urdu", "hi", "hindi"],
};

export function sameLanguage(expected: string, detected: string): boolean {
  return (LANG_NAMES[expected] ?? [expected]).includes((detected || "").toLowerCase());
}

// Whisper's language label is unreliable on a few seconds of audio (English heard as "Russian", Spanish as "Portuguese").
// So the label alone never rejects: the transcript's own alphabet has to agree with it.
const SCRIPT: Record<string, string> = { en: "latin", es: "latin", ru: "cyrillic", kk: "cyrillic", ur: "arabic" };
const KAZAKH_LETTERS = /[әғқңөұүһі]/i;

export function scriptOf(text: string): string | null {
  const n = { latin: 0, cyrillic: 0, arabic: 0, devanagari: 0 };
  for (const ch of text) {
    if (/\p{Script=Latin}/u.test(ch)) n.latin++;
    else if (/\p{Script=Cyrillic}/u.test(ch)) n.cyrillic++;
    else if (/\p{Script=Arabic}/u.test(ch)) n.arabic++;
    else if (/\p{Script=Devanagari}/u.test(ch)) n.devanagari++;
  }
  const [best, count] = Object.entries(n).sort((a, b) => b[1] - a[1])[0];
  return count >= 3 ? best : null;
}

function labelCode(detected: string): string | null {
  const d = (detected || "").toLowerCase();
  for (const [code, names] of Object.entries(LANG_NAMES)) if (names.includes(d)) return code;
  return null;
}

/** True only when the words themselves show another language, not when Whisper's label merely disagrees. */
export function wrongLanguage(expected: string, asr: { language: string; text: string }): boolean {
  const script = scriptOf(asr.text);
  if (!script) return false; // too little text to judge; the length and text checks will speak
  const want = SCRIPT[expected];
  if (expected === "ur" ? !["arabic", "devanagari"].includes(script) : script !== want) return true;
  if (expected === "kk" && KAZAKH_LETTERS.test(asr.text)) return false;
  const label = labelCode(asr.language);
  if (!label || sameLanguage(expected, asr.language)) return false;
  if (expected === "ru" && label === "kk") return KAZAKH_LETTERS.test(asr.text);
  return SCRIPT[label] === script; // e.g. Spanish words for an English task: same alphabet, label agrees with the words
}

export function verdict(sig: Signal, prompt: string, lang: string, asr: { language: string; text: string } | null, th: Thresholds = TH) {
  const reasons = signalReasons(sig, prompt, th);
  let c: number | null = null;
  if (asr) {
    c = cer(prompt, asr.text);
    if (c > (th.max_cer[lang] ?? 0.25)) reasons.push(wrongLanguage(lang, asr) ? "wrong language" : "does not match the prompt");
  }
  const accept = reasons.length === 0;
  return { accept, reasons, usable_s: accept ? sig.speech_s : 0, cer: c };
}

// Free conversation: no text to compare with. Demo rules, not measured against the targets.
export const FREE = { min_speech_s: 10, max_gap_s: 5, min_words: 12 };

export function freeVerdict(sig: Signal, lang: string, asr: { language: string; text: string } | null, th: Thresholds = TH) {
  const reasons: string[] = [];
  if (sig.speech_s < FREE.min_speech_s) reasons.push("too little speech");
  if (sig.clip_ratio > th.max_clip_ratio) reasons.push("clipping");
  if (sig.speech_dbfs < th.min_speech_dbfs) reasons.push("too quiet");
  if (sig.snr_db < th.min_snr_db) reasons.push("too noisy");
  if (sig.lead_silence_s > th.max_edge_silence_s || sig.trail_silence_s > th.max_edge_silence_s || sig.max_gap_s > FREE.max_gap_s) reasons.push("long silence");
  let words = 0;
  if (asr) {
    words = normalize(asr.text).split(" ").filter(Boolean).length;
    if (wrongLanguage(lang, asr)) reasons.push("wrong language");
    else if (words < FREE.min_words) reasons.push("too few words");
  }
  const accept = reasons.length === 0;
  return { accept, reasons, usable_s: accept ? sig.speech_s : 0, cer: null, words };
}
