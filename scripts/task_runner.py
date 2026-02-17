import os
import sys
import re
import yaml
import json
import subprocess
import time
import glob
from datetime import datetime, timedelta

USERS_ROOT = "/app/users"
CORE_INSTRUCTIONS_DIR = "/app/core_instructions"
CURRENT_TASK_FILE = "/app/data/current_task.json"
GEMINI_BIN = "gemini"


def set_current_task(filename, user_id):
    with open(CURRENT_TASK_FILE, 'w') as f:
        json.dump({"task": filename, "user_id": user_id, "started_at": datetime.now().isoformat()}, f)

def clear_current_task():
    if os.path.exists(CURRENT_TASK_FILE):
        os.remove(CURRENT_TASK_FILE)

def get_context(user_dir):
    ctx = ""
    # 1. Global/Core Instructions
    if os.path.exists(CORE_INSTRUCTIONS_DIR):
        for f in sorted(os.listdir(CORE_INSTRUCTIONS_DIR)):
            if f.endswith(".md"):
                with open(os.path.join(CORE_INSTRUCTIONS_DIR, f), 'r') as file:
                    ctx += f"\nFILE {f} (from core_instructions):\n{file.read()}\n"
    
    # 2. User Instructions
    inst_dir = os.path.join(user_dir, "instructions")
    if os.path.exists(inst_dir):
        for f in sorted(os.listdir(inst_dir)):
            if f.endswith(".md"):
                with open(os.path.join(inst_dir, f), 'r') as file:
                    ctx += f"\nFILE {f} (from user_instructions):\n{file.read()}\n"

    # 3. User Memories
    mem_dir = os.path.join(user_dir, "memories")
    if os.path.exists(mem_dir):
        for f in sorted(os.listdir(mem_dir)):
            if f.endswith(".md"):
                with open(os.path.join(mem_dir, f), 'r') as file:
                    ctx += f"\nFILE {f} (from user_memories):\n{file.read()}\n"

    # 4. User Skills (Available Capabilities)
    skills_file = os.path.join(user_dir, "skills", "skills.md")
    if os.path.exists(skills_file):
        with open(skills_file, 'r') as file:
            ctx += f"\nFILE skills.md (AVAILABLE_SKILLS):\n{file.read()}\n"

    return ctx

def run_gemini(prompt, user_dir, yolo=True, timeout=300):
    args = [GEMINI_BIN]
    if yolo: args.append("-y")
    
    # ISOLATION MAGIC:
    # Set HOME to the user's directory so Gemini CLI finds .gemini/settings.json THERE.
    env = os.environ.copy()
    env['HOME'] = user_dir
    
    try:
        result = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, env=env)
        if result.returncode != 0:
            print(f"Gemini returned error code {result.returncode}: {result.stderr}")
        return result.stdout.strip()
    except Exception as e:
        print(f"Gemini subprocess error: {e}")
        return ""

def maintenance_and_memory(user_dir, task_content, task_result):
    """Extract and save new facts about the user from a completed task."""
    if "Emoji: 👎" in task_content:
        return
    
    memories_dir = os.path.join(user_dir, "memories")
    os.makedirs(memories_dir, exist_ok=True)
    
    # Load existing memories for context
    existing_memories = ""
    if os.path.exists(memories_dir):
        for f in sorted(os.listdir(memories_dir)):
            if f.endswith(".md"):
                try:
                    with open(os.path.join(memories_dir, f), 'r') as mf:
                        existing_memories += f"\n{mf.read()}"
                except Exception:
                    pass
    
    prompt = (
        "You are a memory extraction agent. Your ONLY job is to extract new, important facts "
        "about the user from a conversation. These facts should be useful for future interactions.\n\n"
        "Rules:\n"
        "- Only extract PERSONAL facts (name, preferences, schedule, contacts, habits).\n"
        "- Do NOT extract task-specific operational details.\n"
        "- Do NOT repeat facts already known.\n"
        "- If no new facts are found, respond with exactly: NO_NEW_FACTS\n"
        "- If facts are found, respond with one fact per line, each starting with '- '.\n\n"
        f"EXISTING MEMORIES:\n{existing_memories if existing_memories else '(none)'}\n\n"
        f"CONVERSATION:\n{task_content}\n\n"
        f"AI RESPONSE:\n{task_result}\n\n"
        "NEW FACTS (or NO_NEW_FACTS):"
    )
    
    try:
        result = run_gemini(prompt, user_dir, yolo=True, timeout=60)
        if result and "NO_NEW_FACTS" not in result:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            memory_file = os.path.join(memories_dir, f"auto_{timestamp}.md")
            with open(memory_file, 'w') as f:
                f.write(f"# Auto-extracted ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n{result}\n")
            print(f"[Memory] Saved new facts to {memory_file}")
    except Exception as e:
        print(f"[Memory] Error extracting memories: {e}")

def process_tasks():
    user_dirs = glob.glob(os.path.join(USERS_ROOT, "user_*"))
    
    for user_dir in user_dirs:
        user_id = os.path.basename(user_dir).replace("user_", "")
        tasks_dir = os.path.join(user_dir, "tasks")
        archive_dir = os.path.join(tasks_dir, "archive")
        
        if not os.path.exists(tasks_dir): continue

        files = [f for f in os.listdir(tasks_dir) if f.endswith(".md") and os.path.isfile(os.path.join(tasks_dir, f))]
        files.sort(key=lambda x: (not x.startswith("tg_task_"), x))
        
        if not files: continue

        user_ctx = get_context(user_dir)

        for filename in files:
            filepath = os.path.join(tasks_dir, filename)
            with open(filepath, 'r') as f: content = f.read()
            try:
                parts = content.split('---', 2)
                if len(parts) < 3: continue
                
                metadata = yaml.safe_load(parts[1]) or {}
                
                # Check retry logic
                now = datetime.now()
                next_retry = metadata.get('next_retry_at')
                if next_retry and now < datetime.fromisoformat(next_retry): continue

                metadata['notified'] = False
                history = parts[2].strip()
                
                print(f"[{datetime.now()}] Processing {filename} for {user_id}")
                set_current_task(filename, user_id)
                
                last_run = metadata.get('last_run_timestamp', "unknown")
                prompt = f"{user_ctx}\n\nCURRENT TIME: {now}\nLAST RUN: {last_run}\n"
                prompt += "INSTRUCTION: Use XML tags <thought> and <answer>. Do not write Python code (like print(default_api...)) to invoke tools; use the native tool calling mechanism provided.\n\n"
                prompt += f"HISTORY:\n{history}\n\nANSWER:"

                # PASS USER_DIR HERE
                response = run_gemini(prompt, user_dir)
                
                # Check for OAuth requirement in response
                if "accounts.google.com/o/oauth2" in response:
                    print(f"[{now}] OAuth required for user {user_id}. Distinguishing...")
                    # 681255809395 is the Gemini CLI Client ID
                    if "681255809395" in response:
                        response = "<thought>Gemini CLI needs authentication.</thought><answer>⚠️ Пожалуйста, используйте команду /auth для авторизации, и тогда всё заработает.</answer>"
                    else:
                        # Extract the URL
                        match = re.search(r'(https://[^\s]*google\.com/[^\s]*oauth[^\s]*)', response, re.IGNORECASE)
                        if match:
                            url = match.group(1).strip().rstrip('.')
                            response = f"<thought>An MCP tool (like Google Calendar) needs authentication.</thought><answer>⚠️ Одному из инструментов требуется авторизация. Пожалуйста, откройте ссылку в браузере:\n\n{url}\n\nПолучите код и введите его в Telegram командой `/auth_code <code>` (если это не сработает, попробуйте сначала запустить `/auth`).</answer>"
                        else:
                            response = "<thought>Auth required, but URL not found.</thought><answer>⚠️ Одному из инструментов требуется авторизация, но ссылка не найдена. Пожалуйста, попробуйте выполнить /auth.</answer>"

                if response and "Quota" not in response and "429" not in response and "accounts.google.com/o/oauth2" not in response:
                    # Success
                    metadata.pop('first_failed_at', None); metadata.pop('next_retry_at', None)
                    with open(filepath, "w") as f:
                        f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{history}\n\n--- RESULT ({now}) ---\n{response}\n")
                    if not os.path.exists(archive_dir): os.makedirs(archive_dir)
                    os.rename(filepath, os.path.join(archive_dir, filename))
                    
                    # Update memory with facts from this interaction
                    maintenance_and_memory(user_dir, history, response)
                    
                    # Auto-commit and push changes
                    print(f"[{now}] Committing and pushing changes for {user_id}...", flush=True)
                    subprocess.run([sys.executable, "/app/scripts/git_manager.py", "commit", user_id, f"Task {filename} completed"], check=False)
                else:
                    # Failure / Quota
                    if response and ("Quota" in response or "429" in response):
                        print(f"[{now}] Quota limit for {user_id}. Sleeping task 1h.")
                        metadata['next_retry_at'] = (now + timedelta(hours=1)).isoformat()
                        if not metadata.get('notified'):
                             # Notification logic placeholder
                             pass
                        with open(filepath, "w") as f:
                            f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{history}\n")
                    else:
                        # Retry logic (15m)
                        print(f"[{now}] Task failed. Retrying in 15m.")
                        metadata['next_retry_at'] = (now + timedelta(minutes=15)).isoformat()
                        metadata['notified'] = False
                        with open(filepath, "w") as f:
                             f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{history}\n\n--- RESULT ---\n<thought>Error.</thought><answer>⚠️ Ошибка. Повторю через 15 мин.</answer>\n")

                clear_current_task()
            except Exception as e:
                print(f"Error processing {filename}: {e}")
                clear_current_task()

if __name__ == "__main__":
    while True:
        process_tasks()
        time.sleep(10)
