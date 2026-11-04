---
last_mapped_commit: 4034834f86f2d6cc8a6d98ceb10b64e09ce2a387
last_mapped_at: 2026-11-03
---
# Directory Structure

```
day-trader-skill-main/
├── .planning/                  # GSD planning directory
│   ├── codebase/              # Codebase analysis maps
│   ├── PROJECT.md             # Project definition & milestones
│   ├── REQUIREMENTS.md        # Feature requirements
│   ├── ROADMAP.md             # Implementation roadmap
│   └── STATE.md               # Current execution state
├── forex/                     # Core Forex & Gold paper trading platform
│   ├── alerts.py              # Sound & desktop alert notifications
│   ├── backtest.py            # Historical backtest engine
│   ├── config.json            # Base risk and account configuration
│   ├── twelvedata_config.json # Twelve Data API settings
│   ├── dashboard.py           # HTTP server and embedded web UI (port 8765)
│   ├── data.py                # Yahoo Finance, Twelve Data & Binance fetcher
│   ├── entry.py               # Bar-by-bar entry validation
│   ├── exit.py                # Stop loss & take profit target planner
│   ├── observe.py             # Live market observer & automated signal builder
│   ├── pairs.py               # Currency pairs & Gold specifications (pip values, lot sizes)
│   ├── plan.json              # Active observation plan state
│   ├── pricing.py             # Spread & execution modeling
│   ├── risk.py                # Pre-trade risk enforcement
│   ├── scanner.py             # Market scanning algorithms
│   ├── sessions.py            # Market hours & active trading sessions
│   ├── sizer.py               # Position sizing calculations
│   ├── ticket.py              # Paper trade order ticket formatters
│   ├── state/                 # State persistence
│   └── tests/                 # Unit tests for forex package
├── trader/                    # Common configuration for US equity tools
├── trader-alerts/             # Price alerts skill
├── trader-analyst/            # SEC earnings analysis skill
├── trader-broker/             # Paper order executor skill
├── trader-budget/             # Position budget sizing skill
├── trader-entry/              # Stock entry timing skill
├── trader-exit/               # Stock exit planning skill
├── trader-news/               # Stock news monitor skill
├── trader-pelosi/             # Congressional trade disclosure tracker
├── trader-risk/               # Portfolio risk manager skill
├── trader-scanner/            # Stock chart scanner skill
├── trader-timemachine/        # Monte Carlo simulation skill
├── tests/                     # Top-level test suite
├── run_dashboard.bat          # Windows batch script to launch web dashboard
└── README.md                  # Project documentation
```
