# Bug investigation playbook

## When to use

- "Why does X fail?" / "Why is Y returning the wrong value?"
- A stack trace or error log line is in hand and root cause is unknown.
- A regression appeared after a recent change and you need to find it.

## When NOT to use

- "How do I add feature Z?" — that's open-ended design, not bug-hunt.
- Performance issues — different signal set (profiling, not AST).
- Locale / config / data issues that don't touch code paths — use
  `find_in_templates` / `find_locale_key` directly.

## MCP sequence (typical)

The order matters: each step narrows the surface so you read less code.

1. **Anchor on a symbol from the log line.**
   `mcp__vc-context__logline_to_symbol(line="ERROR auth_router.handle_callback ...")`
   → returns the symbol name (or null if the line doesn't carry one).
   If null, fall through to step 2 with whatever name you can read off
   the traceback.

2. **Locate the symbol — file + line range, no body yet.**
   `find_symbol(name="handle_callback", fields=["file","line","end_line","kind"])`
   → ~40 tokens. You now know the def block range.

3. **Match the failure mode against `raises`.**
   `get_raised_exceptions(symbol="handle_callback")`
   - If the failure exception appears here → root cause is *inside this
     symbol*. Skip to step 5 (read the body).
   - If it doesn't appear → the failure propagates from a callee. Step 4.

4. **Walk one level into callees.**
   `get_callees(symbol="handle_callback")`
   → list of identifiers. Filter the suspicious ones (matches the error
   message, related to the failing operation), then `find_symbol` on
   each in **one batch**:
   `find_symbols(["validate_token", "fetch_user"], fields=["file","line"])`

5. **Read the def block — and only the def block.**
   `find_symbol(name="handle_callback", include_body=true)`
   OR `read_slice(file, start=line, end=end_line)` if the body is large.
   Cite line numbers in the answer.

6. **Confirm a behaviour claim before stating it.**
   Don't say "it returns 403 when token is missing" unless the body
   actually shows that branch. If the index says it raises X but the
   body shows X is wrapped — say "raises X (per index); wrapped at
   `file:line`".

## Context budget

- **Hard cap: 2 source reads** before posting a partial answer.
  Anything past 2 reads → state what you found, what's still unclear,
  and ask for confirmation before reading more.
- **Never read the whole file** containing the symbol. Use
  `find_symbol(include_body=true)` or `read_slice(line, end_line)`.
- Don't read the test file unless the bug is "test fails" specifically.

## Evidence rules

- Cite `file:line` for every claim about behaviour.
- "Raises X" → cite the `raise` line via `read_slice`. Index alone is
  weaker evidence; mark as "(index)" if you couldn't confirm.
- If you assert "the caller passes None", read the call site too.
- If you didn't read it: say **"assuming"** explicitly. Do not state
  unread behaviour as fact.

## Output format

```
Root cause:    <one sentence>
Evidence:      <file:line, file:line>
Fix surface:   <symbol(s) needing change>
Confidence:    high | medium | low
Open questions: <if any — what would change confidence>
```

## Failure mode

When `find_symbol` returns null and the log line has no anchor:
- Check `logline_to_symbol` output — sometimes it returns a partial
  hint like a router prefix.
- Try `find_call_sites` on the failing function name as a string.
- If still nothing: state "symbol not in index; user needs to point at
  the relevant file or update the index via
  `python3 .ai-context/agent_map.py`".
