#!/usr/bin/env bash

SENTINEL="$HOME/.flatline/pending_crystallization"
LOG="$HOME/logs/flatline-crystallize.log"
mkdir -p "$HOME/logs"

cleanup_and_exit() {
    log "ERROR: $1"
    # Stop crystallizer if running
    systemctl --user stop llama-crystallizer.service 2>/dev/null || true
    # Restart llama-qwen
    systemctl --user start llama-qwen-mtp.service 2>/dev/null || true
    # Delete sentinel
    rm -f "$SENTINEL"
    exit 1
}

log() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S') - $1"
    echo "$msg" >> "$LOG"
}

# Check sentinel exists
if [ ! -f "$SENTINEL" ]; then
    exit 0
fi

log "Starting delayed crystallization run"

# Read session_id from sentinel
SESSION_ID=$(python3 -c "import json; print(json.load(open('$SENTINEL'))['session_id'])")

# Stop llama-qwen
log "Stopping llama-qwen-mtp.service"
systemctl --user stop llama-qwen-mtp.service || cleanup_and_exit "Failed to stop llama-qwen-mtp.service"

# Poll until port 1235 is closed
log "Waiting for port 1235 to close..."
TIMEOUT=120
ELAPSED=0
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    if ! ss -tlnp 2>/dev/null | grep -q ':1235 ' && ! nc -z -w1 localhost 1235 2>/dev/null; then
        log "Port 1235 is closed"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    cleanup_and_exit "Timeout waiting for port 1235 to close after ${TIMEOUT}s"
fi

# Start crystallizer
log "Starting llama-crystallizer.service"
systemctl --user start llama-crystallizer.service || cleanup_and_exit "Failed to start llama-crystallizer.service"

# Poll until port 1238 responds
log "Waiting for port 1238 to respond..."
TIMEOUT=180
ELAPSED=0
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    if curl -s http://localhost:1238/health 2>/dev/null | grep -q '"status":"ok"'; then
        log "Port 1238 is responding"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    cleanup_and_exit "Timeout waiting for port 1238 to respond after ${TIMEOUT}s"
fi

# Run crystallization
log "Running crystallize_session with session_id=$SESSION_ID"
cd /home/fuad/OCProjects/flatline && python3 -c "
from neo4j import GraphDatabase, Auth
from flatline_crystallizer import crystallize_session
DB_PATH = '/home/fuad/OCProjects/flatline/flatline.db'
driver = GraphDatabase.driver('bolt://192.168.1.53:7687', auth=Auth('basic', 'neo4j', 'neo4j_password'))
session = driver.session()
try:
    crystallize_session(DB_PATH, session, '$SESSION_ID')
finally:
    driver.close()
" || cleanup_and_exit "Crystallization failed"

# Stop crystallizer
log "Stopping llama-crystallizer.service"
systemctl --user stop llama-crystallizer.service || cleanup_and_exit "Failed to stop llama-crystallizer.service"

# Delete sentinel
log "Deleting sentinel file"
rm -f "$SENTINEL"

log "Crystallization complete. Powering off."
systemctl poweroff
