.PHONY: build

pip_install_args := . -r requirements.txt

ifdef DEV
pip_install_args := --editable $(pip_install_args)
endif

build:
	pip3 install $(pip_install_args)

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
