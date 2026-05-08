# vc-context-builder — common dev targets.
# Stdlib-only project, but the dev-time tooling (ruff, mypy, pre-commit)
# is fetched on demand by `uv run --with` so contributors don't need
# global installs.

.PHONY: help test lint format format-check typecheck snapshots ci \
        index demo install-hooks clean

help:  ## Show this help (default target).
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?##"}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

test:  ## Run the unit + integration test suite.
	python3 -m unittest discover tests

lint:  ## Ruff lint with auto-fix for safe rules.
	uv run --with ruff ruff check . --fix

format:  ## Apply ruff format to the tree.
	uv run --with ruff ruff format .

format-check:  ## Format check (no writes) — same as CI.
	uv run --with ruff ruff format --check .

typecheck:  ## Run mypy with the project's defaults.
	uv run --with mypy mypy . --ignore-missing-imports

snapshots:  ## Verify the MCP tools/list snapshot is up to date.
	python3 tests/regen_snapshots.py --check

ci:  ## Run the full CI gate locally (test + lint + format-check + typecheck + snapshots).
	@$(MAKE) test
	@uv run --with ruff ruff check .
	@uv run --with ruff ruff format --check .
	@$(MAKE) typecheck
	@$(MAKE) snapshots
	@echo "✓ all gates green"

index:  ## (Re)build the agent_*.json artefacts for the current project.
	python3 agent_map.py

demo:  ## End-to-end demo — build + a couple of representative MCP queries.
	@python3 agent_map.py >/dev/null
	@echo "=== card QueryEngine ==="
	@python3 cli.py --root . card QueryEngine | head -10
	@echo
	@echo "=== repo-map ==="
	@python3 cli.py --root . repo-map | head -8

install-hooks:  ## Install the pre-commit hooks (one-time).
	uv tool install pre-commit 2>/dev/null || pip install pre-commit
	pre-commit install

clean:  ## Remove caches + auto-generated artefacts.
	rm -rf __pycache__ */__pycache__ .vc-context/_parse_cache.json
	find . -name "_module_map.json" -not -path "./.git/*" -delete
	rm -f agent_*.json AGENT_README.md
