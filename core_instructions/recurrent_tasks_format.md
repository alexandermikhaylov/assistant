# Task Scheduler Format (Heartbeat)

When creating tasks in the `tasks/recurrent/` folder, you must use the following schedule types in the YAML header.

## Header Structure
```yaml
---
regular: true
schedule:
  times: ["HH:MM"]      # List of run times (required)
  date: "YYYY-MM-DD"    # Optional: for one-time tasks
  weekdays: ["Mon"]     # Optional: days of week (Mon, Tue, Wed, Thu, Fri, Sat, Sun)
description: "Short description"
---
```

## Schedule Types

### 1. Daily
When only `times` are specified.
```yaml
schedule:
  times: ["09:00", "21:00"]
```

### 2. One-time
When a specific `date` is provided. The task will be **deleted** after its first run.
```yaml
schedule:
  times: ["15:00"]
  date: "2026-02-14"
```

### 3. Specific Days (Weekly / Weekdays)
When a `weekdays` list is provided. Use abbreviations: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun`.
```yaml
schedule:
  times: ["10:00"]
  weekdays: ["Mon", "Wed", "Fri"]
```

## Creation Rules
1. Always save files in `users/user_<ID>/tasks/recurrent/`.
2. Use `regular: true`.
3. The content after the header should be a clear instruction for yourself (what to tell the user, what to check via MCP).
4. **Important**: Always ask for the user's confirmation before creating the file.
