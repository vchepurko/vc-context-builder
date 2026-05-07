# Playbooks — task-shaped MCP recipes

Each file in this folder is a **recipe** for one class of code question
— a pre-baked sequence of `vc-context` MCP calls that resolves the
question with minimal context cost and explicit evidence.

Playbooks are NOT a replacement for [CLAUDE.md](../../CLAUDE.md) (rules)
or [AGENTS.md](../../AGENTS.md) (cross-tool conventions).  They sit
between the two: when you have a concrete task type, open the matching
playbook, follow the MCP sequence, copy the output format.

## When to open a playbook

| Task | Playbook |
|------|----------|
| "Why does X fail?" / stack trace in hand | [bug_investigation.md](bug_investigation.md) |
| "What breaks if I change X?" / before refactor | [impact_analysis.md](impact_analysis.md) |
| Reviewing a PR or refactor branch | [refactoring_review.md](refactoring_review.md) |

If your task doesn't match any of these — fall back to the
**Speed-first decision protocol** in [CLAUDE.md](../../CLAUDE.md):
MCP if one call answers it, Bash for git/build/free-text, fold
follow-up reads via `include_body=true` or `read_slice`.

## What every playbook gives you

1. **When to use / when NOT to use** — narrow the trigger
2. **MCP sequence** — concrete tool calls with realistic args
3. **Context budget** — hard cap on source reads
4. **Evidence rules** — what counts as proof
5. **Output format** — copy-paste structure for the answer
6. **Failure mode** — what to say when MCP doesn't have enough

## Adding a new playbook

Only add one when:
- The question class repeats often enough to memorise the sequence
- The current MCP tools cover the question without a new tool
- The shape of "good answer" is concrete (output format ≠ "explain it")

A playbook that just paraphrases CLAUDE.md is **not a playbook** — drop
it. A playbook that names a non-existent tool is a feature request,
not a recipe.
