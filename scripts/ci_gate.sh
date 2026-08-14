#!/usr/bin/env bash

set -euo pipefail

uv_run() {
  uv run --locked "$@"
}

syntax_check() {
  echo "==> backend-gate: Python syntax check"
  uv_run python -m py_compile backend/__init__.py
  uv_run python -m py_compile backend/main.py backend/server.py backend/src/config.py backend/src/auth.py
  uv_run python -m py_compile backend/src/analyzer.py backend/src/notification.py backend/src/storage/__init__.py
  uv_run python -m py_compile backend/src/scheduler.py backend/src/search_service.py
  uv_run python -m py_compile backend/src/market_analyzer.py backend/src/stock_analyzer.py
  uv_run python -m py_compile backend/data_provider/*.py
}

flake8_checks() {
  echo "==> backend-gate: flake8 critical checks"
  uv_run flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
}

deterministic_checks() {
  echo "==> backend-gate: local deterministic checks"
  ./scripts/test.sh code
  ./scripts/test.sh yfinance
}

offline_test_suite() {
  echo "==> backend-gate: offline test suite"
  uv_run python -m pytest -m "not network"
}

run_all() {
  syntax_check
  flake8_checks
  deterministic_checks
  offline_test_suite
  echo "==> backend-gate: all checks passed"
}

phase="${1:-all}"

case "$phase" in
  all)
    run_all
    ;;
  syntax)
    syntax_check
    ;;
  flake8)
    flake8_checks
    ;;
  deterministic)
    deterministic_checks
    ;;
  offline-tests)
    offline_test_suite
    ;;
  *)
    echo "Usage: $0 [all|syntax|flake8|deterministic|offline-tests]" >&2
    exit 2
    ;;
esac
