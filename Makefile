.PHONY: build test typecheck

build:
	pipenv install

test:
	make typecheck

typecheck:
	mypy aw_client --ignore-missing-imports
