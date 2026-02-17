import os
import json
import yaml
import asyncio
import re
import glob
from datetime import datetime
from utils import strip_ansi

USERS_ROOT = "/app/users"
CURRENT_TASK_FILE = "/app/data/current_task.json"

def get_current_tasks(user_id=None):
    res = ""
    # If user_id provided, show only their tasks
    target_dirs = [os.path.join(USERS_ROOT, f"user_{user_id}")] if user_id else glob.glob(os.path.join(USERS_ROOT, "user_*"))
    
    for user_dir in target_dirs:
        tasks_dir = os.path.join(user_dir, "tasks")
        recurrent_dir = os.path.join(tasks_dir, "recurrent")
        u_id = os.path.basename(user_dir).replace("user_", "")
        
        if not user_id: res += f"👤 <b>User {u_id}:</b>\n"

        if os.path.exists(tasks_dir):
            files = [f for f in os.listdir(tasks_dir) if f.endswith(".md") and os.path.isfile(os.path.join(tasks_dir, f))]
            if files:
                res += "📋 <b>Очередь задач:</b>\n"
                for f in sorted(files):
                    mtime = datetime.fromtimestamp(os.path.getmtime(os.path.join(tasks_dir, f))).strftime("%H:%M")
                    res += f"- <code>{f}</code> ({mtime})\n"
            else: res += "📋 Задач нет.\n"
        
        if os.path.exists(recurrent_dir):
            r_files = [f for f in os.listdir(recurrent_dir) if f.endswith(".md")]
            if r_files:
                res += "🔄 <b>Регулярные:</b>\n"
                for f in sorted(r_files):
                    res += f"- <code>{f}</code>\n"
        res += "\n"
    return res.strip()

def get_memories_summary(user_id):
    user_dir = os.path.join(USERS_ROOT, f"user_{user_id}")
    memories_dir = os.path.join(user_dir, "memories")
    
    if not os.path.exists(memories_dir): return "Папка памяти не найдена."
    files = [f for f in os.listdir(memories_dir) if f.endswith(".md")]
    if not files: return "Память пуста."
    
    res = f"🧠 <b>Факты ({len(files)}):</b>\n"
    for f in sorted(files)[:10]:
        res += f"- {f}\n"
    return res

def get_running_status(user_id):
    if os.path.exists(CURRENT_TASK_FILE):
        try:
            with open(CURRENT_TASK_FILE, 'r') as f:
                data = json.load(f)
                if str(data.get('user_id')) == str(user_id):
                    started = datetime.fromisoformat(data['started_at']).strftime("%H:%M:%S")
                    return f"⚙️ <b>Выполняется:</b>\n{data['task']} (с {started})"
        except Exception: pass
    return "⏸ Простой."

def get_full_state(user_id):
    return f"{get_running_status(user_id)}\n\n{get_current_tasks(user_id)}\n\n{get_memories_summary(user_id)}"

def strip_ansi_compat(text):
    """Compatibility wrapper — delegates to shared utils."""
    return strip_ansi(text)

async def notify_results(bot, send_fn):
    """
    Scans ALL user directories for completed tasks (in archive) or failed tasks (in active)
    and sends notifications to the corresponding user.
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    
    # Track processed files to avoid duplicate notifications in this runtime session
    # (In a real DB this would be better, but for now in-memory set + file flag is ok)
    # Actually rely on 'notified: true' flag in file.
    
    # We need a start time reference to avoid notifying old files on restart
    # But since we check 'notified' flag, we can scan everything.
    # To be safe, let's look at files modified in the last 24h or just trust the flag.
    
    while True:
        try:
            user_dirs = glob.glob(os.path.join(USERS_ROOT, "user_*"))
            for user_dir in user_dirs:
                user_id = os.path.basename(user_dir).replace("user_", "")
                
                tasks_dir = os.path.join(user_dir, "tasks")
                archive_dir = os.path.join(tasks_dir, "archive")
                
                for folder in [archive_dir, tasks_dir]:
                    if not os.path.exists(folder): continue
                    
                    for filename in os.listdir(folder):
                        if not filename.endswith(".md"): continue
                        filepath = os.path.join(folder, filename)
                        
                        try:
                            with open(filepath, 'r') as f: content = f.read()
                            if "--- RESULT" not in content: continue
                            
                            parts = content.split('---')
                            if len(parts) < 2: continue
                            
                            metadata = yaml.safe_load(parts[1]) or {}
                            if metadata.get('notified'): continue
                            
                            # Parse Result
                            last_res_block = content.rsplit("--- RESULT", 1)[-1]
                            res_body = last_res_block.split("\n", 1)[-1].strip()
                            
                            # Extract <answer>
                            display_text = ""
                            answer_match = re.search(r'<answer>(.*?)</answer>', res_body, re.DOTALL | re.IGNORECASE)
                            if answer_match:
                                display_text = answer_match.group(1).strip()
                            else:
                                # Fallback: try to find non-tag text
                                display_text = re.sub(r'<thought>.*?</thought>', '', res_body, flags=re.DOTALL).strip()

                            if not display_text: continue
                            
                            display_text = strip_ansi(display_text)

                            # Prepare Buttons
                            builder = InlineKeyboardBuilder()
                            # Check for structured <confirm> tag
                            is_conf = False
                            confirm_match = re.search(r'<confirm>(.*?)</confirm>', display_text, re.DOTALL | re.IGNORECASE)
                            if confirm_match:
                                # Remove the <confirm> tag from displayed text
                                display_text = re.sub(r'<confirm>.*?</confirm>', '', display_text, flags=re.DOTALL | re.IGNORECASE).strip()
                                builder.button(text="✅ Да", callback_data=f"conf_yes_{filename}")
                                builder.button(text="❌ Нет", callback_data=f"conf_no_{filename}")
                                is_conf = True

                            # Send
                            try:
                                sent = await send_fn(user_id, display_text, metadata.get('message_id'), builder.as_markup() if is_conf else None)
                                if sent:
                                    metadata['notified'] = True
                                    metadata['last_ai_message_id'] = sent.message_id
                                    
                                    # Update file safely
                                    # We reconstruct: --- \n metadata \n --- \n rest
                                    # Find where first metadata block ends
                                    _, _, rest = content.split('---', 2)
                                    new_content = f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{rest.strip()}\n"
                                    
                                    with open(filepath, 'w') as f: f.write(new_content)
                                    print(f"--- [NOTIFY] Sent to {user_id}: {filename}")
                            except Exception as send_err:
                                print(f"Failed to send to {user_id}: {send_err}")

                        except Exception as fe:
                            print(f"Error checking file {filename}: {fe}")

        except Exception as e:
            print(f"Notify loop error: {e}")
        
        await asyncio.sleep(5)
