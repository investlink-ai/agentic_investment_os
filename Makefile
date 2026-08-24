.PHONY: agent-workflow architecture bootstrap check format harness lint mutation sync test typecheck

bootstrap: sync
	git config core.hooksPath .githooks

sync:
	uv sync --all-groups

format:
	uv run ruff format .
	uv run ruff check --fix .

harness:
	test -L CLAUDE.md
	test "$$(readlink CLAUDE.md)" = "AGENTS.md"
	test -L .claude/skills
	test "$$(readlink .claude/skills)" = "../.agents/skills"
	test -f .agents/skills/create-issue/SKILL.md
	test -f .agents/skills/find-simplifications/SKILL.md
	test -f .agents/skills/code-review/SKILL.md
	test -f .agents/skills/create-pull-request/SKILL.md
	test -f .agents/skills/deliver-issue/SKILL.md
	test -f .agents/skills/start-issue-worktree/SKILL.md
	test -f .agents/skills/plan-stage-issues/SKILL.md
	test -f .agents/skills/manage-agent-notes/SKILL.md
	test -f .agents/skills/prose-standard/SKILL.md
	test -f .agents/skills/reflect-on-merged-pr/SKILL.md
	test -f .agents/notes/AGENTS.md
	test -f .agents/notes/README.md
	test ! -d .agents/notes/archived
	test -f .github/AGENTS.md
	test -f .github/dependabot.yml
	test -f .github/pull_request_template.md
	test -f .github/workflows/ci.yml
	test -f .github/workflows/mutation.yml
	test -f scripts/__init__.py
	test -f scripts/agent_workflow_harness.py
	test -f scripts/check_capability_dependencies.py
	test -f scripts/check_coverage_tiers.py
	test -f scripts/check_skill_catalog.py
	test -f scripts/check_unit_test_tier.py
	test -f scripts/run_mutation.py
	test -x scripts/start-issue.sh
	grep -qx '/.agents/worktrees/' .gitignore
	grep -qx '/.agents/harness/results/' .gitignore
	test -f CONTEXT.md
	test -f docs/architecture.md
	test -f docs/config-catalog.md
	test -f docs/defensive-patterns.md
	test -f docs/development.md
	test -f docs/investment-domain.md
	test -f docs/product-requirements.md
	test -f docs/testing.md
	test -f docs/module-graph.md
	test ! -e docs/SPEC.md
	test ! -d docs/archive
	test ! -e pytest.ini
	uv run python -m scripts.check_unit_test_tier --root .
	uv run python -m scripts.check_skill_catalog --root .
	uv run python -m scripts.agent_workflow_harness --root . validate
	uv run pytest -o 'addopts=--strict-config --strict-markers -ra' tests/integration/test_skill_catalog.py
	uv run pytest -o 'addopts=--strict-config --strict-markers -ra' tests/unit/test_agent_workflow_harness.py

agent-workflow:
	@test -n "$(SCENARIO)" || { \
		echo 'usage: make agent-workflow SCENARIO=<scenario-id>' >&2; \
		exit 2; \
	}
	uv run python -m scripts.agent_workflow_harness --root . run "$(SCENARIO)"

architecture:
	uv run python -m scripts.check_capability_dependencies --root .
	uv run pytest -o 'addopts=--strict-config --strict-markers -ra' \
		tests/integration/test_capability_dependencies.py tests/integration/test_module_graph.py

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy src tests scripts

test:
	uv run pytest
	uv run python -m scripts.check_coverage_tiers --root .

mutation:
	uv run python scripts/run_mutation.py

check: harness architecture lint typecheck test
