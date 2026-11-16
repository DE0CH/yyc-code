#!/bin/sh
# The worker under its own X display (the browser runs headed: some walls refuse headless Chrome). Xvfb in the
# background, then exec, so SIGTERM reaches the worker itself and it finishes its running tasks before the pod goes.
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/dev/null 2>&1 &
export DISPLAY=:99
# Chrome started before the display is up exits ("Missing X server"): wait for its socket
i=0; while [ ! -S /tmp/.X11-unix/X99 ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i+1)); done
exec python -m aiwatch.worker
