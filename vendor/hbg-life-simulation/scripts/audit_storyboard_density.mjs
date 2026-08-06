#!/usr/bin/env node
import fs from "node:fs";

const args = process.argv.slice(2);
const reportOnly = args.includes("--report-only");
const positional = args.filter((arg) => !arg.startsWith("--"));
const [storyboardPath, warnArg = "12", errorArg = "16"] = positional;

if (!storyboardPath) {
  console.error("usage: audit_storyboard_density.mjs STORYBOARD.json [WARN_SECONDS=12] [ERROR_SECONDS=16] [--report-only]");
  process.exit(2);
}

const warnSeconds = Number(warnArg);
const errorSeconds = Number(errorArg);
const storyboard = JSON.parse(fs.readFileSync(storyboardPath, "utf8"));

const rows = storyboard.map((scene, index) => {
  const nextStart = storyboard[index + 1]?.start;
  const duration = Number.isFinite(scene.duration)
    ? Number(scene.duration)
    : Number.isFinite(nextStart)
      ? Number(nextStart) - Number(scene.start)
      : NaN;
  const severity = duration > errorSeconds ? "ERROR" : duration > warnSeconds ? "WARN" : "OK";
  return { ...scene, duration, severity };
});

const findings = rows.filter((row) => row.severity !== "OK");
for (const row of findings.sort((a, b) => b.duration - a.duration)) {
  console.log([
    row.severity,
    row.id ?? "unknown",
    `${row.duration.toFixed(3)}s`,
    `ch${row.chapter ?? "?"}`,
    row.cue ?? row.visual ?? "",
  ].join("\t"));
}

const errors = findings.filter((row) => row.severity === "ERROR").length;
const warnings = findings.length - errors;
const average = rows.reduce((sum, row) => sum + row.duration, 0) / rows.length;
console.log(JSON.stringify({ scenes: rows.length, averageDuration: Number(average.toFixed(3)), warnings, errors }, null, 2));

if (!reportOnly && errors > 0) process.exit(1);
