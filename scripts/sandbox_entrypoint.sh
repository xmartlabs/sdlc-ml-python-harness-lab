#!/usr/bin/env bash
# Entrypoint for the sandbox image. MODE selects the interface:
#   terminal -> ttyd web shell on :7681
#   desktop  -> Xvfb + fluxbox + x11vnc + noVNC on :6080
set -e
MODE="${MODE:-terminal}"
cd /work

if [ "$MODE" = "desktop" ]; then
  rm -f /tmp/.X0-lock
  Xvfb :0 -screen 0 1280x800x24 >/tmp/xvfb.log 2>&1 &
  sleep 1
  fluxbox >/tmp/fluxbox.log 2>&1 &
  # A ready-to-use terminal on the desktop (cd'd into the workspace).
  xterm -geometry 110x30+20+20 -e "cd /work && bash" >/tmp/xterm.log 2>&1 &
  x11vnc -display :0 -forever -shared -nopw -quiet -bg >/tmp/x11vnc.log 2>&1
  exec websockify --web /usr/share/novnc 6080 localhost:5900
else
  # Writable web shell in the workspace.
  exec ttyd -W -p 7681 bash
fi
