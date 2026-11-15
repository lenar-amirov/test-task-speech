// qc-check: the server half of speech auto-QC.
// Phone uploads a 16 kHz WAV + the prompt it showed. We re-run the signal checks (never trust the phone),
// store the recording, run Whisper for language + transcript, compare with the prompt, log the decision, return the verdict.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import postgres from "npm:postgres@3.4.4";
import { analyzeSignal, decodeWav, TH, verdict } from "./qc.ts";

const LANGS = new Set(["en", "ru", "es", "kk", "ur"]);
const MAX_BYTES = 2_000_000; // ~60 s of 16 kHz 16-bit mono
const MAX_SECONDS = 45;
const PER_IP_PER_HOUR = 40;
const ASR_MODEL = Deno.env.get("ASR_MODEL") ?? "whisper-large-v3";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });

async function sha256(s: string) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

// The decision log lives in schema `qc`, which is not exposed through the public API; only this function reaches it.
const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 2, prepare: false });

async function feed() {
  const rows = await sql`select id, created_at, lang, scenario, transcript, detected_lang, cer, accept, reasons, usable_s
    from qc.checks order by created_at desc limit 10`;
  const [stats] = await sql`select count(*)::int as total, count(*) filter (where accept)::int as accepted,
    coalesce(sum(usable_s), 0)::real as usable_seconds from qc.checks`;
  return { rows, stats };
}

async function transcribe(wav: Uint8Array) {
  const key = Deno.env.get("GROQ_API_KEY");
  if (!key) throw new Error("GROQ_API_KEY is not set");
  const form = new FormData();
  form.append("file", new Blob([wav], { type: "audio/wav" }), "rec.wav");
  form.append("model", ASR_MODEL);
  form.append("response_format", "verbose_json");
  form.append("temperature", "0");
  const r = await fetch("https://api.groq.com/openai/v1/audio/transcriptions", {
    method: "POST", headers: { Authorization: `Bearer ${key}` }, body: form,
  });
  if (!r.ok) throw new Error(`ASR ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  return { text: String(j.text ?? "").trim(), language: String(j.language ?? "") };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method === "GET") {
    try { return json(await feed()); } catch (e) { console.error(e); return json({ error: "feed unavailable" }, 500); }
  }
  if (req.method !== "POST") return json({ error: "GET or POST only" }, 405);
  const t0 = performance.now();
  const steps: Record<string, number> = {};
  const lap = (name: string, from: number) => { steps[name] = Math.round(performance.now() - from); return performance.now(); };

  try {
    const form = await req.formData();
    const file = form.get("audio");
    const lang = String(form.get("lang") ?? "");
    const prompt = String(form.get("prompt") ?? "").slice(0, 400);
    const scenario = String(form.get("scenario") ?? "").slice(0, 40);
    let phone: unknown = null;
    try { phone = JSON.parse(String(form.get("phone") ?? "null")); } catch { phone = null; }
    if (!(file instanceof File)) return json({ error: "audio missing" }, 400);
    if (!LANGS.has(lang)) return json({ error: "unsupported language" }, 400);
    if (!prompt) return json({ error: "prompt missing" }, 400);
    if (file.size > MAX_BYTES) return json({ error: "recording too long" }, 413);

    const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
    const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim();
    const ipHash = await sha256(ip + (Deno.env.get("IP_SALT") ?? "qc"));
    const since = new Date(Date.now() - 3600_000).toISOString();
    const [{ n }] = await sql`select count(*)::int as n from qc.checks where ip_hash = ${ipHash} and created_at >= ${since}`;
    if (n >= PER_IP_PER_HOUR) return json({ error: "Too many checks from this network. Try again in an hour." }, 429);

    // 1) signal checks, recomputed on the server
    let t = performance.now();
    const wav = new Uint8Array(await file.arrayBuffer());
    const x = decodeWav(wav);
    if (x.length / 16000 > MAX_SECONDS) return json({ error: "recording too long" }, 413);
    const sig = analyzeSignal(x);
    t = lap("signal", t);

    // 2) keep the recording (buyers audit samples; disputes need the original)
    const path = `${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.wav`;
    const up = await db.storage.from("qc-recordings").upload(path, wav, { contentType: "audio/wav" });
    if (up.error) throw new Error(`storage: ${up.error.message}`);
    t = lap("storage", t);

    // 3) speech recognition: which language, which words
    const asr = await transcribe(wav);
    t = lap("asr", t);

    // 4) decision
    const v = verdict(sig, prompt, lang, asr, TH);
    t = lap("decision", t);

    // 5) log every decision: this table is what the buyer report and threshold tuning are built from
    const row = {
      lang, scenario, prompt, transcript: asr.text, detected_lang: asr.language, cer: v.cer,
      signal: sql.json(sig), phone: sql.json(phone as never), accept: v.accept, reasons: v.reasons, usable_s: v.usable_s,
      asr_model: ASR_MODEL, audio_path: path, ip_hash: ipHash,
    };
    const [ins] = await sql`insert into qc.checks ${sql(row as Record<string, unknown>)} returning id, created_at`;
    lap("log", t);
    steps.total = Math.round(performance.now() - t0);

    return json({ id: ins.id, created_at: ins.created_at, accept: v.accept, reasons: v.reasons, usable_s: v.usable_s,
      cer: v.cer, transcript: asr.text, detected_lang: asr.language, signal: sig, asr_model: ASR_MODEL, steps });
  } catch (e) {
    console.error(e);
    return json({ error: String((e as Error).message ?? e) }, 500);
  }
});
