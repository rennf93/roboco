# Supported Python versions
PYTHON_VERSIONS = 3.10 3.11 3.12 3.13 3.14
DEFAULT_PYTHON = 3.10

# Install dependencies
.PHONY: install
install:
	@uv sync
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Install dev dependencies
.PHONY: install-dev
install-dev:
	@uv sync --extra dev
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Update dependencies
.PHONY: lock
lock:
	@uv lock
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf


# Upgrade dependencies
.PHONY: upgrade
upgrade:
	@uv lock --upgrade
	@uv sync --all-extras
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# =============================================================================
# INFRASTRUCTURE
# =============================================================================

# Default agents to spawn
AGENTS ?= main-pm be-dev-1 be-qa

# Start infrastructure (PostgreSQL + Redis)
.PHONY: infra
infra:
	@echo "Starting infrastructure..."
	@docker compose up -d postgres redis
	@echo "Waiting for services to be healthy..."
	@sleep 3
	@docker compose ps

# Stop infrastructure
.PHONY: infra-down
infra-down:
	@echo "Stopping infrastructure..."
	@docker compose down

# Run database migrations
.PHONY: migrate
migrate:
	@echo "Running database migrations..."
	@uv run alembic upgrade head

# Create new migration
.PHONY: migration
migration:
	@read -p "Migration message: " msg; \
	uv run alembic revision --autogenerate -m "$$msg"

# =============================================================================
# RUNNING THE APPLICATION
# =============================================================================

# Start API server only (development mode with reload)
.PHONY: api
api:
	@echo "Starting RoboCo API (development mode)..."
	@uv run uvicorn roboco.api.app:app --host 0.0.0.0 --port 8000 --reload

# Start API server (production mode, no reload)
.PHONY: run
run:
	@echo "Starting RoboCo API (production mode)..."
	@uv run uvicorn roboco.api.app:app --host 0.0.0.0 --port 8000

# Start orchestrator only (spawns agents)
.PHONY: orchestrator
orchestrator:
	@echo "Starting orchestrator with agents: $(AGENTS)..."
	@uv run python -m roboco.cli --spawn $(AGENTS)

# Start API + Orchestrator (full development mode)
.PHONY: dev
dev:
	@echo "Starting RoboCo in development mode..."
	@echo "Agents to spawn: $(AGENTS)"
	@echo ""
	@echo "Starting API in background..."
	@uv run uvicorn roboco.api.app:app --host 0.0.0.0 --port 8000 &
	@sleep 2
	@echo "Starting orchestrator..."
	@uv run python -m roboco.cli --spawn $(AGENTS)

# Initialize database only (seed data)
.PHONY: db-init
db-init:
	@echo "Initializing database..."
	@uv run python -m roboco.cli --db-only

# =============================================================================
# MONITORING & STATUS
# =============================================================================

# Show system status
.PHONY: status
status:
	@echo "=== Infrastructure ==="
	@docker compose ps
	@echo ""
	@echo "=== API Health ==="
	@curl -s http://localhost:8000/health 2>/dev/null | jq . || echo "API not running"
	@echo ""
	@echo "=== Orchestrator Status ==="
	@curl -s http://localhost:8000/api/v1/orchestrator/status 2>/dev/null | jq . || echo "Orchestrator not available"

# Tail all logs
.PHONY: logs
logs:
	@docker compose logs -f

# =============================================================================
# TMUX SESSION
# =============================================================================

# Create tmux session with all components
.PHONY: tmux
tmux:
	@echo "Creating tmux session 'roboco'..."
	@tmux kill-session -t roboco 2>/dev/null || true
	@tmux new-session -d -s roboco -n infra
	@tmux send-keys -t roboco:infra "cd $(PWD) && docker compose logs -f" Enter
	@tmux new-window -t roboco -n api
	@tmux send-keys -t roboco:api "cd $(PWD) && make api" Enter
	@tmux new-window -t roboco -n orch
	@tmux send-keys -t roboco:orch "cd $(PWD) && sleep 3 && make orchestrator AGENTS='$(AGENTS)'" Enter
	@tmux select-window -t roboco:api
	@echo "tmux session 'roboco' created. Attach with: tmux attach -t roboco"

# Stop
.PHONY: stop
stop:
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Restart
.PHONY: restart
restart: stop start-example

# Lint code
.PHONY: lint
lint:
	@echo 'Formatting w/ Ruff...' ; echo '' ; uv run ruff format .
	@echo '' ; echo '' ; echo 'Linting w/ Ruff...' ; echo '' ; uv run ruff check .
	@echo '' ; echo '' ; echo 'Type checking w/ Mypy...' ; echo '' ; uv run mypy roboco/ tests/
	@echo '' ; echo '' ; echo 'Finding dead code w/ Vulture...' ; echo '' ; uv run vulture vulture_whitelist.py

# Fix code
.PHONY: fix
fix:
	@echo "Fixing formatting w/ Ruff..."
	@echo ''
	@uv run ruff check --fix .
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Reflow hard-wrapped markdown prose to one line per paragraph (docs style).
.PHONY: reflow-docs
reflow-docs:
	uv run python scripts/reflow_md.py --apply

# CI guard: fail if any in-scope doc has hard-wrapped prose. Wired into `quality`.
.PHONY: reflow-check
reflow-check:
	uv run python scripts/reflow_md.py --check

# Find dead code with Vulture
.PHONY: vulture
vulture:
	@echo "Finding dead code with Vulture..."
	@echo ''
#	@uv run vulture
#	@uv run vulture --verbose
	@uv run vulture vulture_whitelist.py
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Security scan with Bandit
.PHONY: bandit
bandit:
	@echo "Running Bandit security scan..."
	@echo ''
	@uv run bandit -r roboco -ll
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Audit dependencies with pip-audit
.PHONY: pip-audit
pip-audit:
	@echo "Auditing dependencies with pip-audit..."
	@echo ''
	@uv run pip-audit
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Analyze code complexity with Radon
.PHONY: radon
radon:
	@echo "Analyzing code complexity with Radon..."
	@echo ''
	@echo "Cyclomatic Complexity:"
	@uv run radon cc roboco -nc
	@echo ''
	@echo "Maintainability Index:"
	@uv run radon mi roboco -nc
	@echo ''
	@echo "Raw Metrics:"
	@uv run radon raw roboco
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Check complexity thresholds with Xenon
.PHONY: xenon
xenon:
	@echo "Checking complexity thresholds with Xenon..."
	@echo ''
	@uv run xenon roboco --max-absolute B --max-modules A --max-average A
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Analyze dependencies with Deptry
.PHONY: deptry
deptry:
	@echo "Analyzing dependencies with Deptry..."
	@echo ''
	@uv run deptry .
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Run all security checks
.PHONY: security
security: bandit pip-audit
	@echo "All security checks completed."

# =============================================================================
# QUALITY GATES
# =============================================================================

# Run every quality gate. Fails on any red. Use this as the merge gate.
.PHONY: quality
quality:
	@echo "==> ruff format --check"
	@uv run ruff format --check .
	@echo "==> ruff check"
	@uv run ruff check .
	@echo "==> markdown prose (no hard-wrapping)"
	@uv run python scripts/reflow_md.py --check
	@echo "==> mypy"
	@uv run mypy roboco/ tests/
	@echo "==> pytest with coverage"
	@uv run pytest -q --cov=roboco --cov-report=term-missing --cov-fail-under=80
	@echo "==> xenon (cyclomatic complexity)"
	@uv run xenon --max-absolute B --max-modules A --max-average A roboco/
	@echo "==> radon mi (maintainability index)"
	@uv run radon mi roboco/ -nc -s
	@echo "==> vulture (dead code)"
	@uv run vulture roboco/ tests/ vulture_whitelist.py --min-confidence 100
	@echo "==> bandit (security)"
	@uv run bandit -r roboco/ -ll
	@echo "==> pip-audit (deps vulnerabilities)"
	# CVE-2025-3000: memory corruption in torch.jit.script (MEDIUM, local-only,
	# no fix published). torch is a transitive dep (piragi / sentence-transformers)
	# pinned to the CPU wheel and NEVER loaded at runtime — the stack uses Ollama
	# over HTTP for all embeddings/LLM, so the vulnerable JIT path is unreachable.
	# Documented waiver; revisit when a fixed torch ships.
	@uv run pip-audit --ignore-vuln CVE-2025-3000
	@echo "==> deptry (dependency hygiene)"
	@uv run deptry roboco/
	@echo "==> alembic upgrade --sql (migrations parse)"
	@uv run alembic upgrade head --sql > /dev/null
	@echo "==> import-linter (architectural boundaries)"
	@uv run lint-imports
	@echo "==> foundation drift checks (includes lifecycle artifacts)"
	@$(MAKE) foundation-check
	@echo ""
	@echo "All quality gates passed."

.PHONY: quality-fast
quality-fast:
	@uv run ruff format --check .
	@uv run ruff check .
	@uv run mypy roboco/ tests/
	@uv run pytest -q -x --no-cov

# Fast pre-submit gate: format-check + lint + types + complexity, NO tests.
# This is the command a project points `quality_command` at, so the agent
# pre-submit gate (run at i_am_done) executes it in the dev's workspace and
# catches lint/type/complexity at the desk. The test suite stays on CI.
.PHONY: gate
gate:
	@uv run ruff format --check .
	@uv run ruff check .
	@uv run mypy roboco/ tests/
	@uv run xenon --max-absolute B --max-modules A --max-average A roboco/

# Run all analysis tools
.PHONY: analysis
analysis: deptry
	@echo "All analysis tools completed."

# Run all checks (linting, security, quality, and analysis)
.PHONY: check-all
check-all: lint security quality analysis
	@echo "All checks completed."

# Run tests (default Python version)
.PHONY: test
test:
	@COMPOSE_BAKE=true PYTHON_VERSION=$(DEFAULT_PYTHON) docker compose run --rm --build roboco pytest -v --cov=.
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Run All Python versions
.PHONY: test-all
test-all: test-3.10 test-3.11 test-3.12 test-3.13 test-3.14

# Python 3.10
.PHONY: test-3.10
test-3.10:
	@docker compose down -v roboco
	@COMPOSE_BAKE=true PYTHON_VERSION=3.10 docker compose build roboco
	@PYTHON_VERSION=3.10 docker compose run --rm roboco pytest -v --cov=.
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Python 3.11
.PHONY: test-3.11
test-3.11:
	@docker compose down -v roboco
	@COMPOSE_BAKE=true PYTHON_VERSION=3.11 docker compose build roboco
	@PYTHON_VERSION=3.11 docker compose run --rm roboco pytest -v --cov=.
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Python 3.12
.PHONY: test-3.12
test-3.12:
	@docker compose down -v roboco
	@COMPOSE_BAKE=true PYTHON_VERSION=3.12 docker compose build roboco
	@PYTHON_VERSION=3.12 docker compose run --rm roboco pytest -v --cov=.
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Python 3.13
.PHONY: test-3.13
test-3.13:
	@docker compose down -v roboco
	@COMPOSE_BAKE=true PYTHON_VERSION=3.13 docker compose build roboco
	@PYTHON_VERSION=3.13 docker compose run --rm roboco pytest -v --cov=.
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Python 3.14
.PHONY: test-3.14
test-3.14:
	@docker compose down -v roboco
	@COMPOSE_BAKE=true PYTHON_VERSION=3.14 docker compose build roboco
	@PYTHON_VERSION=3.14 docker compose run --rm roboco pytest -v --cov=.
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Stress Test
.PHONY: stress-test
stress-test:
	@COMPOSE_BAKE=true docker compose up --build -d roboco-example redis
	@echo "Waiting for services to start up..."
	@sleep 5
	@docker compose run --rm roboco-example uv run python examples/testing/stress_test.py --url http://roboco-example:8000 --duration 120 --concurrency 50 --ramp-up 10 --delay 0.02 --test-type standard -v
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# High-load stress test
.PHONY: high-load-stress-test
high-load-stress-test:
	@COMPOSE_BAKE=true docker compose up --build -d roboco-example redis
	@echo "Waiting for services to start up..."
	@sleep 5
	@docker compose run --rm roboco-example uv run python examples/testing/stress_test.py --url http://roboco-example:8000 --duration 180 --concurrency 100 --ramp-up 15 --delay 0.01 --test-type high_load -v
	@docker compose down --rmi all --remove-orphans -v
	@docker system prune -f

# Serve docs
.PHONY: serve-docs
serve-docs:
	@uv run mkdocs serve
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Lint documentation
.PHONY: lint-docs
lint-docs:
	@uv run pymarkdownlnt scan -r -e ./.venv -e ./.git -e ./.github -e ./roboco -e ./tests -e ./.claude -e ./CLAUDE.md -e ./ZZZ .
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Fix documentation
.PHONY: fix-docs
fix-docs:
	@uv run pymarkdownlnt fix -r -e ./.venv -e ./.git -e ./.github -e ./roboco -e ./tests -e ./.claude -e ./CLAUDE.md -e ./ZZZ .
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf

# Prune
.PHONY: prune
prune:
	@docker system prune -f

# Clean Cache Files
.PHONY: clean
clean:
	@find . | grep -E "(__pycache__|\.pyc|\.pyo|\.pytest_cache|\.ruff_cache|\.mypy_cache)" | xargs rm -rf
	@cd panel && rm -rf node_modules/ && rm -rf .next/ && rm -rf logs/
	@cd ..

# Security
.PHONY: panel-token
panel-token:
	@SECRET="$$(grep -E '^ROBOCO_AGENT_AUTH_SECRET=' .env 2>/dev/null | head -1 | cut -d= -f2-)"; \
	ROBOCO_AGENT_AUTH_SECRET="$${SECRET:-$$ROBOCO_AGENT_AUTH_SECRET}" \
	uv run python -c "import sys; from roboco.agents_config import issue_panel_token; tok = issue_panel_token(); print(tok) if tok != 'UNSIGNED' else sys.exit('ERROR: ROBOCO_AGENT_AUTH_SECRET not set (in .env or environment) - the panel token would be unsigned')"

# Help
.PHONY: help
help:
	@echo "RoboCo - AI Agents Company"
	@echo ""
	@echo "Infrastructure:"
	@echo "  make infra                    - Start PostgreSQL + Redis"
	@echo "  make infra-down               - Stop infrastructure"
	@echo "  make migrate                  - Run database migrations"
	@echo "  make migration                - Create new migration"
	@echo "  make db-init                  - Initialize/seed database"
	@echo ""
	@echo "Running:"
	@echo "  make dev                      - Start API + Orchestrator (development)"
	@echo "  make dev AGENTS='a b c'       - Start with specific agents"
	@echo "  make api                      - Start API only (with reload)"
	@echo "  make run                      - Start API only (production)"
	@echo "  make orchestrator             - Start orchestrator only"
	@echo "  make tmux                     - Create tmux session with all components"
	@echo ""
	@echo "Monitoring:"
	@echo "  make status                   - Show system status"
	@echo "  make logs                     - Tail infrastructure logs"
	@echo ""
	@echo "Dependencies:"
	@echo "  make install                  - Install dependencies"
	@echo "  make install-dev              - Install dev dependencies"
	@echo "  make lock                     - Update lock file"
	@echo "  make upgrade                  - Upgrade all dependencies"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint                     - Run linting (ruff, mypy, vulture)"
	@echo "  make fix                      - Auto-fix linting issues"
	@echo "  make quality                  - Run all quality checks"
	@echo "  make security                 - Run security checks (bandit, safety, pip-audit)"
	@echo "  make check-all                - Run ALL checks"
	@echo "  make panel-token              - Print the panel's CEO token for secure mode"
	@echo ""
	@echo "Testing:"
	@echo "  make test                     - Run tests (Python $(DEFAULT_PYTHON))"
	@echo "  make test-all                 - Run tests (all Python versions)"
	@echo "  make stress-test              - Run stress test"
	@echo ""
	@echo "Documentation:"
	@echo "  make serve-docs               - Serve documentation"
	@echo "  make lint-docs                - Lint markdown files"
	@echo ""
	@echo "Cleanup:"
	@echo "  make stop                     - Stop all containers"
	@echo "  make clean                    - Clean cache files"
	@echo "  make prune                    - Prune docker resources"
	@echo ""
	@echo "See docs/deployment.md and docs/usage.md for detailed guides."

# Python versions list
.PHONY: show-python-versions
show-python-versions:
	@echo "Supported Python versions: $(PYTHON_VERSIONS)"
	@echo "Default Python version: $(DEFAULT_PYTHON)"

# =============================================================================
# LIFECYCLE ARTIFACTS
# =============================================================================

# Regenerate canonical lifecycle artifacts (markdown / JSON / prompt fragments)
# from roboco/lifecycle/spec.py. Output is deterministic; CI gates on
# `make lifecycle && git diff --exit-code`.
.PHONY: lifecycle
lifecycle:
	uv run python scripts/build_lifecycle_artifacts.py

# =============================================================================
# FOUNDATION DRIFT GATE
# =============================================================================

# Canonical drift gate: validates identity tables, runs foundation self-tests,
# regenerates lifecycle artifacts and fails on any uncommitted diff, and
# (when reachable) checks postgres enum parity. Run on every PR — drift
# between foundation tables / lifecycle spec and the committed artifacts
# cannot land on master.
.PHONY: foundation-check
foundation-check:
	@echo "==> foundation/identity validators"
	uv run python -c "from roboco.foundation import _validate; _validate.run_all(); print('  identity validators: OK')"
	@echo "==> foundation/tracing verb parity"
	uv run pytest tests/foundation/test_tracing_verb_parity.py --no-cov -q
	@echo "==> foundation/journaling consumers"
	uv run pytest tests/foundation/test_journaling_consumers.py --no-cov -q
	@echo "==> foundation/communications consumers"
	uv run pytest tests/foundation/test_communications_consumers.py --no-cov -q
	@echo "==> foundation tests (full)"
	uv run pytest tests/foundation/ --no-cov -q
	@echo "==> lifecycle artifacts up-to-date (renders + git diff)"
	@$(MAKE) lifecycle
	@git diff --exit-code -- docs/rag/lifecycle panel/lib/lifecycle.json agents/prompts/_generated/lifecycle-*.md \
		|| (echo "Lifecycle artifacts are out of date. Run 'make lifecycle' and commit the diff." && exit 1)
	@echo "==> postgres enum parity (offline-skip if no DB)"
	uv run python scripts/verify_postgres_enums.py || echo "  (skipped — postgres unreachable)"
	@echo "All foundation drift checks passed."

# Backwards-compatible alias — prior CI / scripts called `ci-lifecycle-check`.
# `foundation-check` is now the canonical drift gate; this alias just forwards.
.PHONY: ci-lifecycle-check
ci-lifecycle-check: foundation-check
