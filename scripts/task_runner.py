import os
import sys
import re
import yaml
import json
import subprocess
import time
import glob
from datetime import datetime, timedelta
from utils import strip_ansi

USERS_ROOT = "/app/users"
CORE_INSTRUCTIONS_DIR = "/app/core_instructions"
CURRENT_TASK_FILE = "/app/data/current_task.json"
GEMINI_BIN = "gemini"

class QuotaExhaustedError(Exception):
    def __init__(self, wait_seconds, message=""):
        self.wait_seconds = wait_seconds
        self.message = message
        super().__init__(f"Quota exhausted. Retry after {wait_seconds}s: {message}")

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

GEMINI_MCP_ALLOWED_KEYS = {"command", "args", "env", "cwd", "timeout", "url", "headers"}

def sanitize_gemini_config(user_dir):
    """Remove unrecognized keys from mcpServers entries that cause Gemini CLI to reject the config."""
    settings_path = os.path.join(user_dir, ".gemini", "settings.json")
    if not os.path.exists(settings_path):
        return
    
    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
        
        mcp = settings.get("mcpServers")
        if not isinstance(mcp, dict):
            if isinstance(mcp, list):
                settings["mcpServers"] = {}
                with open(settings_path, 'w') as f:
                    json.dump(settings, f, indent=2)
                print(f"  -> Fixed mcpServers: was list, reset to dict.", flush=True)
            return
        
        changed = False
        for name, server_config in list(mcp.items()):
            if not isinstance(server_config, dict):
                continue
            
            # Remove unrecognized keys
            bad_keys = set(server_config.keys()) - GEMINI_MCP_ALLOWED_KEYS
            if bad_keys:
                print(f"  -> Removing invalid keys from mcpServers.{name}: {bad_keys}", flush=True)
                for k in bad_keys:
                    del server_config[k]
                changed = True
            
            # Remove MCP servers whose command doesn't exist or is malformed
            cmd = server_config.get("command", "")
            args_list = server_config.get("args", [])
            
            # Check for shell redirects stuffed into command (malformed)
            full_cmd_str = f"{cmd} {' '.join(args_list) if isinstance(args_list, list) else ''}"
            if ">" in full_cmd_str or "|" in full_cmd_str:
                print(f"  -> Removing malformed MCP server '{name}': contains shell redirects.", flush=True)
                del mcp[name]
                changed = True
                continue
            
            # Check if command binary exists
            if cmd:
                import shutil
                cmd_exists = shutil.which(cmd) or os.path.exists(cmd)
                if not cmd_exists:
                    print(f"  -> Removing broken MCP server '{name}': command '{cmd}' not found.", flush=True)
                    del mcp[name]
                    changed = True
                    continue
                
                # Check if script file in args exists
                if isinstance(args_list, list):
                    for arg in args_list:
                        if arg.endswith(('.py', '.js', '.sh')) and not os.path.exists(arg):
                            print(f"  -> Removing broken MCP server '{name}': script '{arg}' not found.", flush=True)
                            del mcp[name]
                            changed = True
                            break
        
        if changed:
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"  -> WARNING: Could not sanitize config: {e}", flush=True)

MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]

def _call_gemini(prompt, user_dir, model, yolo=True, timeout=120):
    """Low-level Gemini CLI call. Returns (stdout, stderr, returncode) or raises TimeoutExpired."""
    args = [GEMINI_BIN, "--model", model]
    if yolo: args.append("-y")
    
    env = os.environ.copy()
    env['HOME'] = user_dir
    
    result = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, env=env)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

def _parse_quota_error(stderr):
    """Check stderr for quota exhaustion. Returns wait_seconds or None."""
    if "QuotaError" in stderr or "exhausted your capacity" in stderr:
        wait_match = re.search(r'reset after\s+((?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?)', stderr)
        wait_secs = 600  # default 10 min
        if wait_match:
            h = int(wait_match.group(2) or 0)
            m = int(wait_match.group(3) or 0)
            s = int(wait_match.group(4) or 0)
            wait_secs = h * 3600 + m * 60 + s
        return wait_secs
    return None

def run_gemini(prompt, user_dir, yolo=True, timeout=300):
    # Self-heal config before each call
    sanitize_gemini_config(user_dir)
    
    min_wait = None
    
    try:
        for i, model in enumerate(MODELS):
            print(f"  -> Trying model: {model}", flush=True)
            
            try:
                stdout, stderr, rc = _call_gemini(prompt, user_dir, model, yolo, timeout)
            except subprocess.TimeoutExpired:
                print(f"  -> TIMEOUT on {model} after {timeout}s. Trying next...", flush=True)
                if i < len(MODELS) - 1:
                    continue
                else:
                    print(f"  -> All models timed out.", flush=True)
                    return ""
            
            if rc != 0:
                print(f"  -> Exit code: {rc}", flush=True)
            if stderr:
                print(f"  -> Stderr: {stderr[:500]}", flush=True)
            
            # Check for quota exhaustion
            quota_wait = _parse_quota_error(stderr)
            if quota_wait is not None:
                min_wait = min(min_wait, quota_wait) if min_wait else quota_wait
                if i < len(MODELS) - 1:
                    print(f"  -> Quota exhausted on {model}. Trying next...", flush=True)
                    continue
                else:
                    # All models exhausted
                    raise QuotaExhaustedError(min_wait, stderr[:200])
            
            # Model worked (or at least didn't hit quota)
            if not stdout:
                print(f"  -> Stdout is EMPTY. Return code: {rc}", flush=True)
                if stderr and rc == 0:
                    return stderr
            
            return stdout
        
        return ""  # Should not reach here
    except QuotaExhaustedError:
        raise
    except Exception as e:
        print(f"  -> Gemini subprocess error: {e}", flush=True)
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
        files.sort() 
        
        if not files: continue

        user_ctx = get_context(user_dir)

        for filename in files:
            filepath = os.path.join(tasks_dir, filename)
            
            try:
                with open(filepath, 'r') as f: content = f.read()
                
                # Split ONLY on the first two '---' (YAML frontmatter delimiters)
                parts = content.split('---', 2)
                if len(parts) < 3: continue
                
                metadata = yaml.safe_load(parts[1]) or {}
                
                # CHECK BLOCKED STATUS
                # 1. Explicit <confirm> tag without user decision
                if "<confirm>" in content and "--- USER DECISION ---" not in content.split("<confirm>")[-1]:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Skipping {filename} (waiting for confirmation)", flush=True)
                    continue
                # 2. Task explicitly marked as needing user input
                task_status = metadata.get('status', '')
                if task_status in ('needs_user_input', 'blocked', 'deferred_quota'):
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Skipping {filename} (status: {task_status})", flush=True)
                    continue

                set_current_task(filename, user_id)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Processing {filename}...", flush=True)

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
                    print(f"  -> State: PLAN_NEEDED", flush=True)
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
                        if "# Plan" in body:
                            new_body = re.sub(r'# Plan\s*\n', f'# Plan\n{plan}\n\n', body, count=1)
                        else:
                            new_body = body.strip() + f"\n\n# Plan\n{plan}\n\n# History\n"
                        
                        with open(filepath, 'w') as f:
                            f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n{new_body}")
                        print(f"  -> Plan saved.", flush=True)
                    else:
                        print(f"  -> WARNING: Gemini returned empty plan.", flush=True)
                    continue

                # STEP B: EXECUTE NEXT ITEM
                lines = plan_text.splitlines()
                next_step_idx = -1
                next_step_text = ""
                
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith("- [ ]"):
                        next_step_idx = i
                        next_step_text = stripped[5:].strip()
                        break
                    elif stripped.startswith("- [/]"):
                        next_step_idx = i
                        next_step_text = stripped[5:].strip()
                        lines[i] = line.replace("- [/]", "- [ ]")
                        print(f"  -> Recovering stuck [/] step: {next_step_text}", flush=True)
                        break
                
                if next_step_idx != -1:
                    print(f"  -> State: EXECUTING step {next_step_idx+1}: {next_step_text}", flush=True)
                    
                    # Mark as In Progress [/]
                    lines[next_step_idx] = lines[next_step_idx].replace("- [ ]", "- [/]")
                    new_plan_text = "\n".join(lines)
                    
                    # Update File (Tick)
                    body = re.sub(r'# Plan\n(.*?)\n#', f'# Plan\n{new_plan_text}\n#', body, flags=re.DOTALL)
                    with open(filepath, 'w') as f:
                        f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- {body}")
                    
                    # Execute
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
                    
                    if result:
                        # Mark as Done [x]
                        lines[next_step_idx] = lines[next_step_idx].replace("- [/]", "- [x]")
                        print(f"  -> Step done.", flush=True)
                    else:
                        # Gemini failed — mark as failed [!] so we don't loop forever
                        lines[next_step_idx] = lines[next_step_idx].replace("- [/]", "- [!]")
                        result = "(Gemini returned empty — step skipped)"
                        print(f"  -> Step FAILED (empty result).", flush=True)
                    
                    final_plan_text = "\n".join(lines)
                    new_history = f"{history_text}\n\n## {next_step_text}\n{result}\n"
                    
                    body = re.sub(r'# Plan\n(.*?)\n#', f'# Plan\n{final_plan_text}\n#', body, flags=re.DOTALL)
                    body = re.sub(r'# History\n(.*)', f'# History\n{new_history}', body, flags=re.DOTALL)
                    
                    with open(filepath, 'w') as f:
                        f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- {body}")
                    continue 

                # STEP C: FINALIZE (No unchecked/in-progress items remain)
                has_answer = "<answer>" in content
                
                if not has_answer:
                    print(f"  -> State: FINALIZING (all steps done, generating answer)...", flush=True)
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
                    
                    # If Gemini didn't include <answer> tags, wrap the whole result
                    if result and "<answer>" not in result:
                        print(f"  -> WARNING: Gemini didn't use <answer> tags, wrapping.", flush=True)
                        result = f"<thought>Plan complete.</thought><answer>{result}</answer>"
                    
                    with open(filepath, 'a') as f:
                        f.write(f"\n\n--- RESULT ({datetime.now().strftime('%H:%M')}) ---\n{result}\n")
                else:
                    print(f"  -> State: ALREADY FINISHED (has <answer>).", flush=True)
                
                # Archive unconditionally
                print(f"  -> Archiving {filename}...", flush=True)
                if not os.path.exists(archive_dir): os.makedirs(archive_dir)
                os.rename(filepath, os.path.join(archive_dir, filename))
                
                # Maintenance (Auto Commit)
                subprocess.run([sys.executable, "/app/scripts/git_manager.py", "commit", user_id, f"Task {filename} completed"], check=False)
                print(f"  -> DONE.", flush=True)

            except QuotaExhaustedError as qe:
                # Per-user deferral: move task to recurrent/ with run_after
                print(f"  -> QUOTA EXHAUSTED for user {user_id}. Deferring task for {qe.wait_seconds}s.", flush=True)
                try:
                    # Revert any in-progress [/] steps back to [ ]
                    with open(filepath, 'r') as f:
                        current_content = f.read()
                    current_content = current_content.replace("- [/]", "- [ ]")
                    
                    # Update metadata with run_after
                    run_after_dt = datetime.now() + timedelta(seconds=qe.wait_seconds)
                    metadata['run_after'] = run_after_dt.isoformat()
                    metadata['status'] = 'deferred_quota'
                    
                    parts = current_content.split('---', 2)
                    if len(parts) >= 3:
                        deferred_content = f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- {parts[2]}"
                    else:
                        deferred_content = current_content
                    
                    # Move to recurrent/
                    recurrent_dir = os.path.join(tasks_dir, "recurrent")
                    os.makedirs(recurrent_dir, exist_ok=True)
                    dest = os.path.join(recurrent_dir, filename)
                    with open(dest, 'w') as f:
                        f.write(deferred_content)
                    os.remove(filepath)
                    
                    wait_min = qe.wait_seconds // 60
                    print(f"  -> Moved {filename} to recurrent/ (run_after: {run_after_dt.strftime('%H:%M')})", flush=True)
                    
                    # Queue notification for user
                    chat_id = metadata.get('chat_id')
                    if chat_id:
                        notif_dir = "/app/data/notifications"
                        os.makedirs(notif_dir, exist_ok=True)
                        notif_file = os.path.join(notif_dir, f"{filename}_{int(time.time())}.json")
                        with open(notif_file, 'w') as nf:
                            json.dump({
                                "chat_id": chat_id,
                                "text": f"⏸ <b>Quota exceeded.</b> Your task is deferred.\n\nI'll retry automatically in ~{wait_min} minutes ({run_after_dt.strftime('%H:%M')}).",
                                "parse_mode": "HTML"
                            }, nf)
                except Exception as move_err:
                    print(f"  -> ERROR deferring task: {move_err}", flush=True)
                # Continue to next task (don't block other users)

            except Exception as e:
                print(f"  -> ERROR processing {filename}: {e}", flush=True)
                import traceback
                traceback.print_exc()
            finally:
                clear_current_task()

if __name__ == "__main__":
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Task runner started.", flush=True)
    while True:
        try:
            process_tasks()
        except Exception as e:
            print(f"Runner Loop Error: {e}", flush=True)
        time.sleep(2)
