import fs from "node:fs";
import path from "node:path";
import { loadProjectSpec, toChineseNumber } from "./project_config.mjs";

const root = process.cwd();
const spec = loadProjectSpec(root);
const sourcePath = path.join(root, "SCRIPT_SOURCE.md");
if (!fs.existsSync(sourcePath)) throw new Error(`Missing SCRIPT_SOURCE.md: ${sourcePath}`);

const source = fs.readFileSync(sourcePath, "utf8").trim();
const openDelimiter = spec.source.openDelimiter ?? "【";
const closeDelimiter = spec.source.closeDelimiter ?? "】";
const open = source.indexOf(openDelimiter);
const close = source.lastIndexOf(closeDelimiter);
let story = open >= 0 && close > open
  ? source.slice(open + openDelimiter.length, close).trim()
  : source;

const openingSentence = spec.source.openingSentence ?? `今天体验的人生副本是${spec.title}。`;
if (!story.startsWith(openingSentence)) {
  throw new Error(`SCRIPT_SOURCE.md must start with the configured opening sentence: ${openingSentence}`);
}
story = story.slice(openingSentence.length).trim();

const appliedCorrections = [];
for (const correction of spec.source.corrections ?? []) {
  const { from, to, required = true } = correction;
  if (!from || typeof to !== "string") throw new Error("Every source correction requires string from/to values");
  if (!story.includes(from)) {
    if (required) throw new Error(`Required correction source not found: ${from}`);
    continue;
  }
  story = story.replaceAll(from, to);
  appliedCorrections.push({ from, to });
}

const chapters = spec.source.chapters;
const starts = chapters.map((chapter) => {
  const index = story.indexOf(chapter.cue);
  if (index < 0) throw new Error(`Chapter cue not found after corrections: ${chapter.cue}`);
  return index;
});
for (let index = 1; index < starts.length; index += 1) {
  if (starts[index] <= starts[index - 1]) throw new Error(`Chapter cues are not in story order: ${chapters[index].cue}`);
}

const correctionNotes = appliedCorrections.length > 0
  ? appliedCorrections.map(({ from, to }) => `- \`${from}\` → \`${to}\``).join("\n")
  : "- 无";
let output = `# 旁白稿：${spec.title}\n\n## 明显转写修正\n\n${correctionNotes}\n\n`;
chapters.forEach((chapter, index) => {
  const end = starts[index + 1] ?? story.length;
  output += `## 第${toChineseNumber(index + 1)}章｜${chapter.title}\n\n${story.slice(starts[index], end).trim()}\n\n`;
});

fs.writeFileSync(path.join(root, "SCRIPT.md"), output);
console.log(JSON.stringify({ title: spec.title, storyChars: story.length, chapters: chapters.length, corrections: appliedCorrections.length }, null, 2));
