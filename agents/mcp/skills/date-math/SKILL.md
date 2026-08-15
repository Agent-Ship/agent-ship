---
name: date-math
description: Compute the number of days between two dates. Use whenever the user asks how many days are between two dates, or about a duration in days.
metadata:
  version: "1.0"
---

# Date math

This agent has a `days_between` tool from the local `time` MCP server. Use it — do not count days
yourself.

When the user asks how many days are between two dates:

1. Parse both dates to ISO format (`YYYY-MM-DD`).
2. Call the `days_between` tool with `start` (the earlier date) and `end` (the later date).
3. Report the result in one short sentence, e.g. "There are 225 days between 2026-01-01 and 2026-08-14."

If a date is ambiguous or missing, ask the user to clarify before calling the tool.
