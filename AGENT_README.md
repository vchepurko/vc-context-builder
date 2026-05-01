# 🤖 AI Agent Standard Operating Procedure (SOP)

## System Architecture
This repository utilizes `vc-context-builder` for hierarchical Retrieval-Augmented Generation (RAG).

## Your Workflow:
1. **DISCOVER:** Read `agent_root.json` to understand the high-level module structure.
2. **LOCATE:** Read `_module_map.json` inside specific directories to find required classes, functions, and dependencies.
3. **MODIFY:** Edit the actual source code files (`.py`, `.php`, `.js`, etc.).
4. **REGENERATE (CRITICAL):** After modifying ANY code, you MUST run `python agent_map.py` to update the context graphs. Do NOT edit JSON maps manually.
