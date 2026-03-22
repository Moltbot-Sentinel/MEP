# Testing MEP

## Quick Start

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests + lint in one command
bash scripts/test.sh
```

## Running Tests

```bash
# All unit tests
python -m pytest tests/ -v

# Single test file
python -m pytest tests/test_hub_api.py -v

# With coverage report
python -m pytest tests/ -v --cov --cov-report=term-missing
```

## Linting

```bash
ruff check hub/ node/ core/ tests/
```

## Test Structure

| File | What it tests |
|------|--------------|
| `tests/test_hub_auth.py` | Ed25519 signature verification, node ID derivation |
| `tests/test_hub_api.py` | Hub API endpoints (register, balance, task lifecycle) |
| `tests/test_max_purchase_price.py` | Data market budget safety logic |
| `tests/test_sentinel_engineer_v2.py` | Autonomous agent: parser, circuit breaker, code executor |

## Integration Tests

Integration tests in `node/test_*.py` require a running Hub:

```bash
# Terminal 1: Start Hub
docker-compose up

# Terminal 2: Run integration tests
python node/test_auction.py
python node/test_three_markets.py
python node/test_dm.py
```

## Writing New Tests

1. Create test files in `tests/` with the prefix `test_`
2. Use `unittest.TestCase` or plain pytest functions
3. For hub endpoint tests, use `FastAPI TestClient` (see `test_hub_api.py` for examples)
4. Tests run on both Ubuntu and Windows in CI — avoid hardcoded Unix paths

## CI

Pull requests automatically run lint + tests via GitHub Actions on both Ubuntu and Windows. PRs must pass before merging.
