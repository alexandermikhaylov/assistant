# AI Assistant with MCP Architecture

A personal AI assistant designed to run on your local machine or server. It leverages **Gemini 2.0 Flash** for reasoning and task execution, and connects to the world via **Telegram**.

Its core strength is the **Model Context Protocol (MCP)**, allowing it to easily integrate with external tools like WhatsApp, Google Calendar, and more.

## Key Features

*   **Multi-User Architecture**: Supports multiple isolated users, each with their own memories, tasks, and integrations.
*   **Persistent Memory**: Remembers facts about you and your preferences, automatically extracted from conversations.
*   **Task Automation**: Handles one-off and recurrent tasks (reminders, daily digests).
*   **Telegram Interface**: Chat with your assistant, send voice messages, and receive proactive notifications.
*   **Extensible (MCP)**: Add new capabilities by plugging in standard MCP servers.
*   **Git-backed User Data**: Each user's data can be synced to a private Git repository.

## Default Integrations

*   **WhatsApp**: Read/send messages via a self-hosted bridge (requires scanning a QR code).
*   **Google Calendar**: Check schedules and create events.

## Project Structure

```
/app
├── core_instructions/  # Base system prompts and rules (English)
├── scripts/            # Python logic
│   ├── telegram_gateway.py  # Telegram bot interface
│   ├── task_runner.py       # Task queue processor (via Gemini CLI)
│   ├── heartbeat.py         # Recurrent task scheduler
│   ├── state_inspector.py   # System state & notification delivery
│   ├── git_manager.py       # Per-user Git repo management
│   └── utils.py             # Shared utilities
├── config/
│   ├── allowed_users.json   # User whitelist
│   └── user_registry.json   # Per-user Git config (gitignored — contains secrets)
├── users/              # User data isolation
│   └── user_<ID>/      # Specific user folder (based on Telegram ID)
│       ├── memories/   # Auto-extracted user facts
│       ├── tasks/      # User's task queue
│       ├── instructions/ # User-specific instructions
│       ├── config/     # User-specific credentials (e.g., Calendar)
│       ├── mcp-servers/# User's private integrations
│       ├── init.sh     # (Optional) User init script
│       └── .gemini/settings.json  # User's Gemini CLI & MCP settings
├── docker-compose.yml
├── Dockerfile
└── entrypoint.sh
```

## Getting Started

1.  **Clone the repo**.
2.  **Configure `.env`** (see `.env.example`):
    ```bash
    TELEGRAM_BOT_TOKEN=your_bot_token
    TELEGRAM_ADMIN_ID=your_telegram_id
    ```
3.  **Run with Docker**:
    ```bash
    docker-compose up -d --build
    ```
4.  **Start Chatting**: Send `/start` to your bot in Telegram.

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Begin interacting with the assistant |
| `/status` | View system status (running task, queue, memories) |
| `/tasks` | View your active and recurrent tasks |
| `/memories` | View stored facts about you |
| `/auth <tool>` | Authenticate a tool (e.g., `/auth google_calendar`, `/auth whatsapp`) |
| `/gemini_code <code>` | Submit an OAuth authorization code |

## Adding New Integrations (MCP)

To add a new tool (e.g., Jira, Linear, Filesystem):

1.  **Add Server**: Place the MCP server code in `users/user_<ID>/mcp-servers/`.
2.  **Register**: Update `users/user_<ID>/.gemini/settings.json` to include the new server command.
3.  **Dependencies**: Add installation steps to `users/user_<ID>/init.sh`.
4.  **Restart**: The assistant will automatically pick up the new tools.

## Troubleshooting

*   **WhatsApp Auth**: If the assistant asks to scan a QR code, check the Telegram chat.
*   **Gemini Auth**: Use `/auth gemini` and follow the OAuth flow.
*   **Logs**: Check `docker-compose logs -f` for detailed execution traces.
