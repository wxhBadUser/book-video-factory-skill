import fs from "node:fs";
import path from "node:path";

const [projectArg = "."] = process.argv.slice(2);
const projectDir = path.resolve(projectArg);
const spec = JSON.parse(fs.readFileSync(path.join(projectDir, "PROJECT_SPEC.json"), "utf8"));
const audio = JSON.parse(fs.readFileSync(path.join(projectDir, "audio_meta.json"), "utf8"));
const narration = spec.narration ?? {};
const minChars = Number(narration.captionMinChars ?? 6);
const maxChars = Number(narration.captionMaxChars ?? 14);
const minDuration = Number(narration.captionMinDuration ?? 0.55);
const lengthOf = (value) => String(value).normalize("NFKC").replace(/[\s“”‘’"'，。！？；：、,.!?;:…—-]/gu, "").length;
const defects = [];

for (const caption of audio.captions ?? []) {
  const length = lengthOf(caption.text);
  if (length > maxChars) defects.push(`${caption.id}: ${length} chars exceeds max ${maxChars}: ${caption.text}`);
  if (!caption.allowShort && length < minChars) defects.push(`${caption.id}: short split tail ${length} chars: ${caption.text}`);
  if (!caption.allowShort && Number(caption.duration) < minDuration) defects.push(`${caption.id}: split fragment lasts only ${caption.duration}s: ${caption.text}`);
}

const bodyLines = String(audio.body?.text ?? "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
for (const [index, line] of bodyLines.entries()) {
  if (!/[。！？.!?]$/u.test(line)) defects.push(`narration body line ${index + 1} ends without sentence punctuation: ${line.slice(-24)}`);
}

if (defects.length > 0) {
  console.error(JSON.stringify({ status: "failed", defects: defects.slice(0, 50) }, null, 2));
  process.exit(1);
}

console.log(JSON.stringify({
  status: "validated",
  captions: audio.captions?.length ?? 0,
  minChars,
  maxChars,
  minDuration,
  shortCompleteUtterances: (audio.captions ?? []).filter((caption) => caption.allowShort).length,
  splitTailDefects: 0,
  narrationLines: bodyLines.length,
}, null, 2));
