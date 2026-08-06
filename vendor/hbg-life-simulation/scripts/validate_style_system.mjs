import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const [projectArg = "."] = process.argv.slice(2);
const projectDir = path.resolve(projectArg);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectStylePath = path.join(projectDir, "HBG_STYLE.json");

const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));
const projectStyle = fs.existsSync(projectStylePath) ? readJson(projectStylePath) : {};
const inferredOrientation = projectStyle.orientation ?? (projectStyle.canvas?.width < projectStyle.canvas?.height ? "portrait" : "landscape");
const defaultStylePath = path.resolve(scriptDir, `../assets/hbg-style-${inferredOrientation}.json`);
const defaults = readJson(defaultStylePath);
const style = {
  ...defaults,
  ...projectStyle,
  canvas: { ...defaults.canvas, ...projectStyle.canvas },
  captions: { ...defaults.captions, ...projectStyle.captions },
  opening: {
    ...defaults.opening,
    ...projectStyle.opening,
    layout: { ...defaults.opening.layout, ...projectStyle.opening?.layout },
  },
  chapters: { ...defaults.chapters, ...projectStyle.chapters },
  audio: { ...defaults.audio, ...projectStyle.audio },
};
const audio = readJson(path.join(projectDir, "audio_meta.json"));
const spec = readJson(path.join(projectDir, "PROJECT_SPEC.json"));

const fail = (message) => {
  console.error(JSON.stringify({ status: "failed", message }, null, 2));
  process.exit(1);
};
const { width, height, fps } = style.canvas;
const orientation = width < height ? "portrait" : "landscape";
if (!(Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0 && width !== height)) fail("canvas must be a positive non-square resolution");
if (!(Number.isFinite(fps) && fps > 0)) fail("canvas fps must be positive");
if (!new Set(["landscape", "portrait"]).has(style.orientation)) fail("style.orientation must be landscape or portrait");
if (style.orientation !== orientation) fail(`style.orientation=${style.orientation} contradicts the ${width}x${height} canvas (${orientation})`);

const finitePositive = (value) => Number.isFinite(Number(value)) && Number(value) > 0;
const openingLayoutKeys = ["leadWidth", "leadFontSize", "flashLabelWidth", "flashLabelFontSize", "titleCardWidth", "primaryTitleFontSize", "secondaryTitleFontSize", "titleTopPercent"];
for (const key of openingLayoutKeys) if (!finitePositive(style.opening.layout?.[key])) fail(`opening.layout.${key} must be a positive number`);
const chapterStyleKeys = ["left", "top", "maxWidth", "kickerFontSize", "titleFontSize", "boxPadding"];
for (const key of chapterStyleKeys) if (!finitePositive(style.chapters?.[key])) fail(`chapters.${key} must be a positive number`);
for (const key of ["fontFamily", "textColor", "accentColor", "backgroundRgba", "assBoxColor"]) if (!style.chapters?.[key]) fail(`chapters.${key} is required`);
if (style.opening.layout.leadWidth > width || style.opening.layout.flashLabelWidth > width || style.opening.layout.titleCardWidth > width) fail("opening text widths must fit inside the selected canvas");
if (style.chapters.maxWidth > width) fail("chapter maxWidth must fit inside the selected canvas");

const captionBottomRatio = style.captions.bottom / height;
const captionFontRatio = style.captions.fontSize / width;
const safeRanges = orientation === "portrait"
  ? { bottomMin: 0.12, bottomMax: 0.2, fontMin: 0.04, fontMax: 0.05 }
  : { bottomMin: 0.04, bottomMax: 0.1, fontMin: 0.02, fontMax: 0.035 };
if (captionBottomRatio < safeRanges.bottomMin || captionBottomRatio > safeRanges.bottomMax) fail(`caption bottom ratio ${captionBottomRatio.toFixed(3)} is outside the ${orientation} safe baseline ${safeRanges.bottomMin}–${safeRanges.bottomMax}`);
if (captionFontRatio < safeRanges.fontMin || captionFontRatio > safeRanges.fontMax) fail(`caption font ratio ${captionFontRatio.toFixed(3)} is outside the ${orientation} range ${safeRanges.fontMin}–${safeRanges.fontMax}`);
if (!(style.captions.boxPadding >= 8)) fail("ASS caption box padding must be at least 8; zero padding makes the background disappear");

const probeVisualDimensions = (relativePath, label) => {
  const absolutePath = path.resolve(projectDir, relativePath);
  if (!fs.existsSync(absolutePath)) fail(`${label} is missing: ${absolutePath}`);
  const result = spawnSync("ffprobe", ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", absolutePath], { encoding: "utf8" });
  if (result.status !== 0) fail(`ffprobe failed for ${label}: ${relativePath}`);
  const stream = JSON.parse(result.stdout).streams?.[0];
  if (!stream || stream.width !== width || stream.height !== height) fail(`${label} must be native ${width}x${height}; got ${stream?.width}x${stream?.height} (${relativePath})`);
};

for (const [index, life] of (spec.opening?.flashLives ?? []).entries()) {
  if (!life.asset) fail(`opening.flashLives[${index}].asset is required`);
  probeVisualDimensions(life.asset, `opening flash asset ${index + 1}`);
}
if (spec.opening?.finalImage) probeVisualDimensions(spec.opening.finalImage, "selected-life image");

const openingRelative = audio.opening?.previewVideo || style.opening.approvedVideo;
if (!openingRelative) fail("audio_meta.opening.previewVideo or style.opening.approvedVideo is required");
const openingPath = path.resolve(projectDir, openingRelative);
if (!fs.existsSync(openingPath)) fail(`approved opening video is missing: ${openingPath}`);

const probe = spawnSync("ffprobe", [
  "-v", "error", "-select_streams", "v:0",
  "-show_entries", "stream=width,height,r_frame_rate:format=duration",
  "-of", "json", openingPath,
], { encoding: "utf8" });
if (probe.status !== 0) fail(`ffprobe failed for approved opening: ${probe.stderr.trim()}`);
const probed = JSON.parse(probe.stdout);
const stream = probed.streams?.[0];
if (!stream || stream.width !== width || stream.height !== height) fail(`approved opening must be ${width}x${height}; got ${stream?.width}x${stream?.height}`);
const duration = Number(probed.format?.duration);
if (!Number.isFinite(duration) || duration + 0.04 < audio.opening.bodyStart) fail(`approved opening duration ${duration} is shorter than bodyStart ${audio.opening.bodyStart}`);

const frameGray = (seconds) => {
  const result = spawnSync("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-ss", String(seconds), "-i", openingPath,
    "-frames:v", "1", "-vf", "scale=180:320,format=gray", "-f", "rawvideo", "pipe:1",
  ], { encoding: null, maxBuffer: 4 * 1024 * 1024 });
  if (result.status !== 0) fail(`unable to sample approved opening at ${seconds}s`);
  return result.stdout;
};
const meanAbsoluteDelta = (a, b) => {
  if (a.length !== b.length || a.length === 0) fail("opening motion samples have incompatible frame buffers");
  let total = 0;
  for (let i = 0; i < a.length; i += 1) total += Math.abs(a[i] - b[i]);
  return total / a.length;
};

const flashStart = Number(audio.opening.flash.start);
const flashDuration = Number(audio.opening.flash.duration);
const finalStart = flashStart + flashDuration - 0.03;
const bodyStart = Number(audio.opening.bodyStart);
const flashDelta = meanAbsoluteDelta(frameGray(flashStart + 0.03), frameGray(Math.min(flashStart + 0.12, flashStart + flashDuration - 0.03)));
const selectedDelta = meanAbsoluteDelta(frameGray(finalStart + 0.08), frameGray(Math.max(finalStart + 0.12, bodyStart - 0.18)));
if (flashDelta < style.opening.minimumFlashMotionDelta) fail(`flash zoom is not measurable enough: delta=${flashDelta.toFixed(3)}`);
if (selectedDelta < style.opening.minimumSelectedMotionDelta) fail(`selected-life zoom is not measurable enough: delta=${selectedDelta.toFixed(3)}`);

console.log(JSON.stringify({
  status: "validated",
  stylePath: fs.existsSync(projectStylePath) ? projectStylePath : defaultStylePath,
  openingPath,
  canvas: { width, height, fps },
  orientation,
  captionBottomRatio: Number(captionBottomRatio.toFixed(4)),
  captionFontRatio: Number(captionFontRatio.toFixed(4)),
  openingLayout: style.opening.layout,
  chapterLayout: style.chapters,
  flashMotionDelta: Number(flashDelta.toFixed(3)),
  selectedMotionDelta: Number(selectedDelta.toFixed(3)),
}, null, 2));
