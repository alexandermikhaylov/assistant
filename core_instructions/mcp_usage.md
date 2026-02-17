# MCP Tool Usage Rules

## 1. Explicit WhatsApp Usage
Use the WhatsApp MCP server only when the user explicitly asks for it.
- EXAMPLES: "Check my WhatsApp messages", "Text [name] on WhatsApp", "Any new messages?"
- Do not connect to WhatsApp for general requests that don't require messenger data.

## 2. Write Operation Confirmation
Before performing any operations that change system state or send data externally, you MUST get explicit user confirmation.
- This applies to: sending WhatsApp messages, creating calendar events, deleting files, or modifying important settings.
- Read operations (list_messages, search_contacts, etc.) are allowed without additional questions if they fall within the scope of the current task.

## 3. Confirmation Process
If a task requires writing/sending data:
1. First prepare a draft or action plan.
2. Ask the user: "I'm ready to [action]. Do you confirm?"
3. Execute the action only after receiving an affirmative response.

## 4. Football API MCP
- **Purpose**: Provides access to football match data, teams, statistics, etc.
- **Example tools**:
    - `get_upcoming_matches(team_name)`: Get upcoming matches for a team.
    - `get_team_stats(team_name)`: Get team statistics.
    - `get_match_results(team1, team2)`: Get match results between two teams.
