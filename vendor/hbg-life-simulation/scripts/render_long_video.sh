#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: render_long_video.sh PROJECT_DIR OUTPUT.mp4" >&2
  exit 2
fi

script_dir=$(cd "$(dirname "$0")" && pwd)
project_dir=$1
output=$2
quality=${HBG_RENDER_QUALITY:-high}
min_free_gib=${HBG_MIN_FREE_GIB:-12}
use_gpu=${HBG_USE_GPU:-auto}
encode_timeout_ms=${FFMPEG_ENCODE_TIMEOUT_MS:-7200000}

if [[ "$quality" != "draft" && "$quality" != "standard" && "$quality" != "high" ]]; then
  echo "HBG_RENDER_QUALITY must be draft, standard, or high" >&2
  exit 2
fi

"$script_dir/preflight_long_render.sh" "$project_dir" "$min_free_gib"
project_dir=$(cd "$project_dir" && pwd)
hyperframes_version=$(node -e '
  const fs = require("fs");
  const pkg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
  const scripts = Object.values(pkg.scripts || {}).join(" ");
  const match = scripts.match(/hyperframes@([0-9]+(?:\.[0-9]+){2})/);
  if (!match) process.exit(1);
  process.stdout.write(match[1]);
' "$project_dir/package.json") || {
  echo "Unable to resolve the pinned HyperFrames version from package.json scripts" >&2
  exit 2
}

gpu_args=()
if [[ "$use_gpu" == "1" || ( "$use_gpu" == "auto" && "$(uname -s)" == "Darwin" ) ]]; then
  gpu_args+=(--gpu)
elif [[ "$use_gpu" != "0" && "$use_gpu" != "auto" ]]; then
  echo "HBG_USE_GPU must be auto, 1, or 0" >&2
  exit 2
fi

cd "$project_dir"
PRODUCER_ENABLE_CHUNKED_ENCODE=true \
FFMPEG_ENCODE_TIMEOUT_MS="$encode_timeout_ms" \
npx --yes "hyperframes@$hyperframes_version" render --quality "$quality" "${gpu_args[@]}" --output "$output"
