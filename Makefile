# xtm developer tasks.
#
# Every target here delegates to scripts/run_tests.sh, which is the one place
# that knows how to run anything in this repository. Run it directly when a
# target does not pass the option you want:
#
#     scripts/run_tests.sh test_yaml        one module
#     scripts/run_tests.sh --shared         one interpreter, for a quick answer
#     scripts/run_tests.sh --help           every option
#
# Generated files all land under .build/, which `make check` asserts.

PYTHON ?= python3
RUN    := PYTHON=$(PYTHON) scripts/run_tests.sh

.PHONY: help test test-fast test-long test-shared coverage coverage-html lint \
        examples profile property mutation hooks check clean

help:
	@echo "make test           Every test module, each in its own interpreter"
	@echo "make test-fast      The commit gate: in-process work plus smoke, ~25s"
	@echo "make test-long      The release gate: integration, races, examples"
	@echo "make hooks          Install the pre-commit hook"
	@echo "make test-shared    The suite in one interpreter (faster, weaker)"
	@echo "make coverage       The suite under coverage, gated by the floor"
	@echo "make coverage-html  As above, then write .build/htmlcov/"
	@echo "make property       Property tests, when Hypothesis is available"
	@echo "make mutation       The mutation catalogue"
	@echo "make lint           pyflakes + compile check"
	@echo "make examples       Every example, the failures, and a profile"
	@echo "make profile        Where the time goes in real usage"
	@echo "make check          Everything, plus a clean-tree assertion"
	@echo "make clean          Remove .build/ and stray artefacts"

test:
	$(RUN)

test-fast:
	$(RUN) --fast

test-long:
	$(RUN) --long

hooks:
	$(RUN) --install-hooks

test-shared:
	$(RUN) --shared

coverage:
	$(RUN) --coverage

coverage-html: coverage
	$(PYTHON) -m coverage html
	@echo "Wrote .build/htmlcov/index.html"

property:
	$(RUN) --property

mutation:
	$(RUN) --mutation

lint:
	$(RUN) --lint

examples:
	$(RUN) --examples

profile:
	$(RUN) --profile

# Asserts a full run leaves no new files behind, which is what proves .build/
# actually absorbs every artefact the suite produces.
#
# Compares the tree before and after rather than requiring it to be clean
# outright: a developer running this with work in progress should not be told
# their own edits are build artefacts. Only a DIFFERENCE across the run is a
# missing .gitignore entry.
check:
	@mkdir -p .build
	@git status --porcelain > .build/tree-before.txt
	@$(RUN) --full
	@git status --porcelain > .build/tree-after.txt
	@if ! diff -u .build/tree-before.txt .build/tree-after.txt \
	        > .build/tree-diff.txt; then \
		echo "A full run left new files behind:"; \
		grep '^+[^+]' .build/tree-diff.txt; \
		echo "Add the offending paths to .gitignore, or write them under .build/."; \
		exit 1; \
	fi
	@echo "Working tree unchanged by a full run."

clean:
	rm -rf .build .coverage-bootstrap htmlcov .pytest_cache __pycache__
	rm -f .coverage .coverage.* coverage.xml coverage.json
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
