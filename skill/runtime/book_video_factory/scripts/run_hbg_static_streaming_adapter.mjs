#!/usr/bin/env node
/**
 * Execute the locked HBG streaming renderer with a static-body policy.
 *
 * The upstream renderer intentionally animates every still, including its
 * ``hold`` preset.  This adapter never changes the vendored checkout: it
 * copies the two upstream runtime dependencies to a disposable directory and
 * replaces exactly the upstream motion filter with a scale/crop-only filter.
 * HBG's composition build, style validation, caption assembly and final media
 * QA remain the authoritative implementation.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const [vendorArg, projectArg, outputArg] = process.argv.slice(2);
if (!vendorArg || !projectArg || !outputArg) {
  throw new Error("usage: run_hbg_static_streaming_adapter.mjs <vendor-root> <project> <output>");
}

const vendorRoot = path.resolve(vendorArg);
const sourceScript = path.join(vendorRoot, "scripts", "render_streaming_ffmpeg.mjs");
const sourceConfig = path.join(vendorRoot, "scripts", "project_config.mjs");
const sourceAssets = path.join(vendorRoot, "assets");
for (const required of [sourceScript, sourceConfig, sourceAssets]) {
  if (!fs.existsSync(required)) throw new Error(`locked HBG input is missing: ${required}`);
}

const source = fs.readFileSync(sourceScript, "utf8");
const start = source.indexOf("const motionFilter = (motion, frames) => {");
const end = source.indexOf("\n\nconst segmentPaths = [];", start);
if (start < 0 || end < 0) {
  throw new Error("locked HBG renderer motion-filter shape changed; static adapter must be reviewed");
}

// The image is scaled and cropped once.  There is no frame-varying expression,
// zoompan, pan, drift, or crop animation in this replacement.
const staticFilter = `const motionFilter = (_motion, _frames) => {
  return [
    \`scale=\${width}:\${height}:force_original_aspect_ratio=increase\`,
    \`crop=\${width}:\${height}\`,
    \`fps=\${fps}\`,
    "format=yuv420p",
  ].join(",");
};`;
const patched = source.slice(0, start) + staticFilter + source.slice(end);
if (patched.includes("zoompan=")) {
  throw new Error("static adapter patch left zoompan in the executable renderer");
}

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "book-video-hbg-static-"));
try {
  const scripts = path.join(temporary, "scripts");
  fs.mkdirSync(scripts, { recursive: true });
  fs.copyFileSync(sourceConfig, path.join(scripts, "project_config.mjs"));
  fs.cpSync(sourceAssets, path.join(temporary, "assets"), { recursive: true });
  const patchedScript = path.join(scripts, "render_streaming_ffmpeg.mjs");
  fs.writeFileSync(patchedScript, patched, "utf8");
  const completed = spawnSync(process.execPath, [patchedScript, path.resolve(projectArg), outputArg], {
    cwd: path.resolve(projectArg),
    stdio: "inherit",
    env: process.env,
  });
  if (completed.status !== 0) {
    throw new Error(`static HBG streaming renderer failed with exit ${completed.status}`);
  }
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
