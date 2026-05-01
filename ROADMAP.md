# 🗺️ vc-context-builder: Roadmap & Architecture

## 🎯 Vision
Transform from a basic regex-based scanner into a deep, AST-aware Context Extraction Engine for AI Agents.

## 🏗️ Architecture: Component-Based Parsers
We will migrate to the **Strategy Pattern**. Parsers will become pluggable components.

```text
/parsers
  ├── base_parser.py      # Abstract interface
  ├── python_parser.py    # Deep AST parser for Python
  ├── php_parser.py       # PHP parser (classes, hooks, traits)
  ├── ts_parser.py        # TypeScript/Angular parser
  └── devops_parser.py    # Docker, GitHub Actions, CI/CD