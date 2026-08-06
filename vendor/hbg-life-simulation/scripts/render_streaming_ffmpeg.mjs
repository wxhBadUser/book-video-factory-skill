import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { toChineseNumber } from "./project_config.mjs";

const [projectArg = ".", outputArg = "renders/story-streaming.mp4"] = process.argv.slice(2);
const projectDir = path.resolve(projectArg);
const outputPath = path.resolve(projectDir, outputArg);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectStylePath = path.join(projectDir, "HBG_STYLE.json");
const projectStyle = fs.existsSync(projectStylePath) ? JSON.parse(fs.readFileSync(projectStylePath, "utf8")) : {};
const inferredOrientation = projectStyle.orientation ?? (projectStyle.canvas?.width < projectStyle.canvas?.height ? "portrait" : "landscape");
const defaultStyle = JSON.parse(fs.readFileSync(path.resolve(scriptDir, `../assets/hbg-style-${inferredOrientation}.json`), "utf8"));
const style = {
  ...defaultStyle,
  ...projectStyle,
  canvas: { ...defaultStyle.canvas, ...projectStyle.canvas },
  captions: { ...defaultStyle.captions, ...projectStyle.captions },
  opening: {
    ...defaultStyle.opening,
    ...projectStyle.opening,
    layout: { ...defaultStyle.opening.layout, ...projectStyle.opening?.layout },
  },
  chapters: { ...defaultStyle.chapters, ...projectStyle.chapters },
  audio: { ...defaultStyle.audio, ...projectStyle.audio },
};
const fps = Number(process.env.HBG_FPS ?? style.canvas.fps);
const width = Number(process.env.HBG_WIDTH ?? style.canvas.width);
const height = Number(process.env.HBG_HEIGHT ?? style.canvas.height);
const bitrate = process.env.HBG_VIDEO_BITRATE ?? style.canvas.videoBitrate;
const bgmVolume = Number(process.env.HBG_BGM_VOLUME ?? style.audio.bgmVolume);
const captionFontFamily = process.env.HBG_CAPTION_FONT_FAMILY ?? style.captions.fontFamily;
const captionFontSize = Number(process.env.HBG_CAPTION_FONT_SIZE ?? style.captions.fontSize);
const captionMarginV = Number(process.env.HBG_CAPTION_MARGIN_V ?? style.captions.bottom);
const captionBoxPadding = Number(process.env.HBG_CAPTION_BOX_PADDING ?? style.captions.boxPadding);
const captionAssBoxColor = process.env.HBG_CAPTION_ASS_BOX_COLOR ?? style.captions.assBoxColor;
const chapterFontFamily = style.chapters.fontFamily;
const chapterFontSize = Number(style.chapters.titleFontSize);
const chapterMarginL = Number(style.chapters.left);
const chapterMarginV = Number(style.chapters.top);
const chapterBoxPadding = Number(style.chapters.boxPadding);
const chapterAssBoxColor = style.chapters.assBoxColor;
const validateOnly = process.env.HBG_VALIDATE_ONLY === "1";
const keepWork = process.env.HBG_KEEP_STREAM_WORK === "1";

const readJson = (name) => JSON.parse(fs.readFileSync(path.join(projectDir, name), "utf8"));
const storyboard = readJson("STORYBOARD.json");
const audio = readJson("audio_meta.json");
const totalDuration = Number(audio.totalDuration.toFixed(3));
const bodyStart = Number(audio.opening.bodyStart.toFixed(3));
const totalFrames = Math.round(totalDuration * fps);
const openingFrames = Math.round(bodyStart * fps);

const resolveMedia = (envName, ...candidates) => {
  const configured = process.env[envName];
  const values = configured ? [configured] : candidates.filter(Boolean);
  for (const value of values) {
    const resolved = path.resolve(projectDir, value);
    if (fs.existsSync(resolved)) return resolved;
  }
  throw new Error(`${envName} could not be resolved from: ${values.join(", ")}`);
};

const openingVideo = resolveMedia(
  "HBG_OPENING_VIDEO",
  audio.opening.previewVideo,
  style.opening.approvedVideo,
);
const lead = resolveMedia("HBG_LEAD_AUDIO", audio.opening.lead.path);
const bgm = resolveMedia("HBG_BGM_AUDIO", audio.bgm?.path);
const flash = resolveMedia("HBG_FLASH_AUDIO", audio.opening.flash.audio);
const reveal = resolveMedia("HBG_REVEAL_AUDIO", audio.opening.reveal.path);
const narration = resolveMedia("HBG_NARRATION_AUDIO", audio.body?.path);

if (!Number.isFinite(fps) || fps <= 0 || !Number.isInteger(width) || !Number.isInteger(height)) {
  throw new Error("HBG_FPS, HBG_WIDTH, and HBG_HEIGHT must be positive numbers");
}
if (!Number.isFinite(bgmVolume) || bgmVolume < 0) throw new Error("HBG_BGM_VOLUME must be a non-negative number");
if (![captionFontSize, captionMarginV, captionBoxPadding, chapterFontSize, chapterMarginL, chapterMarginV, chapterBoxPadding].every(Number.isFinite)) throw new Error("caption/chapter style values must be finite numbers");
if (!Array.isArray(storyboard) || storyboard.length === 0) throw new Error("STORYBOARD.json has no scenes");

if (process.env.HBG_SKIP_STYLE_VALIDATION !== "1") {
  const styleValidation = spawnSync(process.execPath, [path.join(scriptDir, "validate_style_system.mjs"), projectDir], { cwd: projectDir, stdio: "inherit" });
  if (styleValidation.status !== 0) throw new Error(`style system validation failed with exit ${styleValidation.status}`);
}

for (const scene of storyboard) {
  const source = path.join(projectDir, scene.asset);
  if (!fs.existsSync(source)) throw new Error(`missing scene asset: ${source}`);
}

const workDir = path.join(projectDir, "renders", `ffmpeg-work-${path.basename(outputPath, path.extname(outputPath))}`);
if (validateOnly) {
  console.log(JSON.stringify({
    status: "validated",
    projectDir,
    outputPath,
    totalDuration,
    totalFrames,
    openingFrames,
    scenes: storyboard.length,
    openingVideo,
    narration,
    bgm,
    bgmVolume,
  }, null, 2));
  process.exit(0);
}

if (fs.existsSync(workDir)) throw new Error(`work directory already exists: ${workDir}`);
fs.mkdirSync(path.join(workDir, "segments"), { recursive: true });
fs.mkdirSync(path.dirname(outputPath), { recursive: true });

const run = (command, args, label) => {
  const result = spawnSync(command, args, { cwd: projectDir, stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${label} failed with exit ${result.status}`);
};

const ffmpegVideoArgs = () => process.platform === "darwin"
  ? ["-c:v", "h264_videotoolbox", "-allow_sw", "1", "-b:v", bitrate, "-maxrate", "10M", "-bufsize", "20M", "-tag:v", "avc1"]
  : ["-c:v", "libx264", "-preset", "medium", "-crf", "18"];

const motionFilter = (motion, frames) => {
  const denominator = Math.max(1, frames - 1);
  const t = `on/${denominator}`;
  const states = {
    "zoom-in": { z: `1+0.13*${t}`, x: `(iw-iw/zoom)*(0.30+0.25*${t})`, y: `(ih-ih/zoom)*(0.55-0.10*${t})` },
    "zoom-out": { z: `1.13-0.13*${t}`, x: `(iw-iw/zoom)*(0.55-0.20*${t})`, y: `(ih-ih/zoom)*(0.40+0.10*${t})` },
    "pan-left": { z: "1.10", x: `(iw-iw/zoom)*(0.80-0.60*${t})`, y: `(ih-ih/zoom)*0.50` },
    "pan-right": { z: "1.10", x: `(iw-iw/zoom)*(0.20+0.60*${t})`, y: `(ih-ih/zoom)*0.50` },
    hold: { z: `1.01+0.025*${t}`, x: `(iw-iw/zoom)*0.50`, y: `(ih-ih/zoom)*0.50` },
  };
  const state = states[motion] ?? states.hold;
  return [
    `scale=${width * 2}:${height * 2}:force_original_aspect_ratio=increase`,
    `crop=${width * 2}:${height * 2}`,
    `zoompan=z='${state.z}':x='${state.x}':y='${state.y}':d=${frames}:s=${width}x${height}:fps=${fps}`,
    "format=yuv420p",
  ].join(",");
};

const segmentPaths = [];
for (let index = 0; index < storyboard.length; index += 1) {
  const scene = storyboard[index];
  const nextStart = storyboard[index + 1]?.start ?? totalDuration;
  const frames = Math.round(nextStart * fps) - Math.round(scene.start * fps);
  if (frames < 1) throw new Error(`scene ${scene.id} has invalid frame count ${frames}`);
  const segment = path.join(workDir, "segments", `${scene.id}.mp4`);
  segmentPaths.push(segment);
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", path.join(projectDir, scene.asset),
    "-vf", motionFilter(scene.motion, frames), "-frames:v", String(frames), "-r", String(fps), "-an",
    ...ffmpegVideoArgs(), "-g", "60", "-pix_fmt", "yuv420p", segment,
  ], `scene ${scene.id}`);
  if ((index + 1) % 10 === 0 || index === storyboard.length - 1) console.log(`rendered ${index + 1}/${storyboard.length} scenes`);
}

const concatList = path.join(workDir, "segments.txt");
fs.writeFileSync(concatList, segmentPaths.map((item) => `file '${item.replaceAll("'", "'\\''")}'`).join("\n") + "\n");
const bodyVideo = path.join(workDir, "body.mp4");
run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", concatList, "-c", "copy", "-movflags", "+faststart", bodyVideo], "body concat");

const assTime = (seconds) => {
  const centiseconds = Math.max(0, Math.round(seconds * 100));
  const hours = Math.floor(centiseconds / 360000);
  const minutes = Math.floor((centiseconds % 360000) / 6000);
  const secs = Math.floor((centiseconds % 6000) / 100);
  const cs = centiseconds % 100;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
};
const assEscape = (value) => String(value).replaceAll("\\", "\\\\").replaceAll("{", "\\{").replaceAll("}", "\\}").replaceAll("\n", "\\N");
const assLines = [];
for (const caption of audio.captions ?? []) {
  assLines.push(`Dialogue: 0,${assTime(bodyStart + caption.start)},${assTime(bodyStart + caption.end)},Caption,,0,0,0,,${assEscape(caption.text)}`);
}
for (const chapterMeta of audio.chapters ?? []) {
  const first = storyboard.find((scene) => scene.chapter === chapterMeta.chapter);
  if (!first) continue;
  assLines.push(`Dialogue: 1,${assTime(first.start + 0.12)},${assTime(Math.min(first.start + 3, totalDuration))},Chapter,,0,0,0,,${assEscape(`第${toChineseNumber(chapterMeta.chapter)}幕  ${chapterMeta.title}`)}`);
}

const assPath = path.join(workDir, "captions.ass");
fs.writeFileSync(assPath, `[Script Info]
ScriptType: v4.00+
PlayResX: ${width}
PlayResY: ${height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,${captionFontFamily},${captionFontSize},&H00FFFFFF,&H00FFFFFF,${captionAssBoxColor},${captionAssBoxColor},-1,0,0,0,100,100,1,0,3,${captionBoxPadding},0,2,54,54,${captionMarginV},1
Style: Chapter,${chapterFontFamily},${chapterFontSize},&H00FFF8EF,&H00FFF8EF,${chapterAssBoxColor},${chapterAssBoxColor},-1,0,0,0,100,100,2,0,3,${chapterBoxPadding},0,7,${chapterMarginL},${chapterMarginL},${chapterMarginV},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
${assLines.join("\n")}
`);

const filterAssPath = assPath.replaceAll("\\", "\\\\").replaceAll(":", "\\:").replaceAll("'", "\\'");
const filter = [
  `[0:v]trim=start_frame=0:end_frame=${openingFrames},setpts=N/(${fps}*TB)[openingv]`,
  `[1:v]setpts=N/(${fps}*TB)[bodyv]`,
  `[openingv][bodyv]concat=n=2:v=1:a=0[basev]`,
  `[basev]subtitles=filename='${filterAssPath}'[v]`,
  `[2:a]adelay=${Math.round(audio.opening.lead.start * 1000)}:all=1[lead]`,
  `[3:a]atrim=0:${totalDuration},volume=${bgmVolume}[bgm]`,
  `[4:a]atrim=0:${audio.opening.flash.duration},adelay=${Math.round(audio.opening.flash.start * 1000)}:all=1[flash]`,
  `[5:a]adelay=${Math.round(audio.opening.reveal.start * 1000)}:all=1[reveal]`,
  `[6:a]adelay=${Math.round(bodyStart * 1000)}:all=1[narration]`,
  `[lead][bgm][flash][reveal][narration]amix=inputs=5:duration=longest:normalize=0,atrim=0:${totalDuration}[a]`,
].join(";");

run("ffmpeg", [
  "-hide_banner", "-loglevel", "info", "-y", "-i", openingVideo, "-i", bodyVideo, "-i", lead, "-i", bgm, "-i", flash, "-i", reveal, "-i", narration,
  "-filter_complex", filter, "-map", "[v]", "-map", "[a]", "-r", String(fps), "-t", String(totalDuration),
  ...ffmpegVideoArgs(), "-g", "60", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", outputPath,
], "final assembly");

if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size === 0) throw new Error(`final output missing or empty: ${outputPath}`);
if (!keepWork) fs.rmSync(workDir, { recursive: true, force: true });
console.log(JSON.stringify({ outputPath, totalDuration, totalFrames, openingFrames, scenes: storyboard.length, workDir: keepWork ? workDir : null }, null, 2));
