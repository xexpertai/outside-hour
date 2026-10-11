#!/usr/bin/env bash
# From the Playwright recording, make:
#   demo/outside-hour-demo.mp4           the demo with the model wait cut out, labelled on screen
#   demo/outside-hour-demo.gif           the same, as a GIF for the README/post
#   demo/outside-hour-demo-realtime.mp4  the uncut real-time recording, for anyone who wants to check
set -euo pipefail
cd "$(dirname "$0")/.."
RAW=demo/raw
TL=$RAW/timeline.json
j() { python3 -c "import json;print(json.load(open('$TL'))['$1'])"; }
SRC="$RAW/$(j video)"; WS=$(j wait_start); WE=$(j wait_end); SECS=$(python3 -c "print(round($(j plan_seconds)))")
FONT=/usr/share/fonts/truetype/noto/NotoSans-SemiBold.ttf
A_END=$(python3 -c "print(round($WS + 5.0, 2))")   # show ~5 s of the live elapsed timer
B_START=$(python3 -c "print(round($WE - 0.3, 2))")
CUT=$(python3 -c "print(round($B_START - $A_END))")
LABELF=$RAW/cut-label.txt
printf '%s' "Cut: ${CUT} s more of waiting. Gemma took ${SECS} s in total on CPU (real-time file in the repo)" > "$LABELF"

ffmpeg -y -loglevel error -i "$SRC" -an -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -r 30 \
  -movflags +faststart demo/outside-hour-demo-realtime.mp4

CUTGRAPH="[0:v]trim=0:${A_END},setpts=PTS-STARTPTS[a];
  [0:v]trim=start=${B_START},setpts=PTS-STARTPTS,
     drawtext=fontfile=${FONT}:textfile=${LABELF}:x=(w-tw)/2:y=h-125:fontsize=22:fontcolor=white:box=1:boxcolor=0x8a4b00@0.92:boxborderw=10:enable='lt(t,4)'[b];
  [a][b]concat=n=2:v=1[c]"
ffmpeg -y -loglevel error -i "$SRC" -filter_complex "$CUTGRAPH" -map "[c]" -an -c:v libx264 -preset slow -crf 20 \
  -pix_fmt yuv420p -r 30 -movflags +faststart demo/outside-hour-demo.mp4
ffmpeg -y -loglevel error -i demo/outside-hour-demo.mp4 -filter_complex \
  "fps=10,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4" \
  demo/outside-hour-demo.gif
ls -lh demo/*.mp4 demo/*.gif
for f in demo/outside-hour-demo.mp4 demo/outside-hour-demo-realtime.mp4 demo/outside-hour-demo.gif; do
  printf '%s %s s\n' "$f" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"; done
