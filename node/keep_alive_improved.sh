#!/bin/bash
cd /home/wuyanbingep/clawd/chronos-protocol/node

echo "Starting MEP CLI Provider - Improved Keep-Alive"
echo "Only restarts on crash, not normal disconnection"

MAX_RESTARTS=10
RESTART_DELAY=10
restart_count=0

while [ $restart_count -lt $MAX_RESTARTS ]; do
    echo "[$(date)] Starting MEP CLI Provider (attempt $((restart_count + 1))/$MAX_RESTARTS)..."
    
    # Start provider and capture PID
    PYTHONUNBUFFERED=1 python3 mep_cli_provider.py &
    PROVIDER_PID=$!
    
    # Monitor provider
    while kill -0 $PROVIDER_PID 2>/dev/null; do
        sleep 5
        
        # Check if provider is actually connected (optional health check)
        # Could add WebSocket ping or status check here
    done
    
    # Provider process ended
    wait $PROVIDER_PID
    EXIT_CODE=$?
    
    echo "[$(date)] Provider exited with code $EXIT_CODE"
    
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ]; then
        # Normal exit or SIGINT (Ctrl+C) - don't restart
        echo "[$(date)] Normal exit detected. Not restarting."
        break
    else
        # Crash detected - restart
        restart_count=$((restart_count + 1))
        echo "[$(date)] Crash detected. Restarting in ${RESTART_DELAY}s... (restart $restart_count/$MAX_RESTARTS)"
        sleep $RESTART_DELAY
    fi
done

echo "[$(date)] Maximum restarts reached or normal exit. Exiting keep-alive."
