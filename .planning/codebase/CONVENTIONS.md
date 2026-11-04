---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Conventions

## Coding Standards

- **Python**: Standard library preferred. No mandatory third-party pip packages for core features.
- **Typing**: Use standard typing annotations (`typing.Dict`, `typing.List`, `typing.Optional`, `typing.Tuple`, `typing.Any`).
- **Safety First**: Paper observation only (`config["paper"] == True`). Never execute live broker orders without explicit safety fences.
- **Market Conventions**:
  - Forex pip calculations: 4 decimals for major pairs (0.0001), 2 decimals for JPY pairs (0.01), 2 decimals for Gold XAU/USD (0.01 = 1 cent, 1 pip = 0.10 or 0.01 depending on pair definition in `pairs.py`).
  - Strict candle-close rules: An entry or signal must never trigger on a mere wick touch; the candle must complete and close beyond the confirmation level.
- **Naming**:
  - Functions: `snake_case`
  - Classes: `PascalCase`
  - Constants: `UPPER_SNAKE_CASE`
- **Frontend**:
  - Responsive, modern theme without generic template look.
  - Native browser APIs, high readability, clear charts, accessible contrast.
