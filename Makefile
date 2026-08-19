# xtm developer tasks.
#
# The important target is `coverage`. Measuring xtm correctly is not just
# `coverage run`: the integration tests execute xtm.py as a subprocess, so
# the child processes have to start coverage themselves. That needs a .pth
# file on PYTHONPATH plus COVERAGE_PROCESS_START, which is what this target
# sets up. Skipping it reports ~63% instead of ~94% -- an artefact of the
# measurement, not a regression in the tests.

PYTHON ?= python3
COVDIR := .coverage-bootstrap

.PHONY: help test test-isolated coverage coverage-html lint examples clean check

help:
	@echo "make test           Run the full suite"
	@echo "make test-isolated  Run each test module on its own"
	@echo "make coverage       Run the suite under coverage (with subprocess tracking)"
	@echo "make coverage-html  As above, then write htmlcov/"
	@echo "make lint           pyflakes + compile check"
	@echo "make examples       Run every example against its recorded output"
	@echo "make check          lint, coverage, and a clean-tree assertion"
	@echo "make clean          Remove build and test artefacts"

test:
	$(PYTHON) -m unittest discover -t . -s tests -v

# Runs every test module in a fresh interpreter, so a test that only passes
# because an earlier module left global state behind fails here. xtm keeps a
# few module-level globals (DRY_RUN, the resolved config paths, the logger's
# level), which makes that failure mode real: one such dependency was found
# exactly this way.
test-isolated:
	@set -e; for m in $$(find tests -name 'test_*.py' | sed 's|/|.|g; s|\.py$$||' | sort); do \
		echo "--- $$m"; $(PYTHON) -m unittest "$$m" || exit 1; \
	done
	@echo "Every test module passes on its own."

# The .pth file is executed at interpreter startup in EVERY child process,
# which is the only hook that runs early enough to measure a subprocess from
# its first line.
$(COVDIR)/coverage-subprocess.pth:
	@mkdir -p $(COVDIR)
	@printf 'import coverage; coverage.process_startup()\n' > $@

coverage: $(COVDIR)/coverage-subprocess.pth
	@rm -f .coverage .coverage.*
	COVERAGE_PROCESS_START=$(CURDIR)/.coveragerc \
	PYTHONPATH=$(CURDIR)/$(COVDIR) \
		$(PYTHON) -m coverage run -m unittest discover -t . -s tests
	$(PYTHON) -m coverage combine
	$(PYTHON) -m coverage report

coverage-html: coverage
	$(PYTHON) -m coverage html
	@echo "Wrote htmlcov/index.html"

examples:
	$(PYTHON) examples/run.py

lint:
	$(PYTHON) -m pyflakes xtm.py $(shell find tests -name '*.py')
	$(PYTHON) -m py_compile xtm.py $(shell find tests -name '*.py')

# Asserts a full run leaves no new files behind, which is what proves
# .gitignore actually covers every artefact the suite produces.
#
# Compares the tree before and after rather than requiring it to be clean
# outright: a developer running this with work in progress should not be
# told their own edits are build artefacts. Only a DIFFERENCE across the run
# is a missing .gitignore entry.
check: lint examples test-isolated
	@mkdir -p $(COVDIR)
	@git status --porcelain > $(COVDIR)/tree-before.txt
	@$(MAKE) --no-print-directory coverage
	@git status --porcelain > $(COVDIR)/tree-after.txt
	@if ! diff -u $(COVDIR)/tree-before.txt $(COVDIR)/tree-after.txt \
	        > $(COVDIR)/tree-diff.txt; then \
		echo "A full run left new files behind:"; \
		grep '^+[^+]' $(COVDIR)/tree-diff.txt; \
		echo "Add the offending paths to .gitignore."; \
		exit 1; \
	fi
	@echo "Working tree unchanged by a full run."

clean:
	rm -rf $(COVDIR) htmlcov .pytest_cache __pycache__
	rm -f .coverage .coverage.* coverage.xml coverage.json
	find . -name '*.pyc' -delete
