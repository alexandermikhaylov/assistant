import os
import yaml
import json
import time
import glob
from datetime import datetime, timedelta

USERS_ROOT = "/app/users"

def load_state(user_dir):
    state_file = os.path.join(user_dir, "data", "recurrent_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f: return json.load(f)
        except Exception: return {}
    return {}

def save_state(user_dir, state):
    state_file = os.path.join(user_dir, "data", "recurrent_state.json")
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, 'w') as f: json.dump(state, f)

def should_run(filename, metadata, state):
    schedule = metadata.get('schedule', {})
    times = schedule.get('times', [])
    if not times: return False, None
    
    target_date = schedule.get('date')
    weekdays = schedule.get('weekdays')
    
    now = datetime.now()
    today_date = now.strftime("%Y-%m-%d")
    today_weekday = now.strftime("%a")

    if target_date and target_date != today_date: return False, None
    elif weekdays and today_weekday not in weekdays: return False, None

    for t_str in times:
        try:
            t_hour, t_min = map(int, t_str.split(':'))
            task_time = now.replace(hour=t_hour, minute=t_min, second=0, microsecond=0)
            window_end = task_time + timedelta(minutes=15)
            
            if task_time <= now <= window_end:
                key = f"{filename}_{t_str}" # Unique key for state
                last_run_date = state.get(key, {}).get('last_run_date', "")
                
                if last_run_date != today_date:
                    return True, t_str
        except Exception: pass
    return False, None

def check_recurrent_tasks():
    user_dirs = glob.glob(os.path.join(USERS_ROOT, "user_*"))

    for user_dir in user_dirs:
        state = load_state(user_dir)
        recurrent_dir = os.path.join(user_dir, "tasks", "recurrent")
        tasks_dir = os.path.join(user_dir, "tasks")
        
        if not os.path.exists(recurrent_dir): continue

        files = [f for f in os.listdir(recurrent_dir) if f.endswith(".md")]
        for filename in files:
            filepath = os.path.join(recurrent_dir, filename)
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                    parts = content.split('---', 2)
                    if len(parts) < 3: continue
                    metadata = yaml.safe_load(parts[1])
                    
                    run_needed, run_time_str = should_run(filename, metadata, state)
                    if run_needed:
                        print(f"[{datetime.now()}] Spawning recurrent {filename} for {os.path.basename(user_dir)}")
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        new_task = f"recurrent_{os.path.splitext(filename)[0]}_{timestamp}.md"
                        
                        metadata['regular'] = False
                        new_content = f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{parts[2]}"
                        
                        with open(os.path.join(tasks_dir, new_task), "w") as nf:
                            nf.write(new_content)
                        
                        key = f"{filename}_{run_time_str}"
                        state[key] = {'last_run_date': datetime.now().strftime("%Y-%m-%d")}
                        save_state(user_dir, state)

                        if metadata.get('schedule', {}).get('date'):
                            os.remove(filepath)
            except Exception as e:
                print(f"Error in heartbeat for {filename}: {e}")

if __name__ == "__main__":
    print("Heartbeat service started (Multi-user).")
    while True:
        check_recurrent_tasks()
        time.sleep(60)
