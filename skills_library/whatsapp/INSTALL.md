# WhatsApp Skill — Installation Guide

This guide is for **Gemini** to follow when a user requests WhatsApp integration.

## Overview

Uses [whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) — a Go bridge (whatsmeow) + Python MCP server that provides WhatsApp read/write access via MCP tools.

**Components:**
- **Go WhatsApp Bridge**: Connects to WhatsApp Web API, handles auth via QR code, stores messages in SQLite
- **Python MCP Server**: Exposes MCP tools (search contacts, list/send messages, send files, etc.)

---

## Step 1: Prerequisites

Ensure the following are available in the container. Add to the user's `init.sh` if needed:

```bash
# Go (should already be installed globally)
which go || echo "ERROR: Go is required"

# Python + UV
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh

# FFmpeg (optional, for voice messages)
which ffmpeg || apt-get install -y ffmpeg
```

---

## Step 2: Clone and Build the Bridge

Add to the user's `init.sh`:

```bash
USER_DIR="/app/users/user_<USER_ID>"
SKILL_DIR="$USER_DIR/skills/whatsapp"
MCP_DIR="$USER_DIR/mcp-servers/whatsapp-mcp"
LOG_DIR="$SKILL_DIR/logs"

mkdir -p "$SKILL_DIR" "$LOG_DIR"

# Clone if not present
if [ ! -d "$MCP_DIR" ]; then
    git clone https://github.com/lharries/whatsapp-mcp.git "$MCP_DIR"
fi

# Build the Go bridge
cd "$MCP_DIR/whatsapp-bridge"
go build -o whatsapp-bridge main.go
```

---

## Step 3: Start the Bridge as a Background Process

Add to the user's `init.sh` (after the build step):

```bash
# Start WhatsApp bridge in background
cd "$MCP_DIR/whatsapp-bridge"
./whatsapp-bridge > "$LOG_DIR/bridge.log" 2>&1 &
echo $! > "$SKILL_DIR/bridge.pid"
echo "WhatsApp bridge started (PID: $(cat $SKILL_DIR/bridge.pid))"
```

**IMPORTANT**: After adding to `init.sh`, execute the commands immediately — don't wait for container restart.

---

## Step 4: QR Code Authentication

The bridge outputs a QR code to the terminal on first run. **The user needs to scan this QR code with their WhatsApp mobile app.**

### How to get the QR code to the user:

1. Read the bridge log file from the **skill's log folder** (NOT `/app/data/`):
   ```
   # Log is at: /app/users/user_<USER_ID>/skills/whatsapp/logs/bridge.log
   cat $SKILL_DIR/logs/bridge.log
   ```

2. The QR code will appear as a text string in the log. Extract the QR data string.

3. **Generate a QR code image** (ASCII art is NOT readable in Telegram due to line length):
   ```bash
   pip install qrcode pillow
   python3 -c "
   import qrcode
   qr = qrcode.make('QR_STRING_FROM_LOG')
   qr.save('$SKILL_DIR/logs/qr_code.png')
   "
   ```

4. **Send the QR image to the user via Telegram** using your `<answer>` block:
   ```
   <answer>
   To connect WhatsApp, please scan this QR code with your phone:
   Open WhatsApp → Settings → Linked Devices → Link a Device

   [Attach the QR image from $SKILL_DIR/logs/qr_code.png]
   </answer>
   ```

5. **Wait for confirmation**: After sending the QR code, set the task status to `needs_user_input` and wait for the user to confirm they've scanned it.

6. **Verify connection**: Check the skill's log for successful authentication:
   ```
   grep -i "connected\|logged in\|paired" $SKILL_DIR/logs/bridge.log
   ```

### Re-authentication
The WhatsApp session lasts approximately **20 days**. After that, a new QR code scan is required. The bridge will output a new QR code automatically.

---

## Step 5: Configure MCP Server in Gemini Settings

Add the WhatsApp MCP server to the user's `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "whatsapp": {
      "command": "uv",
      "args": [
        "--directory",
        "/app/users/user_<USER_ID>/mcp-servers/whatsapp-mcp/whatsapp-mcp-server",
        "run",
        "main.py"
      ]
    }
  }
}
```

**Note**: Replace `<USER_ID>` with the actual user ID. Use absolute paths.

If the user already has other MCP servers configured, **merge** this entry into the existing `mcpServers` object — don't overwrite.

---

## Step 6: Create Skill Definition

Create `$SKILL_DIR/skill.md`:

```markdown
# WhatsApp Skill

Access WhatsApp messages and contacts via MCP.

## Available Tools
- `search_contacts` — Search contacts by name or phone number
- `list_messages` — Retrieve messages with filters
- `list_chats` — List available chats with metadata
- `get_chat` — Get info about a specific chat
- `get_direct_chat_by_contact` — Find direct chat with a contact
- `get_contact_chats` — List all chats involving a contact
- `get_last_interaction` — Get most recent message with a contact
- `get_message_context` — Retrieve context around a message
- `send_message` — Send a message to a phone number or group
- `send_file` — Send image, video, document
- `send_audio_message` — Send voice message (requires ffmpeg)
- `download_media` — Download media from a message

## Usage Notes
- Read operations are allowed without confirmation
- Send/write operations MUST get user confirmation first
- Phone numbers use international format (e.g., +1234567890)
- Group JIDs look like: 1234567890-1234567890@g.us
```

---

## Step 7: Update Skills Registry

Update `$USER_DIR/skills/skills.md` to include WhatsApp:

```markdown
## WhatsApp
- **Status**: Active
- **Tools**: search_contacts, list_messages, list_chats, send_message, send_file, send_audio_message, download_media
- **Notes**: Requires authenticated WhatsApp session. Re-auth needed every ~20 days.
```

---

## Step 8: Verify Installation

Run these checks:

1. **Bridge is running**:
   ```bash
   ps aux | grep whatsapp-bridge
   ```

2. **MCP server responds**:
   ```bash
   cd $MCP_DIR/whatsapp-mcp-server && uv run main.py --help
   ```

3. **Test a read operation**: After configuration, try using `list_chats` tool to verify the MCP connection works.

---

## Troubleshooting

### Bridge crashes immediately
- Check `$SKILL_DIR/logs/bridge.log` for errors
- Ensure Go is installed and `go build` succeeded
- Ensure SQLite CGO is enabled: `go env CGO_ENABLED` should return `1`

### QR code not appearing
- The QR code only appears on first run or when re-authentication is needed
- Check if `store.db` exists in the bridge directory — if so, the session might already be active

### "Client outdated" error
- Update the whatsmeow dependency:
  ```bash
  cd $MCP_DIR/whatsapp-bridge
  go get -u go.moe.sb/whatsmeow@latest
  go build -o whatsapp-bridge main.go
  ```

### MCP server can't find the database
- The SQLite database (`store.db` and `messages.db`) is created by the Go bridge in its working directory
- Ensure the MCP server can access the same directory
- The default path is `$MCP_DIR/whatsapp-bridge/`

### Session expired (after ~20 days)
- Delete `store.db` from the bridge directory
- Restart the bridge — a new QR code will appear
- Follow Step 4 again to re-authenticate
