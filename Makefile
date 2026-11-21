PY ?= /opt/homebrew/opt/python@3.11/bin/python3.11
VENV := .venv
VPY := $(VENV)/bin/python

.PHONY: setup venv native models web web-build browser dev test bench-phase0 bench-phase0-quick bench-phase1 check-adapters eval

setup: venv native models web browser  ## Everything needed to run offline afterwards

venv:
	test -x $(VPY) || $(PY) -m venv $(VENV)
	$(VPY) -m pip install -q -r requirements.txt

native:
	./scripts/setup_native.sh

models:
	$(VPY) scripts/download_models.py

web:
	cd web && npm ci && npm run build

browser: venv
	$(VPY) -m playwright install chromium --only-shell

web-build:
	cd web && npm run build

dev: web-build  ## Offline after make setup; one worker, one call
	$(VPY) -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --ws-max-size 4096 --ws-max-queue 16 --no-access-log

test:
	cd web && npm run build
	$(VPY) -m pytest -q tests

bench-phase1:
	$(VPY) benchmarks/phase1/run_live.py --browser

check-adapters:
	$(VPY) benchmarks/phase1/check_adapters.py

eval:
	$(VPY) scripts/run_eval.py

bench-phase0:
	$(VPY) benchmarks/phase0/run_phase0.py

bench-phase0-quick:
	$(VPY) benchmarks/phase0/run_phase0.py --quick
