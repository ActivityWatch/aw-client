.PHONY: build

build:
	python3 setup.py install

clean:
	rm -rf build dist
	rm -rf aw_client/__pycache__
