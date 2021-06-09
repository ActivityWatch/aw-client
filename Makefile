.PHONY: build test typecheck clean

build:
	poetry install

test:
	python -c "import aw_client"
	pytest -s -vv tests/test_requestqueue.py

test-integration:
	pytest -v tests/test_client.py

typecheck:
	poetry run mypy

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
