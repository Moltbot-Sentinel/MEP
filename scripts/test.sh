#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/.."

echo "=== Installing test dependencies ==="
pip install -q -r requirements-test.txt

echo ""
echo "=== Lint ==="
ruff check hub/ node/ core/ tests/

echo ""
echo "=== Tests ==="
python -m pytest tests/ -v --tb=short --cov --cov-report=term-missing
