#!/bin/bash

LOG="$HOME/logs/flatline-crystallize.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG"
}

log "Starting pre-crystallization cleanup"

# Warn user 5 minutes before cleanup
notify-send -u critical "Flatline" "Cleanup in 5 minutes. Floorp will be closed." -t 10000
log "Warning notification sent. Waiting 5 minutes..."
sleep 300

# Kill browser
pkill -x firefox 2>/dev/null && log "Killed firefox" || true
pkill -x chromium 2>/dev/null && log "Killed chromium" || true
pkill -x brave 2>/dev/null && log "Killed brave" || true
pkill -x floorp 2>/dev/null && log "Killed floorp" || true

# Kill file manager
pkill -x thunar 2>/dev/null && log "Killed thunar" || true
pkill -x nautilus 2>/dev/null && log "Killed nautilus" || true
pkill -x nemo 2>/dev/null && log "Killed nemo" || true

# Kill image viewers
pkill -x eog 2>/dev/null && log "Killed eog" || true
pkill -x feh 2>/dev/null && log "Killed feh" || true

log "Cleanup complete. Crystallization starts in 15 minutes."
