import os
import asyncio
import yaml
import time
import hashlib
import re
import sys
import traceback
import qrcode
import subprocess
from io import BytesIO
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import BufferedInputFile, InputMediaPhoto

import state_inspector as state_inspector
import git_manager as git_manager
from utils import strip_ansi

import json

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
USERS_ROOT = "/app/users"
BRIDGE_LOG = "/app/data/logs/whatsapp_bridge.log"
ALLOWED_USERS_FILE = "/app/config/allowed_users.json"
USER_REGISTRY_FILE = "/app/config/user_registry.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()
start_time = time.time()

# Global storage for active auth processes: user_id -> subprocess.Process
pending_auth_sessions = {}

# Onboarding state: user_id -> { 'state': 'URL'|'TOKEN', 'data': {} }
pending_onboarding = {}

def log_tg(msg):
    print(f"--- [TG GATEWAY] {datetime.now().strftime('%H:%M:%S')} - {msg}", flush=True)

# strip_ansi is imported from utils.py

def is_user_allowed(user_id):
    """Checks if the user_id is in the whitelist."""
    if not os.path.exists(ALLOWED_USERS_FILE):
        log_tg(f"⚠️ Warning: {ALLOWED_USERS_FILE} not found. Allowing everyone (DEBUG mode).")
        return True
        
    try:
        with open(ALLOWED_USERS_FILE, 'r') as f:
            data = json.load(f)
            allowed = data.get("allowed_ids", [])
            return int(user_id) in allowed
    except Exception as e:
        log_tg(f"Error reading allowed users: {e}")
        return False

async def check_access(message: types.Message):
    user_id = str(message.from_user.id)
    
    # 1. Check Whitelist
    if not is_user_allowed(user_id):
        log_tg(f"⛔ Access denied for user {message.from_user.id} ({message.from_user.full_name})")
        await message.answer("⛔ <b>Доступ запрещен.</b>\nВаш ID не найден в белом списке бота.")
        return False
        
    # 2. Check Onboarding Status
    if user_id in pending_onboarding:
        # ALLOW auth commands to bypass onboarding trap
        if message.text and (message.text.startswith('/auth') or message.text.startswith('/gemini_code')):
             return True
             
        await handle_onboarding_message(message)
        return False
        
    # 3. Check Registration
    paths = get_user_paths(user_id)
    if not os.path.exists(paths["tasks"]):
        # Allowed but no folder -> Start Onboarding
        log_tg(f"User {user_id} allowed but not initialized. Starting onboarding.")
        pending_onboarding[user_id] = {'state': 'URL'}
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Вы авторизованы, но мне нужно настроить ваше личное хранилище.\n"
            "Я использую <b>private GitHub repository</b> для хранения всех ваших задач, памяти и настроек.\n\n"
            "Пожалуйста, пришлите мне <b>HTTPS URL</b> вашего приватного репозитория:\n"
            "Пример: <code>https://github.com/username/my-assistant-data.git</code>",
            parse_mode="HTML"
        )
        return False
        
    return True

async def handle_onboarding_message(message: types.Message):
    user_id = str(message.from_user.id)
    state_data = pending_onboarding[user_id]
    state = state_data.get('state')
    text = message.text.strip()
    
    if state == 'URL':
        # Basic validation
        if not text.startswith("https://") or not text.endswith(".git"):
            await message.answer("⚠️ Некорректный URL. Он должен начинаться с <code>https://</code> и заканчиваться на <code>.git</code>.", parse_mode="HTML")
            return
            
        pending_onboarding[user_id]['repo_url'] = text
        pending_onboarding[user_id]['state'] = 'TOKEN'
        await message.answer(
            "✅ URL сохранен.\n\n"
            "Теперь мне нужен <b>GitHub Personal Access Token (Classic)</b> для доступа к этому репозиторию.\n\n"
            "1. Перейдите в GitHub -> Settings -> Developer Settings -> Personal access tokens (Tokens (classic)).\n"
            "2. Создайте новый токен с правами <b>repo</b> (full control).\n"
            "3. Пришлите мне токен (начинается с <code>ghp_</code> или <code>github_pat_</code>).",
            parse_mode="HTML"
        )
        
    elif state == 'TOKEN':
        if not text.startswith("ghp_") and not text.startswith("github_pat_"):
             await message.answer("⚠️ Это не похоже на GitHub Token. Токен обычно начинается с <code>ghp_</code> или <code>github_pat_</code>.")
             return
             
        repo_url = pending_onboarding[user_id]['repo_url']
        
        await message.answer("🔄 Настраиваю подключение и клонирую репозиторий...")
        
        # Save to registry
        try:
            registry = {}
            if os.path.exists(USER_REGISTRY_FILE):
                with open(USER_REGISTRY_FILE, 'r') as f:
                    registry = json.load(f)
            
            registry[user_id] = {
                "repo_url": repo_url,
                "github_pat": text,
                "branch": "main",
                "git_username": message.from_user.full_name or "Assistant User",
                "git_email": f"{user_id}@assistant.bot"
            }
            
            with open(USER_REGISTRY_FILE, 'w') as f:
                json.dump(registry, f, indent=4)
                
            # Trigger setup via Git Manager
            # Run in executor to avoid blocking loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, git_manager.setup_user_repo, user_id, registry[user_id])
            
            # Initialize structure if needed
            try:
                success, msg = await loop.run_in_executor(None, git_manager.initialize_repo_structure, user_id)
            except ValueError:
                # Handle legacy return if not updated hot
                success = False; msg = "Internal error: git_manager updated during execution."
            
            if success:
                pending_onboarding[user_id]['state'] = 'AUTH_REQUIRED'
                await message.answer(
                    "✅ <b>Репозиторий подключен!</b>\n\n"
                    "Теперь нужно авторизовать <b>Google Gemini</b>, чтобы я мог работать.\n"
                    "Я сейчас запущу процесс авторизации. Пожалуйста, перейдите по ссылке, которую я пришлю, и отправьте мне код."
                )
                # Automatically trigger auth
                await authenticate(message, "gemini")
            else:
                 log_tg(f"Onboarding failed: {msg}")
                 await message.answer(f"❌ <b>Ошибка при инициализации репозитория:</b>\n{msg}\n\nПожалуйста, проверьте права токена (нужен <code>repo</code> access) и попробуйте снова (отправьте URL заново).")
                 # Reset state to allow retry
                 pending_onboarding[user_id]['state'] = 'URL'
                 
        except Exception as e:
             log_tg(f"Onboarding error: {e}"); traceback.print_exc()
             await message.answer(f"❌ Произошла ошибка: {e}")

    elif state == 'AUTH_REQUIRED':
        await message.answer(
            "⏳ <b>Ожидаю авторизацию...</b>\n\n"
            "Пожалуйста, перейдите по ссылке выше и пришлите код командой:\n"
            "<code>/auth_code ВАШ_КОД</code>\n\n"
            "Если ссылка истекла, нажмите /auth.",
            parse_mode="HTML"
        )
        
    elif state == 'SURVEY':
        # Create a task for task_runner to process the profile
        paths = get_user_paths(user_id)
        task_filename = f"onboarding_profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        
        task_content = (
            f"# Request\n"
            f"User Onboarding Profile & Analysis\n\n"
            f"The user has just finished onboarding. They provided the following description of their interests:\n"
            f"> {text}\n\n"
            f"**Your Goal:**\n"
            f"1. Create a file `memories/initial_profile.md` containing extracted facts about the user's interests.\n"
            f"2. Analyze their needs and suggest 2-3 specific MCP tools that might be useful (e.g. Football API, Spotify, advanced coding tools, etc.).\n"
            f"3. Create a file `instructions/suggested_tools.md` with these recommendations and how to install them (briefly).\n"
            f"4. Reply to the user with a friendly welcome message, confirming you understood their interests, and listing your recommendations.\n\n"
            f"# Plan\n\n"
            f"# History\n"
        )
        
        metadata = {"message_id": message.message_id, "chat_id": message.chat.id, "user_id": user_id}
        
        try:
            with open(os.path.join(paths["tasks"], task_filename), "w") as f:
                f.write(f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n\n{task_content}\n")
            
            del pending_onboarding[user_id]
            await message.answer("👍 Спасибо! Я анализирую ваш ответ и сейчас вернусь с рекомендациями...")
            # React to indicate processing
            try: await message.react(reaction=[types.ReactionTypeEmoji(emoji="thinking_face")])
            except: pass
            
        except Exception as e:
            log_tg(f"Error saving onboarding task: {e}")
            await message.answer("❌ Ошибка при сохранении ответа.")

def get_user_paths(user_id):
    """Returns dict with paths for specific user."""
    user_dir = os.path.join(USERS_ROOT, f"user_{user_id}")
    return {
        "tasks": os.path.join(user_dir, "tasks"),
        "archive": os.path.join(user_dir, "tasks", "archive"),
        "root": user_dir
    }

def ensure_user_structure(user_id):
    """Creates basic folder structure if new user."""
    paths = get_user_paths(user_id)
    if not os.path.exists(paths["tasks"]):
        os.makedirs(paths["tasks"], exist_ok=True)
        os.makedirs(paths["archive"], exist_ok=True)
        os.makedirs(os.path.join(paths["root"], "memories"), exist_ok=True)
        os.makedirs(os.path.join(paths["root"], "instructions"), exist_ok=True)
    return paths

def find_task_by_msg_id(user_id, msg_id):
    if not msg_id: return None
    paths = get_user_paths(user_id)
    # Use regex with word boundaries to avoid matching 123 in 123456
    patterns = [
        re.compile(rf"message_id:\s*{msg_id}\b"),
        re.compile(rf"last_ai_message_id:\s*{msg_id}\b")
    ]
    
    for folder in [paths["tasks"], paths["archive"]]:
        if not os.path.exists(folder): continue
        for f in sorted(os.listdir(folder), reverse=True):
            if not f.endswith(".md"): continue
            path = os.path.join(folder, f)
            try:
                with open(path, 'r') as file:
                    header = file.read(2000)
                    if any(p.search(header) for p in patterns): return path
            except Exception:
                pass
    return None

async def send_smart_message(chat_id, text, reply_to=None, reply_markup=None, parse_mode="HTML"):
    parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
    last_sent = None
    for i, part in enumerate(parts):
        current_markup = reply_markup if i == len(parts) - 1 else None
        try:
            last_sent = await bot.send_message(
                chat_id=chat_id,
                text=part,
                reply_to_message_id=reply_to if i == 0 else None,
                reply_markup=current_markup,
                parse_mode=parse_mode
            )
        except Exception as e:
            log_tg(f"Send failed ({e}), trying plain text without reply...")
            clean_part = re.sub(r'<[^>]+>', '', part).replace("---FINAL_ANSWER---", "")
            try:
                # Попытка 2: Без HTML и БЕЗ reply_to (так как он мог вызвать ошибку)
                last_sent = await bot.send_message(
                    chat_id=chat_id,
                    text=clean_part,
                    reply_to_message_id=None, 
                    reply_markup=current_markup
                )
            except Exception as e2:
                log_tg(f"Retry failed too: {e2}")
    return last_sent

async def monitor_qr_code():
    log_tg("QR code monitor started.")
    last_pos = 0
    if os.path.exists(BRIDGE_LOG):
        last_pos = os.path.getsize(BRIDGE_LOG)
    
    sent_qr_info = {"hash": None, "timestamp": 0, "message_id": None} 

    while True:
        try:
            if not os.path.exists(BRIDGE_LOG):
                await asyncio.sleep(3)
                continue

            with open(BRIDGE_LOG, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(last_pos)
                lines = f.readlines()
                current_log_pos = f.tell()

                qr_data_string = None
                qr_ascii_block = []
                start_ascii_block = False

                for line in lines:
                    line_clean = strip_ansi(line)
                    if "Device logged out" in line_clean or "device_removed" in line_clean:
                        await bot.send_message(ADMIN_ID, "❌ WhatsApp разлогинен. Используй /qr для нового кода.")
                    if "Emitting QR code" in line_clean:
                        qr_data_string = line_clean.split("Emitting QR code")[-1].strip()
                        log_tg(f"Clean QR data detected: {qr_data_string[:30]}...")
                    if "Scan this QR code" in line_clean:
                        start_ascii_block = True; qr_ascii_block = []
                    elif start_ascii_block:
                        if any(c in line_clean for c in ["█", "▀", "▄", "║", "╔", "╗"]): qr_ascii_block.append(line_clean)
                        elif "Waiting" in line_clean or "▀▀▀▀" in line_clean: start_ascii_block = False

                qr_payload = qr_data_string or ("".join(qr_ascii_block) if qr_ascii_block else None)
                if qr_payload:
                    qr_hash = hashlib.md5(qr_payload.encode()).hexdigest()
                    # Check if hash changed or if it's been a while (to ensure freshness on screen)
                    if qr_hash != sent_qr_info["hash"] or (time.time() - sent_qr_info["timestamp"] > 5):
                        if qr_data_string:
                            log_tg(f"Generating high-quality PNG for: {qr_data_string[:20]}...")
                            qr_img = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
                            qr_img.add_data(qr_data_string); qr_img.make(fit=True)
                            img = qr_img.make_image(fill_color="black", back_color="white")
                            bio = BytesIO(); img.save(bio, 'PNG'); bio.seek(0)
                            qr_bytes = bio.read()
                            
                            media_file = BufferedInputFile(qr_bytes, filename="qr.png")
                            caption = f"🆕 <b>Свежий QR-код (PNG)</b>\nДанные: <code>{qr_data_string[:15]}...</code>"
                            
                            sent = False
                            if sent_qr_info.get("message_id"):
                                try:
                                    await bot.edit_message_media(
                                        chat_id=ADMIN_ID,
                                        message_id=sent_qr_info["message_id"],
                                        media=InputMediaPhoto(media=media_file, caption=caption, parse_mode="HTML")
                                    )
                                    log_tg("QR updated (edited).")
                                    sent = True
                                except Exception as edit_err:
                                    log_tg(f"Edit failed ({edit_err}), sending new...")
                            
                            if not sent:
                                # Re-create file object for new send
                                media_file = BufferedInputFile(qr_bytes, filename="qr.png")
                                msg = await bot.send_photo(chat_id=ADMIN_ID, photo=media_file, caption=caption, parse_mode="HTML")
                                sent_qr_info["message_id"] = msg.message_id
                                log_tg("QR sent (new).")

                        elif qr_ascii_block:
                            log_tg("Sending ASCII fallback...")
                            await bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ <b>QR-код (ASCII):</b>\n<pre>{''.join(qr_ascii_block)}</pre>", parse_mode="HTML")
                        
                        sent_qr_info["hash"] = qr_hash
                        sent_qr_info["timestamp"] = time.time()
                        
                last_pos = current_log_pos
        except Exception as e:
            log_tg(f"QR Monitor error: {e}"); traceback.print_exc()
        await asyncio.sleep(3)

# Alias map: user-friendly names → actual MCP server/tool names
TOOL_ALIASES = {
    # Russian aliases
    "календарь": "google_calendar",
    "калeндарь": "google_calendar",
    "гугл календарь": "google_calendar",
    # English aliases
    "calendar": "google_calendar",
    "google calendar": "google_calendar",
    "gcal": "google_calendar",
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "вотсап": "whatsapp",
    "ватсап": "whatsapp",
}

async def authenticate(message: types.Message, tool: str):
    user_id = str(message.from_user.id)
    
    # 1. WhatsApp Bridge Restart (Special Case)
    if tool.lower() == "whatsapp":
        await message.answer("🔄 Restarting WhatsApp bridge to trigger a new QR code...")
        try:
            ps_output = subprocess.check_output(["ps", "aux"]).decode()
            for line in ps_output.splitlines():
                if "whatsapp/bridge/main" in line:
                    pid = int(line.split()[1])
                    os.kill(pid, 15)
                    await message.answer("✅ Bridge restarted. Monitor logs/Telegram for the QR code.")
                    return
            await message.answer("❌ Bridge process not found.")
        except Exception as e:
            await message.answer(f"Error: {e}")
        return

    # 2. General Tool/Gemini Authentication
    paths = get_user_paths(user_id)
    user_home_dir = paths["root"]
    
    # Kill existing session if any for this user
    if user_id in pending_auth_sessions:
        log_tg(f"Killing old auth session for user {user_id}")
        try:
            pending_auth_sessions[user_id].kill()
        except Exception:
            pass
        del pending_auth_sessions[user_id]
    
    # Map friendly names to trigger prompts
    # If it's just 'gemini', we run it without args. 
    # Otherwise, we ask Gemini to use the tool and list something to force tool-level authentication.
    cmd = ["gemini", "-y"]
    auth_prompt = None
    if tool != "gemini":
        auth_prompt = f"Use {tool} and call its tools to list some information. This is to verify authentication and trigger an OAuth flow if needed."
    
    if tool == "gemini":
        await message.answer(f"🔄 Starting authentication for: <b>Gemini CLI</b>...", parse_mode="HTML")
    else:
        await message.answer(f"🔄 Starting authentication for: <b>{tool}</b>...", parse_mode="HTML")

    log_tg(f"User {user_id} requested auth for tool: {tool}")

    try:
        env = os.environ.copy()
        env['HOME'] = user_home_dir
        env['TERM'] = 'dumb'
        os.makedirs(os.path.join(user_home_dir, '.gemini'), exist_ok=True)

        process = await asyncio.create_subprocess_exec(
            *cmd, 
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd="/app"
        )
        
        # Feed prompt via stdin (same approach as run_gemini in task_runner.py)
        if auth_prompt:
            process.stdin.write(auth_prompt.encode())
            await process.stdin.drain()
        
        # DO NOT close stdin here! We need it open to send the code later if requested (for gemini -y or tools).
        # process.stdin.close() 
        # await process.stdin.wait_closed()
        
        auth_url = None
        exit_reason = "unknown"
        start_search = time.time()
        output_acc_raw = b""
        log_buffer = ""

        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(8192), timeout=2)
                if not chunk:
                    exit_reason = "process_stdout_closed"
                    break
                
                output_acc_raw += chunk
                
                # Check for URLs in stdout chunk
                chunk_str = chunk.decode(errors='ignore')
                log_tg(f"Auth: chunk +{len(chunk)}b: {chunk_str[:50]}...")
                
                output_acc = strip_ansi(output_acc_raw.decode(errors='ignore'))
                
                # Regex for OAuth URL (Gemini standard)
                urls = re.findall(r'https://[^\s\x00-\x1f\x7f-\xff\x1b]+', output_acc)
                if urls:
                    log_tg(f"Auth: found URLs in stdout: {urls}")
                    for url in urls:
                        if "accounts.google.com" in url and any(x in url for x in [
                            'redirect_uri',
                            'response_type',
                            'client_id',
                            'scope',
                            'consent',
                            'authorization',
                        ]):
                            auth_url = url.strip().rstrip('.:,;)]\'"')
                            break
                
                if auth_url:
                    exit_reason = "oauth_url_found"
                    break
                if "Enter the authorization code" in output_acc:
                    exit_reason = "auth_code_prompt"
                    break
                if "Final Answer" in output_acc:
                    exit_reason = "final_answer"
                    break
                    
            except asyncio.TimeoutError:
                elapsed = time.time() - start_search
                # Log periodic status during wait
                if int(elapsed) % 10 == 0:
                    log_tg(f"Auth: waiting... {elapsed:.0f}s elapsed, {len(output_acc_raw)} bytes so far")
                if elapsed > 55:
                    exit_reason = "timeout"
                    break
                continue
        
        log_tg(f"Auth: DONE. exit_reason={exit_reason}, total_bytes={len(output_acc_raw)}, output:\n{output_acc[:1000]}")
        
        if auth_url:
            pending_auth_sessions[user_id] = process
            await message.answer(f"🔑 <b>Authorize {tool.capitalize()}</b>\n\nPlease open this URL:\n{auth_url}\n\nThen reply with:\n<code>/auth_code YOUR_CODE</code>", parse_mode="HTML")
        else:
            if exit_reason == "final_answer" or not output_acc:
                 await message.answer(f"✅ <b>{tool.capitalize()}</b> is already authenticated.")
                 # If user was in AUTH_REQUIRED Onboarding state, proceed to Survey immediately
                 if user_id in pending_onboarding and pending_onboarding[user_id].get('state') == 'AUTH_REQUIRED':
                    pending_onboarding[user_id]['state'] = 'SURVEY'
                    await message.answer(
                        "✅ <b>Отлично! Теперь мы готовы к работе.</b>\n\n"
                        "Чтобы я мог лучше помогать вам, расскажите немного о себе.\n"
                        "<b>Какие у вас основные цели и интересы?</b> (например: программирование, спорт, изучение языков...)\n\n"
                        "Напишите ответ, и я подготовлю рекомендации."
                    )
            else:
                await message.answer(f"⚠️ No OAuth URL found for <b>{tool}</b>. Check if it's already working.\n\n<pre>{output_acc[:500]}</pre>", parse_mode="HTML")
            process.kill()

    except Exception as e:
        log_tg(f"Auth error: {e}"); traceback.print_exc()
        await message.answer(f"❌ Error: {e}")

@dp.message(Command("auth"))
async def cmd_auth(message: types.Message, command: Command):
    if not await check_access(message): return
    tool = command.args or "gemini"
    await authenticate(message, tool)

@dp.message(Command("auth_code", "gemini_code"))
async def cmd_auth_code(message: types.Message, command: Command):
    if not await check_access(message): return
    user_id = str(message.from_user.id)
    code = command.args
    
    if not code:
        await message.answer("Please provide the authorization code: `/auth_code YOUR_CODE`")
        return

    process = pending_auth_sessions.get(user_id)
    if not process:
        await message.answer("❌ No active authentication session found. Please run /auth first.")
        return
    
    await message.answer(f"🔄 Finalizing authentication...")
    
    try:
        # Send the code to the waiting process
        process.stdin.write(f"{code}\n".encode())
        await process.stdin.drain()
        
        # Wait for the process to complete
        try:
            stdout_data, _ = await asyncio.wait_for(process.communicate(), timeout=20)
            output = strip_ansi(stdout_data.decode())
        except asyncio.TimeoutError:
            process.kill()
            output = "Process timed out during finalization."
        
        log_tg(f"Auth finalization output: {output}")
        
        # Check for success indicators. 
        # Note: Gemini CLI might exit with 42 (No Input) if we passed -y but no prompt, 
        # but if it says "Loaded cached credentials", auth worked.
        is_success = (
            process.returncode == 0 or 
            "Welcome" in output or 
            "Authenticated" in output or 
            "Final Answer" in output or
            "Loaded cached credentials" in output
        )

        if is_success:
            await message.answer("✅ Authentication successful!")
            
            # ONBOARDING TRIGGER:
            if user_id in pending_onboarding and pending_onboarding[user_id].get('state') == 'AUTH_REQUIRED':
                pending_onboarding[user_id]['state'] = 'SURVEY'
                await message.answer(
                    "🎉 <b>Авторизация пройдена!</b>\n\n"
                    "Последний шаг: чтобы я мог лучше помогать вам, расскажите немного о себе.\n"
                    "<b>Какие у вас основные цели и интересы?</b> (например: программирование, спорт, изучение языков, управление проектами...)\n\n"
                    "Напишите ответ, и я подготовлю для вас рекомендации."
                )

        else:
            await message.answer(f"⚠️ Output (Exit code {process.returncode}):\n```\n{output[:1000]}\n```")
            
    except Exception as e:
        log_tg(f"Error during code finalization: {e}"); traceback.print_exc()
        await message.answer(f"❌ Error finalizing authentication: {e}")
    finally:
        if user_id in pending_auth_sessions:
            del pending_auth_sessions[user_id]

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if not await check_access(message): return
    await send_smart_message(message.chat.id, state_inspector.get_full_state(message.from_user.id))

@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    if not await check_access(message): return
    await send_smart_message(message.chat.id, state_inspector.get_current_tasks(message.from_user.id))

@dp.message(Command("memories"))
async def cmd_memories(message: types.Message):
    if not await check_access(message): return
    await send_smart_message(message.chat.id, state_inspector.get_memories_summary(message.from_user.id))

@dp.message_reaction()
async def handle_reaction(reaction: types.MessageReactionUpdated):
    if not is_user_allowed(reaction.user.id): return
    user_id = reaction.user.id
    task_path = find_task_by_msg_id(user_id, reaction.message_id)
    if task_path:
        emoji = reaction.new_reaction[-1].emoji if reaction.new_reaction else "removed"
        with open(task_path, 'a') as f: f.write(f"\n\n--- USER REACTION ({datetime.now()}) ---\nEmoji: {emoji}\n")

@dp.callback_query(F.data.startswith("conf_"))
async def handle_confirmation(callback: types.CallbackQuery):
    if not is_user_allowed(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    user_id = callback.from_user.id
    _, decision, filename = callback.data.split("_", 2)
    paths = get_user_paths(user_id)
    
    # Try finding in tasks first, then archive
    filepath = os.path.join(paths["tasks"], filename)
    if not os.path.exists(filepath):
        filepath = os.path.join(paths["archive"], filename)

    if os.path.exists(filepath):
        res_text = "✅ Да" if decision == "yes" else "❌ Нет"
        with open(filepath, 'a') as f: f.write(f"\n\n--- USER DECISION ---\n{res_text}\n")
        # Move back to active tasks if it was archived
        if paths["archive"] in filepath:
            new_path = os.path.join(paths["tasks"], os.path.basename(filepath))
            os.rename(filepath, new_path)
        
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer()
        try: await callback.message.react(reaction=[types.ReactionTypeEmoji(emoji="✍️")])
        except Exception: pass

@dp.message()
async def handle_message(message: types.Message):
    if not await check_access(message): return
    user_id = str(message.from_user.id)
    paths = ensure_user_structure(message.from_user.id)
    
    # Defaults
    parent_task_id = None
    
    # Check for Reply -> Parent Task logic
    if message.reply_to_message:
        target_msg_id = message.reply_to_message.message_id
        parent_path = find_task_by_msg_id(user_id, target_msg_id)
        if parent_path:
            parent_task_id = os.path.basename(parent_path)
    
    # Always create a NEW task
    task_filename = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(message.text.encode()).hexdigest()[:4]}.md"
    
    metadata = {
        "task_id": task_filename, # using filename as ID for simplicity
        "user_id": user_id,
        "chat_id": message.chat.id,
        "trigger_message_id": message.message_id,
        "parent_task_id": parent_task_id,
        "status": "planning", # Start in planning mode
        "created_at": datetime.now().isoformat()
    }
    
    # Initial Content Structure
    file_content = (
        f"--- \n{yaml.dump(metadata, allow_unicode=True)}--- \n\n"
        f"# Request\n{message.text}\n\n"
        f"# Plan\n\n" # Empty plan signals the runner to generate one
        f"# History\n"
    )

    with open(os.path.join(paths["tasks"], task_filename), "w") as f:
        f.write(file_content)
    
    # React to confirm receipt
    try: await message.react(reaction=[types.ReactionTypeEmoji(emoji="👀")])
    except: pass

async def main():
    log_tg("Bot starting (Multi-user mode ready)...")
    # Start notifying results separately
    asyncio.create_task(state_inspector.notify_results(bot, send_smart_message))
    asyncio.create_task(monitor_qr_code())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
