import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { loadProjectSpec, projectRelative } from "./project_config.mjs";

const root = process.cwd();
const spec = loadProjectSpec(root);
const style = JSON.parse(fs.readFileSync(path.join(root, "HBG_STYLE.json"), "utf8"));
const scriptPath = path.join(root, "SCRIPT.md");
const storyboardBasePath = path.join(root, "STORYBOARD_BASE.json");
const storyboardPath = path.join(root, "STORYBOARD.json");
const audioDir = path.join(root, "assets/audio");
const openingAudioDir = path.join(audioDir, "opening");
fs.mkdirSync(audioDir, { recursive: true });
fs.mkdirSync(openingAudioDir, { recursive: true });

const narration = spec.narration ?? {};
const VOICE = narration.voice;
if (!VOICE) throw new Error("PROJECT_SPEC.narration.voice is required; choose the voice from the current story rather than a builder default");
const BODY_RATE = narration.bodyRate ?? "+0%";
const LEAD_RATE = narration.leadRate ?? BODY_RATE;
const REVEAL_RATE = narration.revealRate ?? BODY_RATE;
const PITCH = narration.pitch ?? "+0Hz";
const LEAD_START = Number(narration.leadStart ?? 0.05);
const FLASH_GAP_AFTER_LEAD = Number(narration.flashGapAfterLead ?? 0.02);
const FLASH_DURATION = Number(narration.flashDuration ?? 1.667);
const REVEAL_HOLD = Number(narration.revealHold ?? 0.12);
const BODY_GAP = Number(narration.bodyGap ?? 0.22);
const CAPTION_MAX_CHARS = Number(narration.captionMaxChars ?? 14);
const CAPTION_MIN_CHARS = Number(narration.captionMinChars ?? 6);
const CAPTION_MIN_DURATION = Number(narration.captionMinDuration ?? 0.55);
const leadText = narration.leadText ?? "今天体验的人生副本是。";
const revealText = narration.revealText ?? `${spec.title}。`;
const narrationOutputRelative = spec.audio?.narrationOutput ?? "assets/audio/narration.m4a";
const flashMediaRelative = spec.opening?.flashMedia;
if (!flashMediaRelative) throw new Error("PROJECT_SPEC.opening.flashMedia is required");

const source = fs.readFileSync(scriptPath, "utf8");
const chapterRegex = /^##\s+第[^\n｜]+章｜(.+)$/gm;
const matches = [...source.matchAll(chapterRegex)];
const chapters = matches.map((match, index) => {
  const bodyStart = match.index + match[0].length;
  const bodyEnd = matches[index + 1]?.index ?? source.length;
  // SCRIPT.md line breaks are editorial formatting, not spoken pauses. Joining
  // without whitespace prevents Edge TTS from splitting a word, number, or
  // sentence merely because the source wrapped onto another line.
  const text = source.slice(bodyStart, bodyEnd).split("\n").map((line) => line.trim()).filter(Boolean).join("");
  return { index: index + 1, title: match[1].trim(), text };
});
if (chapters.length !== spec.source.chapters.length) {
  throw new Error(`Expected ${spec.source.chapters.length} chapters from PROJECT_SPEC, found ${chapters.length} in SCRIPT.md`);
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${command} failed with status ${result.status}`);
}

function probeDuration(file) {
  const result = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file], { encoding: "utf8" });
  if (result.status !== 0) throw new Error(`ffprobe failed for ${file}`);
  return Number(result.stdout.trim());
}

function generateEdgeTts(id, text, targetDir, rate) {
  const txt = path.join(targetDir, `${id}.txt`);
  const mp3 = path.join(targetDir, `${id}.mp3`);
  const vtt = path.join(targetDir, `${id}.vtt`);
  const wav = path.join(targetDir, `${id}.wav`);
  const settings = path.join(targetDir, `${id}.settings.json`);
  const expectedText = `${text}\n`;
  const expectedSettings = `${JSON.stringify({ voice: VOICE, rate, pitch: PITCH }, null, 2)}\n`;
  const hasContent = (file) => fs.existsSync(file) && fs.statSync(file).size > 0;
  const reusable = fs.existsSync(txt) && hasContent(mp3) && hasContent(vtt) && hasContent(wav) && fs.existsSync(settings)
    && fs.readFileSync(txt, "utf8") === expectedText
    && fs.readFileSync(settings, "utf8") === expectedSettings;
  if (!reusable) {
    fs.writeFileSync(txt, expectedText);
    fs.writeFileSync(settings, expectedSettings);
    run("python3", ["-m", "edge_tts", "--voice", VOICE, `--rate=${rate}`, `--pitch=${PITCH}`, "--file", txt, "--write-media", mp3, "--write-subtitles", vtt]);
    run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-i", mp3, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", wav]);
  }
  return { txt, mp3, vtt, wav, duration: probeDuration(wav) };
}

function parseTimestamp(value) {
  const match = value.trim().match(/^(\d+):(\d{2}):(\d{2})[,.](\d{3})$/);
  if (!match) throw new Error(`Invalid subtitle timestamp: ${value}`);
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[4]) / 1000;
}

function parseSubtitleFile(file) {
  return fs.readFileSync(file, "utf8").replaceAll("\r", "").trim().split(/\n{2,}/).map((block) => {
    const lines = block.split("\n").filter(Boolean);
    const timingIndex = lines.findIndex((line) => line.includes(" --> "));
    if (timingIndex < 0) return null;
    const [start, end] = lines[timingIndex].split(" --> ");
    return { start: parseTimestamp(start), end: parseTimestamp(end), text: lines.slice(timingIndex + 1).join(" ").trim() };
  }).filter(Boolean);
}

function normalizeForMatch(value) {
  return String(value).normalize("NFKC").replace(/[\s“”‘’"'，。！？；：、,.!?;:…—-]/g, "").toLowerCase();
}

function findCueLocation(cues, needle, fromIndex) {
  const normalizedNeedle = normalizeForMatch(needle);
  for (let index = fromIndex; index < cues.length; index += 1) {
    const normalizedText = normalizeForMatch(cues[index].text);
    const position = normalizedText.indexOf(normalizedNeedle);
    if (position >= 0) {
      const ratio = normalizedText.length > 0 ? position / normalizedText.length : 0;
      return { index, time: cues[index].start + (cues[index].end - cues[index].start) * ratio };
    }
  }
  for (let index = fromIndex; index < cues.length; index += 1) {
    let combined = "";
    for (let lookahead = index; lookahead < Math.min(cues.length, index + 4); lookahead += 1) {
      combined += normalizeForMatch(cues[lookahead].text);
      const position = combined.indexOf(normalizedNeedle);
      if (position < 0) continue;
      let remaining = position;
      for (let mapped = index; mapped <= lookahead; mapped += 1) {
        const mappedText = normalizeForMatch(cues[mapped].text);
        if (remaining <= mappedText.length) {
          const ratio = mappedText.length > 0 ? remaining / mappedText.length : 0;
          return { index: mapped, time: cues[mapped].start + (cues[mapped].end - cues[mapped].start) * ratio };
        }
        remaining -= mappedText.length;
      }
      return { index, time: cues[index].start };
    }
  }
  return null;
}

const captionSegmenter = new Intl.Segmenter("zh-CN", { granularity: "word" });
const continuationWords = new Set(["起来", "下去", "出来", "进去", "回去", "过来", "过去", "清楚", "明白"]);
const numericMeasureWords = new Set(["分", "秒", "点", "元", "块", "期", "月", "年", "岁", "天", "日", "号", "次", "成", "折"]);
const punctuationOnly = /^[，。！？；：、,.!?;:…—-]+$/u;
const chineseNumberOnly = /^[零〇一二三四五六七八九十百千万亿两点]+$/u;

function captionLength(value) {
  return normalizeForMatch(value).length;
}

function tokenizeCaptionText(value) {
  const tokens = [];
  for (const item of captionSegmenter.segment(value)) {
    const token = item.segment.trim();
    if (!token) continue;
    if (punctuationOnly.test(token) && tokens.length > 0) {
      tokens[tokens.length - 1] += token;
      continue;
    }
    if (tokens.length > 0 && chineseNumberOnly.test(token) && chineseNumberOnly.test(tokens.at(-1))) {
      tokens[tokens.length - 1] += token;
      continue;
    }
    if (tokens.length > 0 && continuationWords.has(token)) {
      tokens[tokens.length - 1] += token;
      continue;
    }
    if (tokens.length > 0 && numericMeasureWords.has(token) && chineseNumberOnly.test(tokens.at(-1))) {
      tokens[tokens.length - 1] += token;
      continue;
    }
    tokens.push(token);
  }
  return tokens;
}

function splitOversizedToken(token) {
  const chars = [...token];
  const count = Math.ceil(chars.length / CAPTION_MAX_CHARS);
  const base = Math.floor(chars.length / count);
  const remainder = chars.length % count;
  const parts = [];
  let cursor = 0;
  for (let index = 0; index < count; index += 1) {
    const size = base + (index < remainder ? 1 : 0);
    parts.push(chars.slice(cursor, cursor + size).join(""));
    cursor += size;
  }
  return parts;
}

function splitLongSemanticUnit(unit) {
  let tokens = tokenizeCaptionText(unit).flatMap((token) => captionLength(token) > CAPTION_MAX_CHARS ? splitOversizedToken(token) : [token]);
  if (tokens.length === 0) return [];
  const n = tokens.length;
  const dp = Array(n + 1).fill(null);
  dp[n] = { cost: 0, parts: [] };
  const target = (CAPTION_MIN_CHARS + CAPTION_MAX_CHARS) / 2;

  for (let start = n - 1; start >= 0; start -= 1) {
    let raw = "";
    for (let end = start + 1; end <= n; end += 1) {
      raw += tokens[end - 1];
      const length = captionLength(raw);
      if (length > CAPTION_MAX_CHARS) break;
      if (!dp[end]) continue;
      const shortPenalty = length < CAPTION_MIN_CHARS ? 80 + ((CAPTION_MIN_CHARS - length) ** 2) * 20 : 0;
      const boundaryBonus = /[，、：,]$/u.test(raw) ? -5 : /[。！？；.!?;]$/u.test(raw) ? -9 : 0;
      const cost = ((length - target) ** 2) + shortPenalty + boundaryBonus + dp[end].cost;
      if (!dp[start] || cost < dp[start].cost) dp[start] = { cost, parts: [raw, ...dp[end].parts] };
    }
  }

  if (!dp[0]) throw new Error(`Unable to split caption semantic unit within ${CAPTION_MAX_CHARS} characters: ${unit}`);
  const parts = dp[0].parts;
  const invalid = parts.find((part) => captionLength(part) < CAPTION_MIN_CHARS);
  if (invalid) throw new Error(`Caption split left a short tail (${captionLength(invalid)} chars): ${parts.join(" / ")}`);
  return parts;
}

function splitCaptionText(text) {
  const normalized = text.replaceAll("\n", "").trim();
  const normalizedLength = captionLength(normalized);
  if (normalizedLength <= CAPTION_MAX_CHARS) return [{ text: normalized, allowShort: normalizedLength < CAPTION_MIN_CHARS }];

  const punctuationUnits = normalized.match(/[^，。！？；：、,.!?;:]+[，。！？；：、,.!?;:]?/gu) ?? [normalized];
  const pieces = punctuationUnits.flatMap((rawUnit) => {
    const unit = rawUnit.trim();
    if (!unit) return [];
    return captionLength(unit) <= CAPTION_MAX_CHARS ? [unit] : splitLongSemanticUnit(unit);
  });
  const target = (CAPTION_MIN_CHARS + CAPTION_MAX_CHARS) / 2;
  const packed = [];
  for (let index = 0; index < pieces.length; index += 1) {
    let current = pieces[index];
    while (index + 1 < pieces.length) {
      const next = pieces[index + 1];
      const combined = `${current}${next}`;
      if (captionLength(combined) > CAPTION_MAX_CHARS) break;
      if (captionLength(current) >= CAPTION_MIN_CHARS && captionLength(next) >= CAPTION_MIN_CHARS && captionLength(combined) > target) break;
      current = combined;
      index += 1;
    }
    packed.push(current);
  }

  for (let index = packed.length - 1; index >= 0; index -= 1) {
    if (captionLength(packed[index]) >= CAPTION_MIN_CHARS) continue;
    if (index > 0) {
      const combined = `${packed[index - 1]}${packed[index]}`;
      if (captionLength(combined) <= CAPTION_MAX_CHARS) {
        packed.splice(index - 1, 2, combined);
        continue;
      }
      const balanced = splitLongSemanticUnit(combined);
      packed.splice(index - 1, 2, ...balanced);
      continue;
    }
    if (packed.length > 1) {
      const combined = `${packed[0]}${packed[1]}`;
      const balanced = captionLength(combined) <= CAPTION_MAX_CHARS ? [combined] : splitLongSemanticUnit(combined);
      packed.splice(0, 2, ...balanced);
    }
  }

  const invalid = packed.find((part) => captionLength(part) < CAPTION_MIN_CHARS || captionLength(part) > CAPTION_MAX_CHARS);
  if (invalid) throw new Error(`Caption semantic packing failed: ${packed.join(" / ")}`);
  return packed.map((part) => ({ text: part, allowShort: false }));
}

function cleanCaptionDisplayText(value) {
  return String(value).replace(/[“”‘’"']/gu, "").replace(/[，。！？；：、,.!?;:…—-]+$/gu, "").trim();
}

function buildCaptionCues(cues) {
  const captions = [];
  let previousEnd = 0;
  for (const cue of cues) {
    const start = Math.max(cue.start, previousEnd);
    const end = Math.max(cue.end, start + 0.24);
    const parts = splitCaptionText(cue.text)
      .map((part) => ({ ...part, text: cleanCaptionDisplayText(part.text) }))
      .filter((part) => part.text && normalizeForMatch(part.text).length > 0);
    if (parts.length === 0) { previousEnd = end; continue; }
    const weights = parts.map((part) => Math.max(1, normalizeForMatch(part.text).length));
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
    let cursor = start;
    let consumedWeight = 0;
    parts.forEach((part, index) => {
      consumedWeight += weights[index];
      const partEnd = index === parts.length - 1 ? end : start + ((end - start) * consumedWeight) / totalWeight;
      const duration = Number((partEnd - cursor).toFixed(3));
      captions.push({ id: `caption-${String(captions.length + 1).padStart(4, "0")}`, start: Number(cursor.toFixed(3)), end: Number(partEnd.toFixed(3)), duration, text: part.text, allowShort: part.allowShort });
      cursor = partEnd;
    });
    previousEnd = end;
  }
  return captions;
}

const lead = generateEdgeTts("lead-natural", leadText, openingAudioDir, LEAD_RATE);
const reveal = generateEdgeTts("reveal-natural", revealText, openingAudioDir, REVEAL_RATE);
const flashStart = LEAD_START + lead.duration + FLASH_GAP_AFTER_LEAD;
const revealStart = flashStart + FLASH_DURATION + REVEAL_HOLD;
const bodyStart = revealStart + reveal.duration + BODY_GAP;
const bodyText = chapters.map((chapter) => chapter.text).join("\n");
const body = generateEdgeTts("narration-full", bodyText, audioDir, BODY_RATE);
const bodyCues = parseSubtitleFile(body.vtt);
if (bodyCues.length === 0) throw new Error("Full-body Edge TTS returned no subtitle cues");
const captions = buildCaptionCues(bodyCues);
const captionDefects = captions.filter((caption) => {
  const length = captionLength(caption.text);
  return length > CAPTION_MAX_CHARS || (!caption.allowShort && length < CAPTION_MIN_CHARS) || (!caption.allowShort && caption.duration < CAPTION_MIN_DURATION);
});
if (captionDefects.length > 0) {
  throw new Error(`Caption semantic audit failed:\n${captionDefects.slice(0, 20).map((caption) => `${caption.id}: ${caption.duration}s / ${captionLength(caption.text)} chars / ${caption.text}`).join("\n")}`);
}

const narrationOutput = path.resolve(root, narrationOutputRelative);
fs.mkdirSync(path.dirname(narrationOutput), { recursive: true });
run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-i", body.wav, "-c:a", "aac", "-b:a", "192k", narrationOutput]);

const storyboardBase = JSON.parse(fs.readFileSync(storyboardBasePath, "utf8"));
const cueMatches = new Map();
let searchIndex = 0;
const missingCues = [];
for (const scene of storyboardBase) {
  const location = findCueLocation(captions, scene.cue, searchIndex);
  if (!location) { missingCues.push(`${scene.id}: ${scene.cue}`); continue; }
  cueMatches.set(scene.id, location);
  searchIndex = location.index;
}
if (missingCues.length > 0) throw new Error(`Unable to align storyboard cues to full narration:\n${missingCues.join("\n")}`);

const chapterText = new Map(chapters.map((chapter) => [chapter.index, chapter.text]));
const storyboard = storyboardBase.map((scene, index) => {
  const location = cueMatches.get(scene.id);
  const relativeStart = index === 0 ? 0 : location.time;
  const nextScene = storyboardBase[index + 1];
  const nextRelativeStart = nextScene ? cueMatches.get(nextScene.id).time : body.duration;
  if (nextRelativeStart <= relativeStart) throw new Error(`Non-increasing scene timing for ${scene.id}: ${relativeStart} -> ${nextRelativeStart}`);
  const start = bodyStart + relativeStart;
  const end = bodyStart + nextRelativeStart;
  return { ...scene, cueTime: Number(relativeStart.toFixed(3)), narration: chapterText.get(scene.chapter), start: Number(start.toFixed(3)), duration: Number((end - start).toFixed(3)), end: Number(end.toFixed(3)) };
});

const chapterMeta = chapters.map((chapter) => {
  const firstScene = storyboard.find((scene) => scene.chapter === chapter.index);
  const nextChapterScene = storyboard.find((scene) => scene.chapter === chapter.index + 1);
  if (!firstScene) throw new Error(`Storyboard has no scene for chapter ${chapter.index}`);
  const start = firstScene.start;
  const end = nextChapterScene?.start ?? bodyStart + body.duration;
  return { id: `ch${String(chapter.index).padStart(2, "0")}`, chapter: chapter.index, title: chapter.title, text: chapter.text, start: Number(start.toFixed(3)), end: Number(end.toFixed(3)), duration: Number((end - start).toFixed(3)) };
});

const narrationDuration = body.duration;
const totalDuration = bodyStart + narrationDuration;
const opening = {
  previewVideo: style.opening.approvedVideo,
  lead: { text: leadText, path: projectRelative(root, lead.mp3), rate: LEAD_RATE, start: LEAD_START, duration: lead.duration, end: LEAD_START + lead.duration },
  flash: { video: flashMediaRelative, audio: flashMediaRelative, start: flashStart, duration: FLASH_DURATION, end: flashStart + FLASH_DURATION },
  reveal: { text: revealText, path: projectRelative(root, reveal.mp3), rate: REVEAL_RATE, start: revealStart, duration: reveal.duration, end: revealStart + reveal.duration },
  bodyStart,
};

fs.writeFileSync(path.join(root, "audio_meta.json"), `${JSON.stringify({
  provider: "Edge TTS",
  voice: VOICE,
  rate: BODY_RATE,
  pitch: PITCH,
  syncMode: "full-body-vtt-master",
  openingDuration: bodyStart,
  opening,
  body: { text: bodyText, path: narrationOutputRelative, sourcePath: projectRelative(root, body.mp3), vtt: projectRelative(root, body.vtt), duration: narrationDuration },
  bgm: { path: spec.audio?.bgmLooped },
  chapters: chapterMeta,
  captions,
  captionAudit: { minChars: CAPTION_MIN_CHARS, maxChars: CAPTION_MAX_CHARS, minDuration: CAPTION_MIN_DURATION, defects: 0 },
  narrationDuration,
  totalDuration,
}, null, 2)}\n`);
fs.writeFileSync(storyboardPath, `${JSON.stringify(storyboard, null, 2)}\n`);
console.log(JSON.stringify({ opening, chapters: chapterMeta.map(({ chapter, title, duration }) => ({ chapter, title, duration })), captions: captions.length, narrationDuration, totalDuration, storyboardScenes: storyboard.length }, null, 2));
