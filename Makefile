.PHONY: build test typecheck clean

pip_install_args := . -r requirements.txt --upgrade

ifdef DEV
pip_install_args := --editable $(pip_install_args)
endif

build:
	pip3 install $(pip_install_args)

test:
	python3 -c "import aw_client"

typecheck:
	MYPYPATH="${MYPYPATH}:../aw-core" mypy aw_client --follow-imports=skip --ignore-missing-imports

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
