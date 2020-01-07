.PHONY: build test typecheck clean

ifdef DEV
installcmd := poetry install
else
installcmd := pip3 install .
endif

build:
	$(installcmd)

test:
	python3 -c "import aw_client"
	pytest -s -vv tests/test_requestqueue.py

typecheck:
	MYPYPATH="${MYPYPATH}:../aw-core" mypy aw_client --follow-imports=skip --ignore-missing-imports

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
