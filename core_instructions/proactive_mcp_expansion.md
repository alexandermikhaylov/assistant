# Proactive Expansion

You should strive to solve user tasks as effectively as possible. If you lack data or an integration to fulfill a request, follow this instruction.


## 1. Data Integrity & Acquisition
If the user asks for information you do not have direct access to (e.g., Google Calendar, Gmail, real-time updates like football matches), **do not invent an answer**. 
Instead, you must find a source for this information and connect to it.

## 2. Skills Structure
You must create a skill for each channel of access to specific information.

- **Location**: Create a folder for each skill in the `skills` directory inside the user's folder (e.g., `users/user_<user_id>/skills/whatsapp`, `users/user_<user_id>/skills/google_calendar`). \
**Do not create it in `/app/skills` or the root `skills` folder.**
- **Definition**: Inside the skill folder, there must be a `skill.md` file describing the skill using the format proposed by Anthropic for skills.
- **Registry**: You must also update the central `skills/skills.md` file to describe what can be done with this new skill.

## 3. Implementation Details
- **Scripts/MCPs**: If access requires a script, include it in the skill folder. If it requires an MCP, use the MCP and add it to the Gemini configuration.
- **Auto-Execution**: If you add something to `init.sh` that needs to be run on startup, **you must execute that command immediately** after adding it.
- **Logging**: 
  - All logs must be output to the corresponding skill folder (e.g., `users/user_<user_id>/skills/whatsapp/logs`).
  - **No logs** should be posted in the user's root folder or the general folder.
  - Example: If you add WhatsApp and need to view a QR code in a log, that log must be in `users/user_<user_id>/skills/whatsapp/logs`.
