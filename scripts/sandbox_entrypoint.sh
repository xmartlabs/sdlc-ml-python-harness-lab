#!/usr/bin/env bash
# Entrypoint for the sandbox image. MODE selects the interface:
#   terminal -> ttyd web shell on :7681
#   desktop  -> Xvfb + fluxbox + x11vnc + noVNC on :6080
# With PLAY_COMMAND set (the "Play" sandbox, ADR-0078) the desktop also starts
# the case's game, full-screen-ish, once the X server is up. The terminal stays
# on the desktop underneath, so a person can relaunch or inspect it.
set -e
MODE="${MODE:-terminal}"
cd /work

if [ "$MODE" = "desktop" ]; then
  rm -f /tmp/.X0-lock
  # 1600x900: room for a game window at 1280x720 plus the desktop around it.
  Xvfb :0 -screen 0 1600x900x24 >/tmp/xvfb.log 2>&1 &
  sleep 1
  fluxbox >/tmp/fluxbox.log 2>&1 &
  # A ready-to-use terminal on the desktop (cd'd into the workspace).
  xterm -geometry 110x30+20+20 -e "cd /work && bash" >/tmp/xterm.log 2>&1 &
  if [ -n "${PLAY_COMMAND:-}" ]; then
    # The command is authored in the case's task.yaml and reviewed with it. Its
    # output goes to a log the terminal can read; if the game exits, the
    # message says so in a window instead of leaving an empty desktop.
    (
      cd /work
      status=0
      bash -c "$PLAY_COMMAND" >/tmp/play.log 2>&1 || status=$?
      # Written to a file and shown with `cat`, so nothing in the command is
      # ever re-parsed by a second shell.
      {
        printf 'The game exited with status %s. Last lines of /tmp/play.log:\n\n' "$status"
        tail -n 30 /tmp/play.log
        printf '\nRelaunch from the terminal, in /work:\n  %s\n' "$PLAY_COMMAND"
      } >/tmp/play-exit.txt
      xterm -geometry 100x20+200+200 -title "Game exited" \
        -e bash -c 'cat /tmp/play-exit.txt; exec bash' >/dev/null 2>&1
    ) &
  fi
  x11vnc -display :0 -forever -shared -nopw -quiet -bg >/tmp/x11vnc.log 2>&1
  exec websockify --web /usr/share/novnc 6080 localhost:5900
else
  # Writable web shell in the workspace.
  exec ttyd -W -p 7681 bash
fi
