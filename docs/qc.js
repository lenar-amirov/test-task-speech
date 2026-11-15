// Phone-side checks, ported 1:1 from prototype/qc/signal.py and qc/checks.py (signal part).
const SR = 16000, FRAME = 480, HOP = 160;

async function decodeTo16k(arrayBuffer) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const buf = await ctx.decodeAudioData(arrayBuffer.slice(0));
  ctx.close && ctx.close();
  const len = Math.max(1, Math.ceil(buf.duration * SR));
  const off = new OfflineAudioContext(1, len, SR);
  const src = off.createBufferSource();
  src.buffer = buf; src.connect(off.destination); src.start();
  const out = await off.startRendering();
  return out.getChannelData(0);
}

function percentile(arr, p) {
  const a = Float64Array.from(arr).sort();
  if (!a.length) return 0;
  const i = (a.length - 1) * p / 100, lo = Math.floor(i), hi = Math.ceil(i);
  return a[lo] + (a[hi] - a[lo]) * (i - lo);
}

function frameDb(x) {
  if (x.length < FRAME) { const y = new Float32Array(FRAME); y.set(x); x = y; }
  const n = 1 + Math.floor((x.length - FRAME) / HOP), db = new Float64Array(n);
  for (let f = 0; f < n; f++) {
    let s = 0; const o = f * HOP;
    for (let k = 0; k < FRAME; k++) { const v = x[o + k]; s += v * v; }
    db[f] = 20 * Math.log10(Math.sqrt(s / FRAME + 1e-12) + 1e-12);
  }
  return db;
}

function runs(mask) {
  const r = []; let s = -1;
  for (let i = 0; i <= mask.length; i++) {
    const v = i < mask.length && mask[i];
    if (v && s < 0) s = i;
    if (!v && s >= 0) { r.push([s, i]); s = -1; }
  }
  return r;
}

function analyzeSignal(x) {
  const duration = x.length / SR, db = frameDb(x);
  const noise = percentile(db, 10), peak = percentile(db, 95);
  const thr = Math.max(noise + 0.35 * (peak - noise), noise + 6, -55);
  const voiced = Array.from(db, v => v > thr);
  for (const [s, e] of runs(voiced.map(v => !v))) if (s > 0 && e < voiced.length && e - s < 20) for (let i = s; i < e; i++) voiced[i] = true;
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
  const un = []; db.forEach((v, i) => { if (!voiced[i]) un.push(v); });
  const noiseDb = un.length > 5 ? percentile(un, 50) : noise;
  let clip = 0; for (let i = 0; i < x.length; i++) if (Math.abs(x[i]) >= 0.98) clip++;
  return { duration_s: duration, speech_s: speech, lead_silence_s: lead, trail_silence_s: trail, max_gap_s: gap,
           clip_ratio: x.length ? clip / x.length : 0, speech_dbfs: speechDb, noise_dbfs: noiseDb, snr_db: speechDb - noiseDb };
}

function checkSignal(sig, prompt, th) {
  const reasons = [], expected = Math.max(1, prompt.length / th.chars_per_s);
  if (sig.speech_s < th.min_speech_ratio * expected) reasons.push('too short or cut off');
  if (sig.clip_ratio > th.max_clip_ratio) reasons.push('clipping');
  if (sig.speech_dbfs < th.min_speech_dbfs) reasons.push('too quiet');
  if (sig.snr_db < th.min_snr_db) reasons.push('too noisy');
  // Silence at the start or end is trimmed on the server, so only a long pause inside rejects.
  if (sig.max_gap_s > th.max_gap_s) reasons.push('long silence');
  return { accept: !reasons.length, reasons, usable_s: reasons.length ? 0 : sig.speech_s };
}
