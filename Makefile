# =============================================================================
# ML Python base template — commands to configure, develop, and govern
# AI-assisted projects across Claude Code, OpenCode, Codex, Antigravity,
# and GitHub Copilot.
#
# Main features:
# - Virtual environment management with uv
# - Linting, formatting, typing, and security gates (ruff/mypy/bandit/pytest)
# - Governed skills/agents sync engine (single source: .github/)
# - Template lifecycle: bootstrap new projects + sync from upstream template
# - Docker containerization support
#
# Basic usage:
#   make init NAME=my_project  # Bootstrap a fresh clone as a new project
#   make install               # Set up development environment
#   make format                # Automatically format code
#   make check                 # Read-only quality gate (CI-safe)
#   make sync-skills           # Refresh native skill/agent views + adapters
# =============================================================================

export PYTHON_VERSION=3.11.9
export ENVIRONMENT=localhost
VENV_DIR ?= .venv
KERNEL_NAME=ai-kernel
TEMPLATE_REMOTE ?= template
TEMPLATE_REPO ?= git@github.com:marcosdh1987/ml-python-base.git
TEMPLATE_BRANCH ?= main

# Declarative skills/adapters sync engine (replaces inline bash projection).
SKILLS_SYNC = uv run python -m ml_python_base.skills_sync

# =============================================================================
# DEVELOPMENT ENVIRONMENT CONFIGURATION
# =============================================================================

# Set up virtual environment and install all dependencies using uv.lock
install:
	@set -e; \
	echo "🚀 Configuring project with uv..."; \
	UV_BIN="$$(command -v uv || true)"; \
	if [ -z "$$UV_BIN" ]; then \
		echo "❌ uv is not installed. Installing..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		for candidate in "$$HOME/.local/bin/uv" "$$HOME/.cargo/bin/uv"; do \
			if [ -x "$$candidate" ]; then UV_BIN="$$candidate"; break; fi; \
		done; \
	fi; \
	if [ -z "$$UV_BIN" ]; then \
		echo "❌ Could not find uv after installation. Add it to PATH and retry."; \
		exit 1; \
	fi; \
	echo "✅ Using uv at $$UV_BIN"; \
	echo "📌 Pinning Python version $(PYTHON_VERSION)..."; \
	"$$UV_BIN" python pin $(PYTHON_VERSION); \
	echo "📦 Syncing dependencies (creating .venv if it doesn't exist)..."; \
	"$$UV_BIN" sync; \
	echo "🔌 Registering Jupyter kernel..."; \
	"$$UV_BIN" run python -m ipykernel install --user --name=$(KERNEL_NAME) --display-name="Python (uv)"; \
	echo "✅ Environment ready! Use 'source .venv/bin/activate' to activate."

# Add a new library to the project (replaces editing requirements.in)
# Usage: make add PKG=tensorflow
add:
	@echo "📦 Adding package $(PKG)..."
	@uv add $(PKG)
	@echo "✅ Package added and lockfile updated."

# Remove a library from the project
# Usage: make remove PKG=tensorflow
remove:
	@echo "🗑️ Removing package $(PKG)..."
	@uv remove $(PKG)
	@echo "✅ Package removed and lockfile updated."

# Generate requirements.txt (for legacy compatibility or simple deployments)
generate-requirements:
	@echo "📋 Exporting requirements.txt from uv.lock..."
	@uv export --format requirements-txt > requirements.txt
	@echo "✅ requirements.txt generated"

# Set up pre-commit hooks (Recommended to run once)
setup-hooks:
	@echo "🪝 Installing pre-commit hooks..."
	@if [ ! -d .venv ]; then make install; fi
	@echo "📦 Syncing dev dependencies required for hooks..."
	@uv sync --group dev
	@. $(VENV_DIR)/bin/activate && pre-commit install
	@echo "✅ Hooks installed!"

# =============================================================================
# CODE QUALITY AND LINTING
# =============================================================================

# Automatically format code with ruff
format:
	@echo "🎨 Formatting code with ruff..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && ruff format src/ tests/ scripts/
	@. $(VENV_DIR)/bin/activate && ruff check --select I --fix src/ tests/ scripts/
	@echo "🧹 Cleaning notebook outputs..."
	@. $(VENV_DIR)/bin/activate && nbstripout notebooks/*.ipynb 2>/dev/null || echo "⚠️  No notebooks found or nbstripout not installed"
	@echo "✅ Code formatted and notebooks cleaned!"

# Check code quality with multiple tools
lint:
	@echo "🔍 Running code quality analysis..."
	@if [ ! -d .venv ]; then make install; fi
	@echo "🚀 Ruff (fast linter)..."
	@. $(VENV_DIR)/bin/activate && ruff check src/ tests/ scripts/
	@echo " Bandit (security)..."
	@. $(VENV_DIR)/bin/activate && bandit -r src/ tests/ scripts/ -f json -o security-report.json -ll -q || true
	@. $(VENV_DIR)/bin/activate && bandit -r src/ tests/ scripts/ -ll || true
	@echo "✅ Quality analysis completed!"

# Check only with ruff (faster for development)
lint-fast:
	@echo "⚡ Fast analysis with ruff..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && ruff check src/ tests/ scripts/
	@echo "✅ Fast analysis completed!"

# Automatically fix linting issues when possible
fix:
	@echo "🔧 Fixing issues automatically..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && ruff check --fix src/ tests/ scripts/ || echo "⚠️  Some issues remain for manual review."
	@. $(VENV_DIR)/bin/activate && ruff format src/ tests/ scripts/
	@echo "🧹 Cleaning notebook outputs..."
	@. $(VENV_DIR)/bin/activate && nbstripout notebooks/*.ipynb 2>/dev/null || echo "⚠️  No notebooks found or nbstripout not installed"
	@echo "✅ Code formatted and cleanups applied!"

# Fix issues aggressively (includes unsafe fixes)
fix-force:
	@echo "🚨 Applying aggressive fixes (unsafe)..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && ruff check --fix --unsafe-fixes src/ tests/ scripts/ || echo "⚠️  Issues remain."
	@. $(VENV_DIR)/bin/activate && ruff format src/ tests/ scripts/
	@echo "✅ Aggressive fixes applied!"

# =============================================================================
# SYSTEM TESTING
# =============================================================================

# Run all tests with coverage
test:
	@echo "🧪 Running tests with coverage..."
	@if [ ! -d .venv ]; then make install; fi
	@echo "🎨 Running format before tests..."
	@make format
	@echo "🔧 Running fix before tests..."
	@make fix
	@. $(VENV_DIR)/bin/activate && PYTHONPATH=${PWD}/src pytest tests/ --cov=src --cov-report=html --cov-report=term-missing || echo "⚠️  No tests found to run"
	@echo "✅ Tests completed! See report in htmlcov/index.html"

# Run specific tests
test-unit:
	@echo "🧪 Running unit tests..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && PYTHONPATH=${PWD}/src pytest tests/ -v

# =============================================================================
# APPLICATION EXECUTION
# =============================================================================

# Start LangGraph development server
run-dev:
	@echo "🚀 Starting development server..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && langgraph dev

# Start FastAPI server
run-api:
	@echo "🚀 Starting API server..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && PYTHONPATH=${PWD} uvicorn api:app --reload --host 0.0.0.0 --port 8008 --log-level debug

# Run CLI with a predefined question
run-question:
	@echo "🚀 Running a single question"
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && PYTHONPATH=${PWD}/src python main.py --question "What can you help me with?"

# Start interactive CLI mode
run-interactive:
	@echo "🚀 Starting interactive CLI mode"
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && PYTHONPATH=${PWD}/src python main.py --interactive

# =============================================================================
# OPENCODE RUNTIME (self-hosted / cloud, configured via .env)
# =============================================================================

# Launch the opencode TUI with .env loaded so opencode.json {env:...} resolves.
# Installs opencode-conductor-plugin into the global opencode config dir on first run.
opencode:
	@command -v opencode >/dev/null 2>&1 || { echo "❌ opencode not found. Install: brew install anomalyco/tap/opencode"; exit 1; }
	@[ -f .env ] || echo "⚠️  no .env found — copy .env.example to .env and edit (or use 'opencode auth login' / the /models picker)."
	@if [ ! -d "$$HOME/.config/opencode/node_modules/opencode-conductor-plugin" ]; then \
	  echo "🔌 Installing opencode-conductor-plugin..."; \
	  npm install --prefix "$$HOME/.config/opencode" opencode-conductor-plugin; \
	fi
	@set -a; [ -f .env ] && . ./.env; set +a; opencode

# Verify opencode is installed and the configured local endpoints are reachable.
opencode-doctor:
	@set -a; [ -f .env ] && . ./.env; set +a; \
	if command -v opencode >/dev/null 2>&1; then echo "✅ opencode: $$(opencode --version 2>/dev/null || echo installed)"; \
	else echo "❌ opencode not installed (brew install anomalyco/tap/opencode)"; fi; \
	for pair in "Ollama=$$OLLAMA_BASE_URL" "LM_Studio=$$LMSTUDIO_BASE_URL"; do \
	  name=$${pair%%=*}; url=$${pair#*=}; \
	  if [ -z "$$url" ]; then echo "–  $$name: not configured in .env"; continue; fi; \
	  if curl -fsS --max-time 4 "$$url/models" >/dev/null 2>&1; then echo "✅ $$name reachable: $$url"; \
	  else echo "❌ $$name unreachable: $$url (is the host up and the server listening?)"; fi; \
	done; \
	if [ -n "$$GATEWAY_BASE_URL" ]; then \
	  if curl -fsS --max-time 4 "$$GATEWAY_BASE_URL/models" -H "Authorization: Bearer $$GATEWAY_TOKEN" >/dev/null 2>&1; then echo "✅ AI Gateway reachable: $$GATEWAY_BASE_URL"; \
	  else echo "❌ AI Gateway unreachable: $$GATEWAY_BASE_URL (is 'docker compose up' running on :4000 and GATEWAY_TOKEN correct?)"; fi; \
	else echo "–  AI Gateway: GATEWAY_BASE_URL not set in .env (using direct ollama/lmstudio providers)"; fi

# =============================================================================
# DOCKER BUILD AND DEPLOYMENT
# =============================================================================

# Docker configuration variables
IMG_NAME ?= ml-python-base
IMAGE_TAG ?= latest
CONTAINER_NAME ?= ml-python-base-server
API_PORT ?= 8008

# Enable Docker BuildKit
export DOCKER_BUILDKIT=1

# Build API Docker image
build-api:
	@echo "🔨 Building FastAPI Docker image (using Dockerfile.api)..."
	@docker build --platform=linux/amd64 -t ${IMG_NAME}:${IMAGE_TAG} -f Dockerfile.api .
	@echo "✅ FastAPI Docker image built successfully!"

# Run Docker container
run-api-docker:
	@echo "🚀 Running Docker container..."
	@docker run --platform=linux/amd64 -e ENV=production -d -p ${API_PORT}:${API_PORT} --env-file .env ${IMG_NAME}:${IMAGE_TAG}
	@echo "✅ Docker container running at http://localhost:${API_PORT}!"

# Build without cache
build-fresh:
	@echo "🔨 Building Docker image without cache..."
	@docker build --no-cache --platform=linux/amd64 -t ${IMG_NAME}:${IMAGE_TAG} -f Dockerfile.api .
	@echo "✅ Docker image built successfully!"

# Stop Docker container
stop-docker:
	@echo "🛑 Stopping Docker container..."
	@docker stop ${CONTAINER_NAME} 2>/dev/null || true
	@docker rm ${CONTAINER_NAME} 2>/dev/null || true
	@echo "✅ Container stopped!"

# =============================================================================
# USEFUL COMMANDS
# =============================================================================

# Read-only quality gate (CI-safe: never mutates the working tree).
# Mirrors `make fix`/`make format` checks without applying changes.
check:
	@echo "🔎 Running read-only quality gate..."
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && ruff format --check src/ tests/ scripts/
	@. $(VENV_DIR)/bin/activate && ruff check src/ tests/ scripts/
	@. $(VENV_DIR)/bin/activate && bandit -r src/ tests/ scripts/ -ll -q
	@. $(VENV_DIR)/bin/activate && mypy src/ scripts/
	@. $(VENV_DIR)/bin/activate && PYTHONPATH=${PWD}/src pytest tests/ --cov=src --cov-report=term-missing
	@echo "✅ Quality gate passed (read-only)."

# Static type checking only.
typecheck:
	@if [ ! -d .venv ]; then make install; fi
	@. $(VENV_DIR)/bin/activate && mypy src/ scripts/

# Full CI pipeline: read-only quality gate + skill-sync drift gate.
# CI must verify, not mutate — use `make fix`/`make format` locally instead.
ci:
	@echo "🚀 Running full CI pipeline (read-only)..."
	@make check
	@make check-sync
	@echo "✅ CI pipeline completed successfully!"

# =============================================================================
# TEMPLATE SYNC WORKFLOW
# =============================================================================

# Bootstrap a fresh clone of this template as a new project.
# Renames the package, rewrites references (keeping the upstream template URL),
# creates .env, then chains environment setup and the read-only gates.
# Usage:
#   make init NAME=my_project
# Preview without writing:
#   python3 scripts/init_project.py --name my_project --dry-run
init:
	@[ -n "$(NAME)" ] || { echo "❌ Usage: make init NAME=my_project"; exit 1; }
	@python3 scripts/init_project.py --name "$(NAME)"
	@$(MAKE) install
	@$(MAKE) sync-skills
	@$(MAKE) template-remote-setup
	@$(MAKE) ci
	@echo "✅ Project '$(NAME)' initialized. Review the diff and commit."

# Add (or update) template remote used for repository synchronization.
# Usage:
#   make template-remote-setup
#   make template-remote-setup TEMPLATE_REPO=git@github.com:org/your-template.git
template-remote-setup:
	@set -e; \
	echo "🔗 Configuring template remote '$${TEMPLATE_REMOTE:-$(TEMPLATE_REMOTE)}'..."; \
	if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		echo "❌ This directory is not a git repository."; \
		exit 1; \
	fi; \
	if git remote get-url $(TEMPLATE_REMOTE) >/dev/null 2>&1; then \
		echo "ℹ️  Remote '$(TEMPLATE_REMOTE)' already exists. Updating URL..."; \
		git remote set-url $(TEMPLATE_REMOTE) $(TEMPLATE_REPO); \
	else \
		echo "➕ Adding remote '$(TEMPLATE_REMOTE)'..."; \
		git remote add $(TEMPLATE_REMOTE) $(TEMPLATE_REPO); \
	fi; \
	echo "✅ Template remote configured:"; \
	git remote -v | grep '^$(TEMPLATE_REMOTE)\s' || true

# Fetch template updates and show commit delta before merging/rebasing.
# Usage:
#   make template-sync-preview
template-sync-preview:
	@set -e; \
	echo "📥 Fetching updates from $(TEMPLATE_REMOTE)..."; \
	git fetch $(TEMPLATE_REMOTE); \
	echo "📊 Incoming commits from $(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH):"; \
	if git rev-list --count HEAD..$(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH) >/dev/null 2>&1; then \
		count="$$(git rev-list --count HEAD..$(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH))"; \
		echo "   $$count commit(s) ahead in template branch."; \
		git --no-pager log --oneline --decorate --graph HEAD..$(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH) | head -n 30 || true; \
	else \
		echo "⚠️  Could not compare branches. Verify remote/branch names."; \
	fi

# Merge template changes into current branch.
# Usage:
#   make template-sync-merge
#   make template-sync-merge TEMPLATE_BRANCH=develop
template-sync-merge:
	@set -e; \
	echo "🧭 Checking working tree status..."; \
	if [ -n "$$(git status --porcelain)" ]; then \
		echo "❌ Working tree is not clean. Commit or stash before syncing."; \
		exit 1; \
	fi; \
	git fetch $(TEMPLATE_REMOTE); \
	echo "🔀 Merging $(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH) into $$(git branch --show-current)..."; \
	git merge --no-ff $(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH) || { \
		echo "⚠️  Merge conflicts detected. Resolve them and continue with 'git commit'."; \
		echo "   Tip: git status && git diff --name-only --diff-filter=U"; \
		exit 1; \
	}; \
	echo "✅ Template merge completed."

# Rebase current branch on top of template changes.
# Usage:
#   make template-sync-rebase
template-sync-rebase:
	@set -e; \
	echo "🧭 Checking working tree status..."; \
	if [ -n "$$(git status --porcelain)" ]; then \
		echo "❌ Working tree is not clean. Commit or stash before syncing."; \
		exit 1; \
	fi; \
	git fetch $(TEMPLATE_REMOTE); \
	echo "🧬 Rebasing $$(git branch --show-current) onto $(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH)..."; \
	git rebase $(TEMPLATE_REMOTE)/$(TEMPLATE_BRANCH) || { \
		echo "⚠️  Rebase conflicts detected. Resolve and continue with 'git rebase --continue'."; \
		echo "   If needed: git rebase --abort"; \
		exit 1; \
	}; \
	echo "✅ Template rebase completed."

# =============================================================================
# AI TOOL SKILLS SYNC
# Declarative engine: src/ml_python_base/skills_sync, driven by
# adapters/registry.toml. Add a new AI tool by adding ONE [[tool]] entry to
# the registry (plus a template under adapters/templates/) -- no Makefile edit.
# =============================================================================

# Materialize Claude Code native skills (.claude/skills symlinks).
setup-claude-skills:
	@$(SKILLS_SYNC) link --tool claude

# Materialize OpenCode native skills (.opencode/skills symlinks).
setup-opencode-skills:
	@$(SKILLS_SYNC) link --tool opencode

# Materialize Antigravity native skills (.agents/skills copies + manifest).
setup-antigravity-skills:
	@$(SKILLS_SYNC) link --tool antigravity

# Project governed agents (.github/agents) into each tool's native agent format.
sync-agents:
	@$(SKILLS_SYNC) agents

# Regenerate the managed skill region inside every adapter file.
render-adapters:
	@$(SKILLS_SYNC) render

# Ingest ad-hoc external skills, refresh skills-lock.json, and rebuild every
# native adapter view (Claude / OpenCode / Antigravity) + adapter skill regions.
sync-skills:
	@$(SKILLS_SYNC) sync

# Fail if any generated skill artifact is stale (CI drift gate).
check-sync:
	@$(SKILLS_SYNC) check

# Remove all external skills and reset native views to internal-only.
purge-external-skills:
	@$(SKILLS_SYNC) purge

# Show help information about available commands
help:
	@echo "🧰 ML Python Base Template - Available Commands:"
	@echo ""
	@echo "Development Setup:"
	@echo "  make init NAME=my_project Bootstrap a fresh clone as a new project (rename + env + gates)"
	@echo "  make install              Set up virtual environment and dependencies"
	@echo "  make setup-hooks          Set up pre-commit hooks"
	@echo "  make generate-requirements Generate requirements.txt from current environment"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format               Automatically format code (ruff format + imports)"
	@echo "  make lint                 Full quality analysis (ruff + bandit)"
	@echo "  make lint-fast            Fast analysis with ruff only"
	@echo "  make fix                  Automatically fix issues (ruff check + format)"
	@echo "  make check                Read-only quality gate (CI-safe: format+lint+bandit+mypy+tests)"
	@echo "  make typecheck            Static type checking with mypy"
	@echo "  make ci                   Full read-only pipeline: check + check-sync"
	@echo ""
	@echo "Testing:"
	@echo "  make test                 Run all tests with coverage"
	@echo "  make test-unit            Run unit tests only"
	@echo "  make run-batch-test       Run batch tests against API (dataset v1)"
	@echo "  make run-batch-test-custom Run batch tests with custom parameters"
	@echo ""
	@echo "Application Execution (Local):"
	@echo "  make run-dev             Start LangGraph development server"
	@echo "  make run-api             Start FastAPI server"
	@echo "  make run-question        Test with a predefined question"
	@echo "  make run-interactive     Start interactive CLI mode"
	@echo ""
	@echo "OpenCode (self-hosted / cloud, via .env):"
	@echo "  make opencode            Launch opencode TUI with .env loaded"
	@echo "  make opencode-doctor     Check opencode install + local endpoints"
	@echo ""
	@echo "Docker:"
	@echo "  make build-api           Build API Docker image"
	@echo "  make build-fresh         Build without cache"
	@echo "  make run-api-docker      Run API in Docker container"
	@echo "  make stop-docker         Stop Docker container"
	@echo ""
	@echo "Service URLs:"
	@echo "  🚀 FastAPI: http://localhost:8008"
	@echo "  📖 API Documentation: http://localhost:8008/docs"
	@echo "  🔍 Agent Discovery: http://localhost:8008/.well-known/agent.json"
	@echo ""
	@echo "Utilities:"
	@echo "  make help                Show this help message"
	@echo "  make template-remote-setup Add/update template upstream remote"
	@echo "  make template-sync-preview Fetch template and preview incoming commits"
	@echo "  make template-sync-merge Merge template branch into current branch"
	@echo "  make template-sync-rebase Rebase current branch onto template branch"
	@echo "  make setup-claude-skills Generate .claude/skills native symlinks from governed skills"
	@echo "  make setup-antigravity-skills Generate .agents/skills native mirror from governed skills"
	@echo "  make setup-opencode-skills Generate .opencode/skills native symlinks from governed skills"
	@echo "  make render-adapters     Regenerate the managed skill region in every adapter file"
	@echo "  make sync-skills         Sync external skills to .github/skills-external and refresh native adapters"
	@echo "  make check-sync          Fail if generated skill artifacts are stale (CI drift gate)"
	@echo "  make purge-external-skills Purge all external skills and reset metadata"
	@echo "  make clean               Clean cache and generated files"
	@echo ""

# Clean generated files and cache
clean:
	@echo "🧹 Cleaning..."
	@rm -rf __pycache__ .pytest_cache htmlcov .coverage .mypy_cache .ruff_cache
	@rm -f security-report.json
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Cleanup completed!"

# Set help as default goal
.DEFAULT_GOAL := help

# Declare phony targets
.PHONY: init install setup-hooks run-dev run-api run-question run-interactive opencode opencode-doctor build-api run-api-docker stop-docker build-fresh clean help generate-requirements run-batch-test run-batch-test-custom test test-unit format lint lint-fast fix fix-force check typecheck ci template-remote-setup template-sync-preview template-sync-merge template-sync-rebase setup-claude-skills setup-antigravity-skills setup-opencode-skills sync-agents render-adapters sync-skills check-sync purge-external-skills
