PY ?= .venv/bin/python
RUFF ?= .venv/bin/ruff

.PHONY: test lint fmt

test:
	$(PY) -m pytest

lint:
	$(RUFF) check tools tests
	$(RUFF) format --check tools tests

fmt:
	$(RUFF) check tools tests --fix
	$(RUFF) format tools tests
