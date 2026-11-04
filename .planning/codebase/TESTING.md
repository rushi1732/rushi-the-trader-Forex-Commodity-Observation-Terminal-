---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Testing

## Test Execution

- Tests are executed using standard Python `unittest`:

```powershell
& "C:\Program Files\LibreOffice\program\python.exe" -m unittest discover -s forex/tests
```

- Existing tests:
  - `forex/tests/test_forex_backtest.py`
  - `forex/tests/test_twelve_data.py`
  - Top-level tests in `tests/`

## Testing Guidelines

- Unit tests verify pure mathematical and structural logic without network calls (using mocked candles or offline data fixtures).
- Ensure spread, pip, session DST, and indicator calculations have deterministic assertions.
