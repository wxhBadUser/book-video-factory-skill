#!/usr/bin/env node
/** Build the opening as static stills joined by hard cuts (no image motion). */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const [workspaceArg, outputArg] = process.argv.slice(2);
if (!workspaceArg || !outputArg) throw new Error("usage: render_static_opening_preview.mjs <workspace> <output>");
const root = path.resolve(workspaceArg);
const output = path.resolve(outputArg);
const read = (relative) => JSON.parse(fs.readFileSync(path.join(root, relative), "utf8"));
const style = read("HBG_STYLE.json");
const spec = read("PROJECT_SPEC.json");
const audio = read("audio_meta.json");
const width = Number(style.canvas?.width), height = Number(style.canvas?.height), fps = Number(style.canvas?.fps);
const opening = audio.opening;
if (!Number.isInteger(width) || !Number.isInteger(height) || !Number.isFinite(fps) || !opening) {
  throw new Error("static opening requires HBG canvas and audio opening metadata");
}
const resolve = (relative, label) => {
  if (typeof relative !== "string" || !relative) throw new Error(`${label} path is invalid`);
  const resolved = path.resolve(root, relative);
  if (!resolved.startsWith(root + path.sep) || !fs.existsSync(resolved)) throw new Error(`${label} is missing: ${relative}`);
  return resolved;
};
const finalImage = resolve(spec.opening?.finalImage, "opening final image");
const flashImages = (spec.opening?.flashLives ?? []).map((item, index) => resolve(item.asset, `opening flash image ${index + 1}`));
if (!flashImages.length) throw new Error("static opening requires at least one flash image");
const lead = resolve(opening.lead?.path, "lead audio");
const flash = resolve(opening.flash?.audio, "flash audio");
const reveal = resolve(opening.reveal?.path, "reveal audio");
const bgm = resolve(spec.audio?.bgmSource, "BGM source");
const total = Number(opening.bodyStart);
if (!Number.isFinite(total) || total <= 0) throw new Error("opening bodyStart is invalid");
const flashStart = Number(opening.flash?.start), flashDuration = Number(opening.flash?.duration);
if (!Number.isFinite(flashStart) || !Number.isFinite(flashDuration) || flashDuration <= 0) throw new Error("opening flash timing is invalid");

const temporary = fs.mkdtempSync(path.join(root, ".static-opening-"));
const run = (args, label) => {
  const result = spawnSync("ffmpeg", args, { cwd: root, stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${label} failed with exit ${result.status}`);
};
const seconds = (value) => Math.max(1 / fps, Number(value.toFixed(3)));
try {
  const stills = [{ image: finalImage, duration: seconds(flashStart) }];
  const perFlash = flashDuration / flashImages.length;
  for (const image of flashImages) stills.push({ image, duration: seconds(perFlash) });
  const tail = total - flashStart - flashDuration;
  if (tail > 0) stills.push({ image: finalImage, duration: seconds(tail) });
  const segments = [];
  for (const [index, still] of stills.entries()) {
    const segment = path.join(temporary, `segment-${String(index).padStart(3, "0")}.mp4`);
    run([
      "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", still.image,
      "-vf", `scale=${width}:${height}:force_original_aspect_ratio=increase,crop=${width}:${height},fps=${fps},format=yuv420p`,
      "-t", String(still.duration), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
      "-pix_fmt", "yuv420p", "-r", String(fps), segment,
    ], `static opening segment ${index}`);
    segments.push(segment);
  }
  const list = path.join(temporary, "segments.txt");
  fs.writeFileSync(list, segments.map((item) => `file '${item.replaceAll("'", "'\\''")}'`).join("\n") + "\n", "utf8");
  const video = path.join(temporary, "video.mp4");
  run(["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", list, "-c", "copy", video], "static opening concat");
  const filter = [
    `[1:a]adelay=${Math.round(Number(opening.lead?.start || 0) * 1000)}:all=1[lead]`,
    `[2:a]atrim=0:${total},volume=${Number(style.audio?.bgmVolume || 0.22)}[bgm]`,
    `[3:a]atrim=0:${flashDuration},adelay=${Math.round(flashStart * 1000)}:all=1[flash]`,
    `[4:a]adelay=${Math.round(Number(opening.reveal?.start || 0) * 1000)}:all=1[reveal]`,
    `[lead][bgm][flash][reveal]amix=inputs=4:duration=longest:normalize=0,atrim=0:${total}[a]`,
  ].join(";");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  run([
    "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-i", lead,
    "-stream_loop", "-1", "-i", bgm, "-i", flash, "-i", reveal,
    "-filter_complex", filter, "-map", "0:v", "-map", "[a]", "-t", String(total),
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", String(fps),
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", output,
  ], "static opening assembly");
  if (!fs.existsSync(output) || fs.statSync(output).size === 0) throw new Error("static opening output is empty");
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
