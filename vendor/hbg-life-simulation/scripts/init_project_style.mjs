import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const projectArg = args.find((value) => !value.startsWith("--")) ?? ".";
const orientationFlag = args.find((value) => value.startsWith("--orientation="));
const orientationIndex = args.indexOf("--orientation");
const orientation = orientationFlag?.split("=")[1] ?? (orientationIndex >= 0 ? args[orientationIndex + 1] : "landscape");
if (!new Set(["landscape", "portrait"]).has(orientation)) throw new Error("--orientation must be landscape or portrait");
const projectDir = path.resolve(projectArg);
const target = path.join(projectDir, "HBG_STYLE.json");
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const template = path.resolve(scriptDir, `../assets/hbg-style-${orientation}.json`);

if (!fs.existsSync(projectDir)) throw new Error(`project directory does not exist: ${projectDir}`);
if (!fs.existsSync(template)) throw new Error(`style template is missing: ${template}`);

if (fs.existsSync(target)) {
  console.log(JSON.stringify({ status: "kept", path: target, requestedOrientation: orientation }, null, 2));
  process.exit(0);
}

fs.copyFileSync(template, target, fs.constants.COPYFILE_EXCL);
console.log(JSON.stringify({ status: "created", path: target, orientation, template }, null, 2));
