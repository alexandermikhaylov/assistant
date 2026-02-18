import os
import json
import yaml
import asyncio
import re
import glob
from datetime import datetime
from utils import strip_ansi
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

USERS_ROOT = "/app/users"
CURRENT_TASK_FILE = "/app/data/current_task.json"

def get_current_tasks(user_id=None):
    res = ""
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
    return strip_ansi(text)

async def notify_results(bot, send_fn):
    """
    Scans user directories for:
    1. Active Tasks (in tasks/) -> Update Dashboard (Edit Message)
    2. Completed Tasks (in archive/) -> Final Result (Edit Message one last time)
    """
    while True:
        try:
            user_dirs = glob.glob(os.path.join(USERS_ROOT, "user_*"))
            for user_dir in user_dirs:
                user_id = os.path.basename(user_dir).replace("user_", "")
                
                tasks_dir = os.path.join(user_dir, "tasks")
                archive_dir = os.path.join(tasks_dir, "archive")
                
                # Check both Active and Archive
                for folder in [tasks_dir, archive_dir]:
                    if not os.path.exists(folder): continue
                    
                    for filename in os.listdir(folder):
                        if not filename.endswith(".md"): continue
                        filepath = os.path.join(folder, filename)
                        
                        try:
                            # READ FILE
                            with open(filepath, 'r') as f: content = f.read()
                            parts = content.split('---', 2)
                            if len(parts) < 3: continue # Metada + Content required
                            
                            metadata = yaml.safe_load(parts[1]) or {}
                            chat_id = metadata.get('chat_id')
                            status_msg_id = metadata.get('status_message_id')
                            last_hash = metadata.get('last_status_hash')
                            
                            if not chat_id: continue

                            # PARSE CONTENT
                            body = content.split('---', 2)[-1]
                            
                            # 1. Extract REQUEST (Short summary)
                            req_match = re.search(r'# Request\n(.*?)\n#', body, re.DOTALL)
                            req_text = req_match.group(1).strip()[:100] + "..." if req_match else "Processing..."
                            # Escape HTML in request text to prevent errors
                            req_text = req_text.replace("<", "&lt;").replace(">", "&gt;")
                            
                            # 2. Extract PLAN
                            plan_match = re.search(r'# Plan\n(.*?)\n#', body, re.DOTALL)
                            plan_text = plan_match.group(1).strip() if plan_match else ""
                            
                            # 3. Extract FINAL RESULT (if any)
                            result_match = re.search(r'<answer>(.*?)</answer>', body, re.DOTALL | re.IGNORECASE)
                            final_answer = result_match.group(1).strip() if result_match else None
                            
                            # GENERATE DISPLAY TEXT
                            display_text = f"🤖 <b>Task:</b> {req_text}\n\n"
                            
                            if final_answer:
                                # Task Completed
                                display_text += f"✅ <b>Done!</b>\n\n{final_answer}"
                            elif plan_text:
                                # Task In Progress - Show Plan
                                display_text += "📋 <b>Plan:</b>\n"
                                for line in plan_text.splitlines():
                                    line = line.strip()
                                    if line.startswith("- [ ]"):
                                        display_text += f"⬜ {line[5:]}\n"
                                    elif line.startswith("- [/]"):
                                        display_text += f"🔄 {line[5:]}\n"
                                    elif line.startswith("- [x]"):
                                        display_text += f"✅ <b>{line[5:]}</b>\n"
                                    elif line.startswith("- [!]"):
                                        display_text += f"❌ {line[5:]}\n"
                            else:
                                display_text += "⏳ <i>Initializing...</i>"

                            # HASH CHECK to avoid spamming edits if nothing changed
                            current_hash = hash(display_text)
                            if str(current_hash) == str(last_hash):
                                continue

                            # SEND / EDIT
                            builder = InlineKeyboardBuilder()
                            # Check for Confirmation
                            confirm_match = re.search(r'<confirm>(.*?)</confirm>', body, re.DOTALL)
                            if confirm_match:
                                display_text += f"\n\n❓ <b>Confirm:</b> {confirm_match.group(1)}" # Show pure text
                                # We remove confirm tag from display to avoid double showing if formatting matches
                                # But actually we want it shown.
                                builder.button(text="✅ Yes", callback_data=f"conf_yes_{filename}")
                                builder.button(text="❌ No", callback_data=f"conf_no_{filename}")
                                builder.adjust(2)

                            sent_msg = None
                            try:
                                if status_msg_id:
                                    # EDIT
                                    if display_text != "SAME": # Pseudo-check
                                        await bot.edit_message_text(
                                            chat_id=chat_id,
                                            message_id=status_msg_id,
                                            text=display_text,
                                            parse_mode="HTML",
                                            reply_markup=builder.as_markup() if confirm_match else None
                                        )
                                        sent_msg = type('obj', (object,), {'message_id': status_msg_id})
                                else:
                                    # SEND NEW
                                    sent_msg = await bot.send_message(
                                        chat_id=chat_id,
                                        text=display_text,
                                        parse_mode="HTML",
                                        reply_markup=builder.as_markup() if confirm_match else None
                                    )
                                
                                # UPDATE METADATA
                                if sent_msg:
                                    metadata['status_message_id'] = sent_msg.message_id
                                    metadata['last_status_hash'] = str(current_hash)
                                    
                                    # Re-save file
                                    # CAREFUL: We must only update metadata block
                                    new_meta = yaml.dump(metadata, allow_unicode=True)
                                    new_content = f"--- \n{new_meta}--- {body}"
                                    
                                    with open(filepath, 'w') as f: f.write(new_content)
                                    # print(f"Updated status for {filename}")

                            except TelegramBadRequest as e:
                                if "message is not modified" in str(e):
                                    pass # Ignore
                                else:
                                    print(f"Tg Error: {e}")
                            except Exception as e:
                                print(f"Notify Error details: {e}")

                        except Exception as fe:
                            pass # File read error
                            
        except Exception as e:
            print(f"Notify loop error: {e}")
        
        await asyncio.sleep(2)
