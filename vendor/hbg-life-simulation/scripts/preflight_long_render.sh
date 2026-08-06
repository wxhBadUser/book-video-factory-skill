#!/usr/bin/env bash
set -euo pipefail

project_dir=${1:-.}
min_free_gib=${2:-12}

if [[ ! -d "$project_dir" ]]; then
  echo "project directory not found: $project_dir" >&2
  exit 2
fi

if ! [[ "$min_free_gib" =~ ^[0-9]+$ ]] || (( min_free_gib < 1 )); then
  echo "minimum free GiB must be a positive integer" >&2
  exit 2
fi

project_dir=$(cd "$project_dir" && pwd)
renders_dir="$project_dir/renders"
mkdir -p "$renders_dir"

free_kib=$(df -Pk "$project_dir" | awk 'NR==2 {print $4}')
required_kib=$((min_free_gib * 1024 * 1024))

printf 'project=%s\n' "$project_dir"
printf 'free_gib=%s\n' "$((free_kib / 1024 / 1024))"
printf 'required_gib=%s\n' "$min_free_gib"

work_dirs=()
while IFS= read -r dir; do
  work_dirs+=("$dir")
done < <(find "$renders_dir" -maxdepth 1 -mindepth 1 -type d \( -name 'work-*' -o -name 'ffmpeg-work-*' \) -print)

if (( ${#work_dirs[@]} > 0 )); then
  echo "existing_render_work_dirs:"
  for dir in "${work_dirs[@]}"; do
    du -sh "$dir"
  done
  echo "verify each render job is inactive before removing any exact work directory" >&2
fi

if (( free_kib < required_kib )); then
  echo "insufficient free space for long render" >&2
  exit 1
fi

echo "preflight=pass"
