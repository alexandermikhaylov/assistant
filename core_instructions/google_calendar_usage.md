# Google Calendar Usage

- The assistant has access to `list-events`, `create-event`, `update-event`, and other tools.
- When working with the calendar, always use `account: 'normal'` unless the user specifies otherwise.
- Before creating or modifying events, you MUST ask for user confirmation.
- Always check the current time via `get-current-time` before searching for or creating events.