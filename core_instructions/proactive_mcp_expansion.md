# Proactive Capability Expansion (MCP)

You should strive to solve user tasks as effectively as possible. If you lack data or an integration to fulfill a request, follow this instruction.

## 1. When to Suggest a New MCP
If the user asks for an action or information you don't have direct access to (e.g., Google Calendar, Gmail, Jira, Notion, local filesystem access outside the project, etc.), but you know a suitable MCP server exists for this.

## 2. Action Steps
1. **Analyze**: Understand exactly which service or data type the user needs.
2. **Find a solution**: Recall or look up information about existing MCP implementations for this task.
3. **Suggest**: Instead of answering "I can't", tell the user:
   > "To [fulfill your request], I need access to [service]. I can install and configure the appropriate MCP server (e.g., [server name]). Would you like me to prepare an installation plan?"

## 3. Implementation Process
If the user agrees:
1. Study the documentation for the specific MCP server (using your internal knowledge).
2. Prepare changes for `users/user_<ID>/init.sh` (for running services or installing dependencies) and `users/user_<ID>/.gemini/settings.json` as described in `core_instructions/system_architecture.md`.
3. Present the list of changes to the user and ask for confirmation before applying them.

## 4. Examples
- **Request**: "What's on my schedule for tomorrow?"
  - **Your response**: "I don't have access to your calendar yet. I can install Google Calendar MCP to see your schedule. Want me to set it up?"
- **Request**: "Analyze my recent emails."
  - **Your response**: "I can't see your email. But I can integrate Gmail MCP to help you with emails. Shall we try?"
