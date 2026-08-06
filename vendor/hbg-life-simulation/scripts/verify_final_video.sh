#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: verify_final_video.sh VIDEO.mp4 QA_DIR [SECONDS:LABEL ...]" >&2
  exit 2
fi

video=$1
qa_dir=$2
shift 2

if [[ ! -s "$video" ]]; then
  echo "video missing or empty: $video" >&2
  exit 2
fi

for command_name in ffmpeg ffprobe; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "required command not found: $command_name" >&2
    exit 2
  fi
done

script_dir=$(cd "$(dirname "$0")" && pwd)
video=$(cd "$(dirname "$video")" && pwd)/$(basename "$video")
mkdir -p "$qa_dir"
qa_dir=$(cd "$qa_dir" && pwd)

ffprobe -v error -count_frames -show_streams -show_format -of json "$video" > "$qa_dir/ffprobe.json"

ffmpeg -hide_banner -nostats -i "$video" \
  -vf 'blackdetect=d=0.20:pic_th=0.98:pix_th=0.10' -an -f null - \
  2>&1 | grep 'black_' > "$qa_dir/blackdetect.txt" || true

ffmpeg -hide_banner -nostats -i "$video" \
  -vn -af 'silencedetect=n=-50dB:d=1.0' -f null - \
  2>&1 | grep 'silence_' > "$qa_dir/silencedetect.txt" || true

ffmpeg -hide_banner -nostats -i "$video" \
  -vn -af 'ebur128=peak=true' -f null - \
  2>&1 | awk '/Summary:/{keep=1} keep{print}' > "$qa_dir/ebur128.txt"

frames=()
index=1
for spec in "$@"; do
  if [[ "$spec" == *:* ]]; then
    seconds=${spec%%:*}
    label=${spec#*:}
  else
    seconds=$spec
    label="frame-$index"
  fi
  safe_label=$(printf '%s' "$label" | tr -cs '[:alnum:]_-' '-')
  frame_path="$qa_dir/$(printf '%02d' "$index")-$safe_label.png"
  ffmpeg -hide_banner -loglevel error -ss "$seconds" -i "$video" -frames:v 1 -y "$frame_path"
  frames+=("$frame_path")
  index=$((index + 1))
done

if (( ${#frames[@]} > 0 )); then
  "$script_dir/make_contact_sheet.sh" "$qa_dir/contact-sheet.jpg" 2 "${frames[@]}"
fi

printf 'probe=%s\n' "$qa_dir/ffprobe.json"
printf 'blackdetect=%s\n' "$qa_dir/blackdetect.txt"
printf 'silencedetect=%s\n' "$qa_dir/silencedetect.txt"
printf 'loudness=%s\n' "$qa_dir/ebur128.txt"
if (( ${#frames[@]} > 0 )); then
  printf 'contact_sheet=%s\n' "$qa_dir/contact-sheet.jpg"
fi
