#!/usr/bin/env bash
# Open an HTML file (or URL) from inside a sandbox shell.
#   desktop mode  -> Chromium window on the noVNC desktop
#   terminal mode -> no X display, so fall back to a headless screenshot
# Installed as `open-html` (and as `xdg-open`) in the sandbox image.
set -euo pipefail

target="${1:-index.html}"

case "$target" in
  http://* | https://* | file://*) url="$target" ;;
  *)
    if [ ! -e "$target" ]; then
      echo "open-html: no such file: $target" >&2
      exit 1
    fi
    url="file://$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
    ;;
esac

flags=(
  --no-sandbox                       # no user namespaces inside the container
  --disable-dev-shm-usage            # /dev/shm is tiny in Docker
  --disable-gpu
  --no-first-run                     # else Debian's ToS dialog hides the page
  --no-default-browser-check
  --disable-search-engine-choice-screen
  --user-data-dir=/tmp/chromium-profile
)

if [ -n "${DISPLAY:-}" ] && pgrep -x Xvfb >/dev/null 2>&1; then
  chromium "${flags[@]}" --window-size=1240,760 "$url" >/tmp/chromium.log 2>&1 &
  echo "Opening $url in Chromium on the desktop (:0)."
else
  shot="${SHOT_PATH:-/work/page.png}"
  chromium "${flags[@]}" --headless=new --hide-scrollbars \
    --window-size=1280,800 --screenshot="$shot" "$url" >/tmp/chromium.log 2>&1
  echo "No X display (terminal mode). Rendered headless to $shot."
fi
