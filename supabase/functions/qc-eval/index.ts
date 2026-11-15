// qc-eval: measures the deployed pipeline (server signal checks + Groq Whisper large-v3 + server rules) on the
// labelled eval sets. It runs exactly the code qc-check runs, but stores nothing and only accepts the eval recordings
// (their PCM hashes are published with the eval set; calls also need the project's public key), so it cannot be used
// as a free transcription endpoint.
import { analyzeSignal, decodeWav, pcmSha256, TH, verdict } from "./qc.ts";
import { ASR_MODEL, AsrError, transcribe } from "./asr.ts";

const HASHES_URL = "https://lenar-amirov.github.io/test-task-speech/evalset/hashes.json";
let allowed: Promise<Set<string>> | null = null;
const evalHashes = () => (allowed ??= fetch(HASHES_URL).then((r) => r.json()).then((a: string[]) => new Set(a)));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  try {
    const form = await req.formData();
    const file = form.get("audio"), lang = String(form.get("lang") ?? ""), prompt = String(form.get("prompt") ?? "");
    if (!(file instanceof File) || !prompt) return json({ error: "audio and prompt required" }, 400);
    const wav = new Uint8Array(await file.arrayBuffer());
    const hash = await pcmSha256(wav);
    if (!(await evalHashes()).has(hash.slice(0, 16))) return json({ error: "not an eval recording" }, 403);
    const sig = analyzeSignal(decodeWav(wav));
    const asr = await transcribe(wav, lang);
    const v = verdict(sig, prompt, lang, asr, TH);
    return json({ ...v, transcript: asr.text, detected_lang: asr.language, signal: sig, asr_model: ASR_MODEL, thresholds: TH });
  } catch (e) {
    if (e instanceof AsrError && (e.status === 429 || e.status >= 500)) return json({ error: e.message }, 429);
    console.error(e);
    return json({ error: String((e as Error).message ?? e) }, 500);
  }
});
