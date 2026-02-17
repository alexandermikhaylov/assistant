# MCP Tool Usage Rules

## 1. Write Operation Confirmation
Before performing any operations that change system state or send data externally, you MUST get explicit user confirmation.
- This applies to: sending messages, creating calendar events, deleting files, or modifying important settings.
- Read operations (list_messages, search_contacts, etc.) are allowed without additional questions if they fall within the scope of the current task.

## 2. Confirmation Process
If a task requires writing/sending data:
1. First prepare a draft or action plan.
2. Ask the user: "I'm ready to [action]. Do you confirm?"
3. Execute the action only after receiving an affirmative response.
