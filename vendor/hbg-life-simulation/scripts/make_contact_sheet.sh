#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: make_contact_sheet.sh OUTPUT.jpg COLS IMAGE..." >&2
  exit 2
fi

output=$1
cols=$2
shift 2
images=("$@")
count=${#images[@]}
rows=$(( (count + cols - 1) / cols ))

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

list_file="$tmp_dir/list.txt"
for image in "${images[@]}"; do
  image_dir=$(cd "$(dirname "$image")" && pwd)
  image_path="$image_dir/$(basename "$image")"
  printf "file '%s'\n" "$image_path" >> "$list_file"
  printf "duration 0.04\n" >> "$list_file"
done
# The concat demuxer ignores the final duration unless the last still is repeated.
last_image=${images[$((count - 1))]}
last_dir=$(cd "$(dirname "$last_image")" && pwd)
printf "file '%s/%s'\n" "$last_dir" "$(basename "$last_image")" >> "$list_file"

ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i "$list_file" \
  -vf "fps=25,scale=480:270,tile=${cols}x${rows}:padding=4:margin=8" -frames:v 1 -y "$output"

printf '%s\n' "$output"
