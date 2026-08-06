#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 6 ]]; then
  echo "usage: split_2x2.sh INPUT OUTPUT_DIR PREFIX [OUTER_MARGIN=8] [GUTTER=8] [ORIENTATION=landscape]" >&2
  exit 2
fi

input=$1
output_dir=$2
prefix=$3
outer=${4:-8}
gutter=${5:-8}
orientation=${6:-landscape}

case "$orientation" in
  landscape)
    output_width=1920
    output_height=1080
    ;;
  portrait)
    output_width=1080
    output_height=1920
    ;;
  *)
    echo "orientation must be landscape or portrait" >&2
    exit 2
    ;;
esac

mkdir -p "$output_dir"

dimensions=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$input")
width=${dimensions%x*}
height=${dimensions#*x}
panel_width=$(( (width - 2 * outer - gutter) / 2 ))
panel_height=$(( (height - 2 * outer - gutter) / 2 ))
x2=$(( outer + panel_width + gutter ))
y2=$(( outer + panel_height + gutter ))

ffmpeg -hide_banner -loglevel error -i "$input" -filter_complex \
  "[0:v]crop=${panel_width}:${panel_height}:${outer}:${outer},scale=${output_width}:${output_height}:force_original_aspect_ratio=increase,crop=${output_width}:${output_height}[p1];\
   [0:v]crop=${panel_width}:${panel_height}:${x2}:${outer},scale=${output_width}:${output_height}:force_original_aspect_ratio=increase,crop=${output_width}:${output_height}[p2];\
   [0:v]crop=${panel_width}:${panel_height}:${outer}:${y2},scale=${output_width}:${output_height}:force_original_aspect_ratio=increase,crop=${output_width}:${output_height}[p3];\
   [0:v]crop=${panel_width}:${panel_height}:${x2}:${y2},scale=${output_width}:${output_height}:force_original_aspect_ratio=increase,crop=${output_width}:${output_height}[p4]" \
  -map '[p1]' -frames:v 1 -y "$output_dir/${prefix}-01.png" \
  -map '[p2]' -frames:v 1 -y "$output_dir/${prefix}-02.png" \
  -map '[p3]' -frames:v 1 -y "$output_dir/${prefix}-03.png" \
  -map '[p4]' -frames:v 1 -y "$output_dir/${prefix}-04.png"

printf '%s\n' \
  "$output_dir/${prefix}-01.png" \
  "$output_dir/${prefix}-02.png" \
  "$output_dir/${prefix}-03.png" \
  "$output_dir/${prefix}-04.png"
