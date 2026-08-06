import fs from "node:fs";
import path from "node:path";

export const readJson = (file) => JSON.parse(fs.readFileSync(file, "utf8"));

export function loadProjectSpec(root = process.cwd()) {
  const file = path.join(root, "PROJECT_SPEC.json");
  if (!fs.existsSync(file)) throw new Error(`Missing PROJECT_SPEC.json: ${file}`);
  const spec = readJson(file);
  if (!spec.title?.trim()) throw new Error("PROJECT_SPEC.title is required");
  if (!Array.isArray(spec.source?.chapters) || spec.source.chapters.length === 0) {
    throw new Error("PROJECT_SPEC.source.chapters must contain at least one chapter");
  }
  return spec;
}
export function toChineseNumber(value) {
  if (!Number.isInteger(value) || value < 1 || value > 99) throw new Error(`chapter number out of range: ${value}`);
  const digits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"];
  if (value < 10) return digits[value];
  const tens = Math.floor(value / 10);
  const ones = value % 10;
  return `${tens === 1 ? "" : digits[tens]}十${ones === 0 ? "" : digits[ones]}`;
}

export function projectRelative(root, file) {
  return path.relative(root, file).split(path.sep).join("/");
}

export function assertProjectFile(root, relative, label = "project asset") {
  if (!relative) throw new Error(`${label} path is required`);
  const absolute = path.resolve(root, relative);
  if (!fs.existsSync(absolute)) throw new Error(`${label} is missing: ${absolute}`);
  return absolute;
}
