PY ?= /opt/homebrew/opt/python@3.11/bin/python3.11
VENV := .venv
VPY := $(VENV)/bin/python

.PHONY: setup venv native models test bench-phase0 bench-phase0-quick

setup: venv native models  ## Everything needed to run offline afterwards

venv:
	test -x $(VPY) || $(PY) -m venv $(VENV)
	$(VPY) -m pip install -q -r requirements.txt

native:
	./scripts/setup_native.sh

models:
	$(VPY) scripts/download_models.py

test:
	$(VPY) -m pytest -q tests

bench-phase0:
	$(VPY) benchmarks/phase0/run_phase0.py

bench-phase0-quick:
	$(VPY) benchmarks/phase0/run_phase0.py --quick
