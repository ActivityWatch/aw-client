.PHONY: build

pip_install_args := .

ifdef DEV
pip_install_args := --editable $(pip_install_args)
endif

build:
	pip3 install . -r requirements.txt

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
