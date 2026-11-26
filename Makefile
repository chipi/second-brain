.PHONY: test lint

test:
	@python3 -m unittest discover -s tests && echo "TEST PASS" || { echo "TEST FAIL"; exit 1; }

lint:
	@python3 -m py_compile $$(find sb tests -name '*.py') && echo "LINT PASS" || { echo "LINT FAIL"; exit 1; }
