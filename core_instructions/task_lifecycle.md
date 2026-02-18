# Task Lifecycle & Architecture

The Assistant uses a "Plan-First" workflow with atomic, linked tasks.

## 1. The "One Task = One Request" Rule
- Every message from the user creates a **New Task File**.
- We never "append" to old task files.
- If the user replies to a previous message, the new task is **linked** to the old one via `parent_task_id`.

## 2. The Planning Loop
Every task follows this lifecycle:

1.  **Analysis**: Read User Request + Parent Context (if any).
2.  **Planning**: Generate a Checklist of steps (The `# Plan` section).
3.  **Dashboard**: Send a **Single Status Message** to the user with the checklist.
4.  **Execution** (Loop):
    - specific step: `[ ]` -> `[/]` (Running)
    - Execute tools.
    - Result: `[x]` (Done) or `[!]` (Failed).
    - **Update Dashboard**: Edit the Status Message to show progress.
5.  **Completion**:
    - Generates a Final Answer (using `<answer>` tag).
    - Archive the task.

## 3. Context & Follow-ups
- **Context**: When executing a child task, the system loads the history/result of the `parent_task_id` into the context window.
- **Reset**: If a user sends a message *without* replying, context is cleared (new chain starts).

## 4. Assistant Responsibilities
- **Do NOT** execute the whole request in one go.
- **MUST** break down complex requests into steps (Search, Read, Think, Write).
- **MUST** update the Plan status (via the system) before running tools for that step.
