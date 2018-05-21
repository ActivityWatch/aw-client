.PHONY: build test typecheck clean

pip_install_args := . -r requirements.txt --upgrade

ifdef DEV
pip_install_args := --editable $(pip_install_args)
endif

build:
	pip3 install $(pip_install_args)

test:
	python3 -c "import aw_client"
	pytest -s -vv tests/test_requestqueue.py

test-cli-query:
	python3 -m aw_client --host localhost:5666 query --start 2018-01-01 tests/queries/aw-development.awquery2

typecheck:
	MYPYPATH="${MYPYPATH}:../aw-core" mypy aw_client --follow-imports=skip --ignore-missing-imports

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
