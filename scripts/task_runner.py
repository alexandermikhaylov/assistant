import os
import sys
import re
import yaml
import json
import subprocess
import time
import glob
from datetime import datetime
from utils import strip_ansi

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
    # 1. Global Interactions (Instructions)
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

    # 4. Skills
    skills_file = os.path.join(user_dir, "skills", "skills.md")
    if os.path.exists(skills_file):
        with open(skills_file, 'r') as file:
            ctx += f"\nFILE skills.md (AVAILABLE_SKILLS):\n{file.read()}\n"

    return ctx

def run_gemini(prompt, user_dir, yolo=True, timeout=300):
    args = [GEMINI_BIN]
    if yolo: args.append("-y")
    
    env = os.environ.copy()
    env['HOME'] = user_dir
    
    try:
        result = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, env=env)
        return result.stdout.strip()
    except Exception as e:
        print(f"Gemini subprocess error: {e}")
        return ""

def load_parent_context(user_dir, parent_task_id):
    if not parent_task_id: return ""
    
    # Check Active then Archive
    paths = [
        os.path.join(user_dir, "tasks", parent_task_id),
        os.path.join(user_dir, "tasks", "archive", parent_task_id)
    ]
    
    for path in paths:
        if os.path.exists(path):
            with open(path, 'r') as f: content = f.read()
            # Try to extract <answer> (Final Result) or full history
            # For context, we probably want the User Request + Final Answer
            
            parts = content.split('---')
            if len(parts) >= 3:
                body = parts[2]
                req_match = re.search(r'# Request\n(.*?)\n#', body, re.DOTALL)
                req_text = req_match.group(1).strip() if req_match else "Unknown Request"
                
                ans_match = re.search(r'<answer>(.*?)</answer>', body, re.DOTALL | re.IGNORECASE)
                ans_text = ans_match.group(1).strip() if ans_match else "No Answer"
                
                return f"\n\n--- PREVIOUS CONVERSATION ---\nUser: {req_text}\nAssistant: {ans_text}\n----------------------------\n"
            return ""
            
    return ""

def maintenance_and_memory(user_dir, task_content, task_result):
    # Simplified for now - can be expanded later
    pass

def process_tasks():
    user_dirs = glob.glob(os.path.join(USERS_ROOT, "user_*"))
    
    for user_dir in user_dirs:
        user_id = os.path.basename(user_dir).replace("user_", "")
        tasks_dir = os.path.join(user_dir, "tasks")
        archive_dir = os.path.join(tasks_dir, "archive")
        
        if not os.path.exists(tasks_dir): continue

        files = [f for f in os.listdir(tasks_dir) if f.endswith(".md") and os.path.isfile(os.path.join(tasks_dir, f))]
        # Prioritize older tasks? Or user logic.
        files.sort() 
        
        if not files: continue

        user_ctx = get_context(user_dir)

        for filename in files:
            filepath = os.path.join(tasks_dir, filename)
            with open(filepath, 'r') as f: content = f.read()
            
            # Split ONLY on the first two '---' (YAML frontmatter delimiters)
            # This preserves any '---' inside the body (e.g. --- RESULT ---, --- USER REACTION ---)
            parts = content.split('---', 2)
            if len(parts) < 3: continue
            
            metadata = yaml.safe_load(parts[1]) or {}
            
            # CHECK BLOCKED STATUS (User Confirmation)
            if "<confirm>" in content and "--- USER DECISION ---" not in content.split("<confirm>")[-1]:
                continue

            set_current_task(filename, user_id)
            print(f"Processing {filename}...")

            body = parts[2]
            
            # 1. PARSE SECTIONS
            req_match = re.search(r'# Request\n(.*?)\n#', body, re.DOTALL)
            request_text = req_match.group(1).strip() if req_match else ""
            
            plan_match = re.search(r'# Plan\n(.*?)\n#', body, re.DOTALL)
            plan_text = plan_match.group(1).strip() if plan_match else ""
            
            history_match = re.search(r'# History\n(.*)', body, re.DOTALL)
            history_text = history_match.group(1).strip() if history_match else ""

            # 2. STATE MACHINE
            
            # STEP A: GENERATE PLAN
            if not plan_text:
                print("Generating Plan...")
                parent_ctx = load_parent_context(user_dir, metadata.get('parent_task_id'))
                
                prompt = (
                    f"{user_ctx}\n{parent_ctx}\n"
                    f"USER REQUEST: {request_text}\n\n"
                    "INSTRUCTION: Create a checklist plan to solve the user's request. "
                    "Break it down into atomic steps (search, analyze, execute). "
                    "Output ONLY the markdown list, e.g.:\n- [ ] Step 1\n- [ ] Step 2\n"
                )
                
                plan = run_gemini(prompt, user_dir)
                if plan:
                    # Save Plan
                    if "# Plan" in body:
                        new_body = re.sub(r'# Plan\s*\n', f'# Plan\n{plan}\n\n', body, count=1)
                    else:
                        # Fallback for legacy/malformed files: Append sections
                        new_body = body.strip() + f"\n\n# Plan\n{plan}\n\n# History\n"
                    
                    with open(filepath, 'w') as f:
                        f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{new_body}")
                continue # Loop will pick it up next time/tick

            # STEP B: EXECUTE NEXT ITEM
            lines = plan_text.splitlines()
            next_step_idx = -1
            next_step_text = ""
            
            # Find first unchecked OR stuck-in-progress item
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("- [ ]"):
                    next_step_idx = i
                    next_step_text = stripped[5:].strip()
                    break
                elif stripped.startswith("- [/]"):
                    # Crashed mid-execution: treat as unchecked, re-execute
                    next_step_idx = i
                    next_step_text = stripped[5:].strip()
                    # Reset to unchecked first
                    lines[i] = line.replace("- [/]", "- [ ]")
                    break
            
            if next_step_idx != -1:
                # Mark as In Progress [/]
                lines[next_step_idx] = lines[next_step_idx].replace("- [ ]", "- [/]")
                new_plan_text = "\n".join(lines)
                
                # Update File (Tick)
                body = re.sub(r'# Plan\n(.*?)\n#', f'# Plan\n{new_plan_text}\n#', body, flags=re.DOTALL)
                with open(filepath, 'w') as f:
                    f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- {body}")
                
                # Execute
                print(f"Executing step: {next_step_text}")
                
                # Check for User Decision Context
                decision_ctx = ""
                if "--- USER DECISION ---" in history_text:
                     last_decision = history_text.split("--- USER DECISION ---")[-1].strip()
                     decision_ctx = f"\nUSER DECISION ON PREVIOUS CONFIRMATION: {last_decision}\n"

                prompt = (
                    f"{user_ctx}\n"
                    f"OBJECTIVE: {request_text}\n"
                    f"CURRENT PLAN:\n{new_plan_text}\n"
                    f"CURRENT STEP: {next_step_text}\n"
                    f"HISTORY SO FAR:\n{history_text}\n"
                    f"{decision_ctx}\n"
                    "INSTRUCTION: Execute this step. Output PLAIN TEXT or TOOL CALLS. "
                    "Do NOT use <thought> or <answer> tags."
                )
                
                result = run_gemini(prompt, user_dir)
                
                # Save Result
                # Mark as Done [x]
                lines[next_step_idx] = lines[next_step_idx].replace("- [/]", "- [x]")
                final_plan_text = "\n".join(lines)
                
                new_history = f"{history_text}\n\n## {next_step_text}\n{result}\n"
                
                body = re.sub(r'# Plan\n(.*?)\n#', f'# Plan\n{final_plan_text}\n#', body, flags=re.DOTALL)
                body = re.sub(r'# History\n(.*)', f'# History\n{new_history}', body, flags=re.DOTALL)
                
                with open(filepath, 'w') as f:
                    f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- {body}")
                
                clear_current_task()
                continue 

            # STEP C: FINALIZE (No unchecked/in-progress items remain)
            # Check <answer> in FULL content, not just history_text (Gemini may have
            # leaked it into step output or it could be in a RESULT block)
            has_answer = "<answer>" in content
            
            if not has_answer:
                print("Finalizing...")
                prompt = (
                    f"{user_ctx}\n"
                    f"OBJECTIVE: {request_text}\n"
                    f"The plan is complete.\n"
                    f"HISTORY:\n{history_text}\n"
                    "INSTRUCTION: Provide the FINAL ANSWER to the user. "
                    "Use <thought> for reasoning and <answer> for the message. "
                    "Use HTML formatting."
                )
                
                result = run_gemini(prompt, user_dir)
                
                with open(filepath, 'a') as f:
                    f.write(f"\n\n--- RESULT ({datetime.now().strftime('%H:%M')}) ---\n{result}\n")
            
            # Archive (whether we just finalized or it was already done)
            print(f"Archiving {filename}...")
            if not os.path.exists(archive_dir): os.makedirs(archive_dir)
            os.rename(filepath, os.path.join(archive_dir, filename))
            
            # Maintenance (Auto Commit)
            subprocess.run([sys.executable, "/app/scripts/git_manager.py", "commit", user_id, f"Task {filename} completed"], check=False)
            
            clear_current_task()

if __name__ == "__main__":
    while True:
        try:
            process_tasks()
        except Exception as e:
            print(f"Runner Loop Error: {e}")
        time.sleep(2)
