// Speech recognition through Groq's hosted Whisper. Shared by qc-check (live) and qc-eval (measurement),
// so the measured pipeline is the deployed one.
export const ASR_MODEL = Deno.env.get("ASR_MODEL") ?? "whisper-large-v3";

export class AsrError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call(wav: Uint8Array, language?: string) {
  const key = Deno.env.get("GROQ_API_KEY");
  if (!key) throw new Error("GROQ_API_KEY is not set");
  const form = new FormData();
  form.append("file", new Blob([wav], { type: "audio/wav" }), "rec.wav");
  form.append("model", ASR_MODEL);
  form.append("response_format", "verbose_json");
  form.append("temperature", "0");
  if (language) form.append("language", language);
  const r = await fetch("https://api.groq.com/openai/v1/audio/transcriptions", {
    method: "POST", headers: { Authorization: `Bearer ${key}` }, body: form,
  });
  if (!r.ok) throw new AsrError(r.status, `ASR ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  return { text: String(j.text ?? "").trim(), language: String(j.language ?? "") };
}

/** Transcribe without telling Whisper the language, so it can tell us when the speaker used another one. */
export async function transcribe(wav: Uint8Array, lang: string) {
  const asr = await call(wav);
  // Whisper often labels spoken Urdu as Hindi and writes it in Devanagari. Same spoken language, so ask again for Urdu script.
  if (lang === "ur" && ["hi", "hindi"].includes(asr.language.toLowerCase())) return { ...(await call(wav, "ur")), language: asr.language };
  return asr;
}
