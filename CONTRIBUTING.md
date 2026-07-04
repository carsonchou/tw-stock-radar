# Contributing to tw-stock-radar

Thanks for your interest. This document covers how to set up a dev environment,
run tests, and submit a good PR.

---

## Dev setup

```bash
git clone https://github.com/carsonchou/tw-stock-radar
cd tw-stock-radar
pip install -r requirements.txt
cp .env.example .env   # optional — core scanner works without any keys
```

No build step. No separate frontend build. `dashboard.html` is a single
self-contained file that the stdlib HTTP server serves directly.

---

## Running tests

```bash
python -m unittest discover -s tests/ -v
```

~110 tests. stdlib `unittest` only — no pytest, no mocks, zero network calls.
Runs in under 3 seconds. **All tests must pass before submitting a PR.**

The test suite is the source of truth for correctness. If your change touches
indicator logic, chips parsing, or signal detection, add or update a test.

---

## PR guidelines

- **One PR = one topic.** Don't bundle unrelated fixes or features.
- **Green tests required.** CI runs on every push; a red badge blocks merge.
- **No new runtime dependencies** without a strong reason. Current count is 7.
- Keep `dashboard.html` self-contained — no CDN imports, no build step.
- For large changes (new data source, new tab), open an issue first to align
  on the design before writing code.

---

## Where contributions are most useful

| Area | What's needed |
|------|--------------|
| **TPEX price history** | The old monthly endpoint is dead. Find a working API for OTC stock price history so we don't fall back to yfinance for every 上櫃 stock. |
| **Options open interest** | TWSE publishes daily options OI for free. Wire it into the chips pipeline as an additional confluence signal. |
| **Warrant flow** | Complement the Four AI Teachers panel with actual warrant OI change data. |
| **Institutional sector breakdown** | TWSE publishes sector-level foreign flow, not just per-stock. Add it to the Sector tab. |
| **English AI teacher prompts** | `analyst.py` prompts are in Traditional Chinese. An English translation would make the Four Teachers panel accessible to non-Chinese users. |
| **Mobile responsive layout** | The HUD was designed for desktop. A responsive pass on `dashboard.html` would help. |

See [VISION.md](VISION.md) for the full roadmap and what we will not merge.

---

## Code style

- Python: no formatter enforced, but match the existing style (4-space indent,
  type hints on public functions, no unused imports).
- JavaScript: vanilla ES2020 inside `dashboard.html`. No bundler, no transpiler.
- Comments: explain *why*, not *what*. One line max; no block comment essays.

---

## Reporting bugs

Open a GitHub issue with:
1. What you expected to happen
2. What actually happened (error message / wrong output)
3. Python version + pandas/numpy version (`pip show pandas numpy`)
4. Minimal reproduction steps

For data correctness issues (wrong price, wrong chips number), include the
stock code, date, and the source you're comparing against.
