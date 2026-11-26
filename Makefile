.PHONY: test lint venv

PY := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

venv:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:
	@$(PY) -m unittest discover -s tests && echo "TEST PASS" || { echo "TEST FAIL"; exit 1; }

lint:
	@$(PY) -m py_compile $$(find sb tests -name '*.py') && echo "LINT PASS" || { echo "LINT FAIL"; exit 1; }
