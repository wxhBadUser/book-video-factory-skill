import fs from "node:fs";
import path from "node:path";
import { loadProjectSpec } from "./project_config.mjs";

const root = process.cwd();
const spec = loadProjectSpec(root);
const storyboardPath = path.join(root, "STORYBOARD_BASE.json");
if (!fs.existsSync(storyboardPath)) {
  throw new Error("STORYBOARD_BASE.json must be authored from semantic story beats before running this validator");
}

const source = JSON.parse(fs.readFileSync(storyboardPath, "utf8"));
if (!Array.isArray(source) || source.length === 0) throw new Error("STORYBOARD_BASE.json must be a non-empty array");
const motionCycle = ["zoom-in", "zoom-out", "pan-left", "pan-right", "hold"];
const seenIds = new Set();
let previousChapter = 0;

const normalized = source.map((scene, index) => {
  const id = scene.id ?? `s${String(index + 1).padStart(3, "0")}`;
  if (seenIds.has(id)) throw new Error(`Duplicate storyboard id: ${id}`);
  seenIds.add(id);
  const chapter = Number(scene.chapter);
  if (!Number.isInteger(chapter) || chapter < 1 || chapter > spec.source.chapters.length) {
    throw new Error(`${id} has invalid chapter ${scene.chapter}; expected 1-${spec.source.chapters.length}`);
  }
  if (chapter < previousChapter) throw new Error(`${id} moves backwards from chapter ${previousChapter} to ${chapter}`);
  previousChapter = chapter;
  if (!scene.cue?.trim()) throw new Error(`${id} is missing cue`);
  if (!(scene.description ?? scene.visual)?.trim()) throw new Error(`${id} is missing description/visual`);
  return {
    ...scene,
    id,
    chapter,
    chapterTitle: spec.source.chapters[chapter - 1].title,
    description: scene.description ?? scene.visual,
    highRisk: Boolean(scene.highRisk),
    asset: scene.asset ?? `assets/generated/scenes/${id}.png`,
    motion: scene.motion ?? motionCycle[index % motionCycle.length],
  };
});

const script = fs.readFileSync(path.join(root, "SCRIPT.md"), "utf8");
let searchFrom = 0;
for (const scene of normalized) {
  const found = script.indexOf(scene.cue, searchFrom);
  if (found < 0) throw new Error(`${scene.id} cue is missing or out of order in SCRIPT.md: ${scene.cue}`);
  searchFrom = found + Math.max(1, scene.cue.length);
}

fs.writeFileSync(storyboardPath, `${JSON.stringify(normalized, null, 2)}\n`);
console.log(JSON.stringify({ scenes: normalized.length, chapters: spec.source.chapters.length, highRisk: normalized.filter((scene) => scene.highRisk).length }, null, 2));
