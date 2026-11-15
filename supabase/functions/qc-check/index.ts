// qc-check: the server half of speech auto-QC.
// Phone uploads a 16 kHz WAV + the prompt it showed. We re-run the signal checks (never trust the phone),
// store the recording, run Whisper for language + transcript, compare with the prompt, log the decision, return the verdict.
// Also: the same recording uploaded twice is rejected, and stored recordings are deleted after 30 days.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import postgres from "npm:postgres@3.4.4";
import { analyzeSignal, decodeWav, freeVerdict, pcmSha256, TH, verdict } from "./qc.ts";
import { ASR_MODEL, transcribe } from "./asr.ts";

const LANGS = new Set(["en", "ru", "es", "kk", "ur"]);
const MAX_BYTES = 2_200_000; // ~65 s of 16 kHz 16-bit mono
const MAX_SECONDS = 65;
const PER_IP_PER_HOUR = 40;
const KEEP_DAYS = 30; // demo retention: recordings older than this are deleted
// The demo's own sample files (hashed as the page uploads them) may be checked again and again; any other repeat is a duplicate.
const SAMPLE_HASHES = new Set(["46ccb5f2e535b7e9", "5311bbd285e98cc5", "c93b536cd95b56ae", "8f63e873fa611970", "ca0e949e7c4eb32d", "36c88b20861517e6", "33910e4ae8294633", "8da880df678c08db", "5e9e8b84047a90c4", "a2df6b6da2eff967"]);

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });

async function sha256(s: string) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

// The decision log lives in schema `qc`, which is not exposed through the public API; only this function reaches it.
const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 2, prepare: false });

/** Delete stored recordings from days older than KEEP_DAYS. Folders are named by date, so this is a cheap listing. */
async function purgeOld(db: ReturnType<typeof createClient>) {
  const cutoff = new Date(Date.now() - KEEP_DAYS * 86400_000).toISOString().slice(0, 10);
  const { data: days } = await db.storage.from("qc-recordings").list("", { limit: 100 });
  for (const d of days ?? []) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d.name) || d.name >= cutoff) continue;
    const { data: files } = await db.storage.from("qc-recordings").list(d.name, { limit: 1000 });
    const paths = (files ?? []).map((f) => `${d.name}/${f.name}`);
    if (paths.length) await db.storage.from("qc-recordings").remove(paths);
    await sql`update qc.checks set audio_path = null where audio_path like ${d.name + "/%"}`;
  }
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  // The decision log is private: read it in the Supabase SQL editor (qc.checks), not through this public endpoint.
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  const t0 = performance.now();
  const steps: Record<string, number> = {};
  const lap = (name: string, from: number) => { steps[name] = Math.round(performance.now() - from); return performance.now(); };

  try {
    const form = await req.formData();
    const file = form.get("audio");
    const lang = String(form.get("lang") ?? "");
    const prompt = String(form.get("prompt") ?? "").slice(0, 400);
    const scenario = String(form.get("scenario") ?? "").slice(0, 40);
    const task = String(form.get("task") ?? "read") === "free" ? "free" : "read";
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
    const hash = await pcmSha256(wav);
    t = lap("signal", t);

    // 1b) the same recording sent again (resubmitted for double pay, or shared between accounts)
    if (!SAMPLE_HASHES.has(hash.slice(0, 16))) {
      const [dup] = await sql`select id from qc.checks where signal->>'sha256' = ${hash} limit 1`;
      if (dup) {
        const reasons = ["duplicate recording"];
        const [ins] = await sql`insert into qc.checks ${sql({ lang, scenario, prompt, signal: sql.json({ ...sig, sha256: hash, duplicate_of: dup.id }),
          phone: sql.json(phone as never), accept: false, reasons, usable_s: 0, ip_hash: ipHash } as Record<string, unknown>)} returning id, created_at`;
        steps.total = Math.round(performance.now() - t0);
        return json({ id: ins.id, created_at: ins.created_at, accept: false, reasons, usable_s: 0, cer: null, words: null, task,
          transcript: "", detected_lang: "", signal: sig, asr_model: null, borderline: false, steps });
      }
    }

    // 2) keep the recording (buyers audit samples; disputes need the original)
    const path = `${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.wav`;
    const up = await db.storage.from("qc-recordings").upload(path, wav, { contentType: "audio/wav" });
    if (up.error) throw new Error(`storage: ${up.error.message}`);
    t = lap("storage", t);

    // 3) speech recognition: which language, which words
    const asr = await transcribe(wav, lang);
    t = lap("asr", t);

    // 4) decision
    const v = task === "free" ? freeVerdict(sig, lang, asr, TH) : { ...verdict(sig, prompt, lang, asr, TH), words: null };
    t = lap("decision", t);

    // 5) log every decision: this table is what the buyer report and threshold tuning are built from
    const row = {
      lang, scenario, prompt, transcript: asr.text, detected_lang: asr.language, cer: v.cer,
      signal: sql.json({ ...sig, sha256: hash, borderline: v.borderline }), phone: sql.json(phone as never), accept: v.accept, reasons: v.reasons, usable_s: v.usable_s,
      asr_model: ASR_MODEL, audio_path: path, ip_hash: ipHash,
    };
    const [ins] = await sql`insert into qc.checks ${sql(row as Record<string, unknown>)} returning id, created_at`;
    lap("log", t);
    // housekeeping after the answer is ready; a failure here never affects the verdict
    // @ts-ignore EdgeRuntime is provided by Supabase
    EdgeRuntime.waitUntil(purgeOld(db).catch((e) => console.error("purge", e)));
    steps.total = Math.round(performance.now() - t0);

    return json({ id: ins.id, created_at: ins.created_at, accept: v.accept, reasons: v.reasons, usable_s: v.usable_s,
      cer: v.cer, words: v.words, task, transcript: asr.text, detected_lang: asr.language, signal: sig, asr_model: ASR_MODEL, borderline: v.borderline, steps });
  } catch (e) {
    console.error(e);
    return json({ error: String((e as Error).message ?? e) }, 500);
  }
});
