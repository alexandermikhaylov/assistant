#!/bin/bash

# Ensure log directory exists
mkdir -p /app/data/logs

# --- Git Restore (Checkout User Repos) ---
echo "Restoring user repositories..."
python3 /app/scripts/git_manager.py restore

# --- User Initialization (Isolated Mode) ---
# Iterate over all user directories in /app/users/
for user_dir in /app/users/user_*; do
    if [ -f "$user_dir/init.sh" ]; then
        user_id=$(basename "$user_dir")
        echo "[Global] Found init.sh for $user_id. Launching..."
        
        # Make executable if not already
        chmod +x "$user_dir/init.sh"
        
        # Execute in background (fire and forget)
        (
            bash "$user_dir/init.sh" >> "/app/data/logs/${user_id}_init.log" 2>&1
        ) &
    fi
done

sleep 3

# --- Core Services ---

echo "Starting Heartbeat Service..."
(
    while true; do
        echo "[$(date)] Starting Heartbeat Service..."
        python3 -u /app/scripts/heartbeat.py
        echo "[$(date)] Heartbeat Service exited. Restarting in 5 seconds..."
        sleep 5
    done
) &
HEARTBEAT_PID=$!

echo "Starting Task Runner..."
(
    while true; do
        echo "[$(date)] Starting Task Runner..."
        python3 -u /app/scripts/task_runner.py
        echo "[$(date)] Task Runner exited. Restarting in 5 seconds..."
        sleep 5
    done
) &
RUNNER_PID=$!

echo "Starting Telegram Gateway..."
(
    while true; do
        echo "[$(date)] Starting Telegram Gateway..."
        python3 -u /app/scripts/telegram_gateway.py
        echo "[$(date)] Telegram Gateway exited. Restarting in 5 seconds..."
        sleep 5
    done
) &
GATEWAY_PID=$!

echo "All core processes started. Waiting..."

# Trap signals and kill background processes
trap "kill $HEARTBEAT_PID $RUNNER_PID $GATEWAY_PID; exit" SIGINT SIGTERM
wait
