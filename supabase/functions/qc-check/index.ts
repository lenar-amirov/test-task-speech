// qc-check: the server half of speech auto-QC.
// Phone uploads a 16 kHz WAV + the prompt it showed. We re-run the signal checks (never trust the phone),
// store the recording, run Whisper for language + transcript, compare with the prompt, log the decision, return the verdict.
// Also: the same recording uploaded twice is rejected, silence at the edges is cut off the stored file instead of
// rejecting it, and stored recordings are deleted after 30 days.
// Appeals: a rejected contributor may ask for up to MAX_RECHECKS re-checks of the same recording; if those are negative too,
// they can send it to a person (action=dispute), which marks the row for human review.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import postgres from "npm:postgres@3.4.4";
import { analyzeSignal, decodeWav, encodeWav, freeVerdict, pcmSha256, TH, trimEdges, verdict } from "./qc.ts";
import { ASR_MODEL, transcribe } from "./asr.ts";

// Live demo only: loudness floor lowered from the measured -42 to -60 dBFS, so a clean but faint mic (e.g. an iPad)
// is not rejected; noise is still caught by the SNR rule. Measured results and qc-eval keep the frozen TH.
const LIVE_TH = { ...TH, min_speech_dbfs: -60.0 };
const LANGS = new Set(["en", "ru", "es", "kk", "ur"]);
const MAX_BYTES = 2_200_000; // ~65 s of 16 kHz 16-bit mono
const MAX_SECONDS = 65;
const PER_IP_PER_HOUR = 40;
const KEEP_DAYS = 30; // demo retention: recordings older than this are deleted
const MAX_RECHECKS = 1;
// The demo's own sample files (hashed as the page uploads them) may be checked again and again; any other repeat is a duplicate.
const SAMPLE_HASHES = new Set(["46ccb5f2e535b7e9", "5311bbd285e98cc5", "c93b536cd95b56ae", "8f63e873fa611970", "ca0e949e7c4eb32d", "36c88b20861517e6", "33910e4ae8294633", "8da880df678c08db", "5e9e8b84047a90c4", "a2df6b6da2eff967"]);
const SAMPLE_PROMPTS = new Set(["Compartir una excursión en forma virtual también es una excelente forma de reflexionar sobre una salida y compartir experiencias con clases futuras.", "Durante esta fase de la historia de Europa, la iglesia católica, que había acumulado riqueza y poder, fue cuestionada.", "En efecto, es muy difícil de encontrar, incluso si uno estuviera seguro de su existencia. En el interior de la cueva, el aislamiento es total.", "La incorporación de durmientes para mantener a los rieles en su lugar se produjo bastante temprano. No obstante, paulatinamente, fueron dándose cuenta de que los rieles resultarían más eficientes si estuvieran revestidos de hierro en su parte superior.", "Uno de los periodos más sanguinarios de la historia de Antigua China fue el de Los Tres Reinos, donde miles de personas murieron en una lucha por ocupar el trono del gran palacio en Xi'an.", "Баспанасыз балалар тастап кеткенге немесе қашып кеткенге дейін балаларға қарсы зорлық-зомбылықты көрген немесе жанжарақатын алған болуы мүмкін.", "Ең оңай қол жеткізуге болаты өсімдік ресурстарына жапырақтар мен бұршақтарда қолжетімді ақуыздар болды, бірақ әзірлемесе, олар біз сияқты приматтар үшін қорытуға қиын.", "Ол бізге пойызды, автокөлікті және басқа да көлік құралдарын алып келді.", "بالی میں دیگر ایجنڈے میں دنیا کے باقی ماندہ جنگلات کا تحفظ، اور کم آلودگی کے طریقے سے ترقی کرنے کیلئے ترقی پذیر ممالک کے ساتھ ٹیکنالوجز کا اشتراک کرنے کا موضوع شامل کیا گیا۔", "سندربن دنیا کا سب سے بڑا ساحلی مینگروو بیلٹ ہے، جو ساحلی علاقے سے بنگلادیشی اور ہندوستان کے دور افتادہ پسماندہ علاقے میں 80 کیلو میٹر (50 میل) تک پھیلا ہوا ہے۔"]);

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
    // A listing returns at most 1000 files: keep removing until the day's folder is empty, and only then
    // clear audio_path for that day, so no row loses the path of a file that is still stored.
    let empty = false;
    for (let i = 0; i < 20; i++) {
      const { data: files, error } = await db.storage.from("qc-recordings").list(d.name, { limit: 1000 });
      if (error) break;
      const paths = (files ?? []).map((f) => `${d.name}/${f.name}`);
      if (!paths.length) { empty = true; break; }
      const { error: rmErr } = await db.storage.from("qc-recordings").remove(paths);
      if (rmErr) break;
    }
    if (empty) await sql`update qc.checks set audio_path = null where audio_path like ${d.name + "/%"}`;
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
    // Appeal, step 2: after the re-checks also said no, the contributor sends the recording to a person.
    if (String(form.get("action") ?? "") === "dispute") {
      const id = Number(form.get("id"));
      if (!Number.isInteger(id)) return json({ error: "id missing" }, 400);
      const ipD = await sha256((req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() + (Deno.env.get("IP_SALT") ?? "qc"));
      const [orig] = await sql`select id, accept, ip_hash from qc.checks where id = ${id}`;
      if (!orig || orig.ip_hash !== ipD) return json({ error: "Only the person who sent this recording can dispute it." }, 403);
      const [{ n, ok }] = await sql`select count(*)::int as n, bool_or(accept) as ok from qc.checks where signal->>'recheck_of' = ${String(id)}`;
      if (orig.accept || ok) return json({ error: "This recording was accepted; nothing to dispute." }, 409);
      if (n < MAX_RECHECKS) return json({ error: "Check it again first." }, 409);
      await sql`update qc.checks set signal = signal || ${sql.json({ dispute: "requested", dispute_at: new Date().toISOString() })} where id = ${id}`;
      return json({ id, dispute: "requested" });
    }
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
    // Demo samples are meant to be clicked again and again. Browsers re-encode them slightly differently, so the hash
    // list alone is not enough; the page marks them, and the mark only counts with one of the samples' own sentences.
    // (Demo only: production has no samples, so no exemption at all.)
    const isSample = SAMPLE_HASHES.has(hash.slice(0, 16)) || (scenario.startsWith("sample_") && SAMPLE_PROMPTS.has(prompt));
    // Appeal, step 1: "check again" on a rejected recording. Same file, same sender, at most MAX_RECHECKS times.
    const recheckOf = form.get("recheck_of") ? Number(form.get("recheck_of")) : null;
    let reusePath: string | null = null;
    if (recheckOf !== null) {
      const [orig] = await sql`select id, accept, ip_hash, audio_path, signal->>'sha256' as sha from qc.checks where id = ${recheckOf}`;
      if (!orig || orig.sha !== hash || orig.ip_hash !== ipHash) return json({ error: "Only the same recording from the same sender can be checked again." }, 403);
      if (orig.accept) return json({ error: "This recording was already accepted." }, 409);
      const [{ n: done }] = await sql`select count(*)::int as n from qc.checks where signal->>'recheck_of' = ${String(recheckOf)}`;
      if (done >= MAX_RECHECKS) return json({ error: "The re-check is used. You can send it to a person.", rechecks_left: 0 }, 409);
      reusePath = orig.audio_path;
    }
    if (!isSample && recheckOf === null) {
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

    // 2) keep the recording (buyers audit samples; disputes need the original), with silence at the edges cut off:
    // that is the file a buyer would get, so edge silence is fixed here instead of rejected
    const trim = trimEdges(x, sig);
    let path = reusePath;
    if (!path) {
      path = `${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.wav`;
      const up = await db.storage.from("qc-recordings").upload(path, trim.cut_s ? encodeWav(trim.x) : wav, { contentType: "audio/wav" });
      if (up.error) throw new Error(`storage: ${up.error.message}`);
    }
    t = lap("storage", t);

    // 3) speech recognition: which language, which words
    const asr = await transcribe(wav, lang);
    t = lap("asr", t);

    // 4) decision
    const v = task === "free" ? freeVerdict(sig, lang, asr, LIVE_TH) : { ...verdict(sig, prompt, lang, asr, LIVE_TH), words: null };
    t = lap("decision", t);

    // 5) log every decision: this table is what the buyer report and threshold tuning are built from
    const row = {
      lang, scenario, prompt, transcript: asr.text, detected_lang: asr.language, cer: v.cer,
      signal: sql.json({ ...sig, sha256: hash, borderline: v.borderline, trimmed_s: Math.round(trim.cut_s * 100) / 100, ...(recheckOf !== null ? { recheck_of: String(recheckOf) } : {}) }), phone: sql.json(phone as never), accept: v.accept, reasons: v.reasons, usable_s: v.usable_s,
      asr_model: ASR_MODEL, audio_path: path, ip_hash: ipHash,
    };
    const [ins] = await sql`insert into qc.checks ${sql(row as Record<string, unknown>)} returning id, created_at`;
    lap("log", t);
    // housekeeping after the answer is ready; a failure here never affects the verdict
    // @ts-ignore EdgeRuntime is provided by Supabase
    EdgeRuntime.waitUntil(purgeOld(db).catch((e) => console.error("purge", e)));
    steps.total = Math.round(performance.now() - t0);

    return json({ id: ins.id, created_at: ins.created_at, accept: v.accept, reasons: v.reasons, usable_s: v.usable_s,
      cer: v.cer, words: v.words, task, transcript: asr.text, detected_lang: asr.language, signal: sig, asr_model: ASR_MODEL, borderline: v.borderline,
      recheck_of: recheckOf, steps });
  } catch (e) {
    console.error(e);
    return json({ error: String((e as Error).message ?? e) }, 500);
  }
});
