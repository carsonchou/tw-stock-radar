# Vision — tw-stock-radar

tw-stock-radar is a Taiwan stock market intelligence tool for individual traders and researchers.
It runs locally, uses only free government open data, and shows you what institutions are doing.

This document covers where the project is headed and what it will not become.

---

## What it is

A local scanner that wires together Taiwan's free exchange data — price history, institutional flow (T86),
margin balance, TDCC ownership distribution — and presents it in a single queryable dashboard.

The core loop: scan all 1,900+ TWSE + TPEX stocks daily → score on 4 dimensions → surface chips
confluence setups → track real post-signal outcomes.

**Honest position on signals:** technical signals alone show ~50% win rate on the full universe.
The tool's value is not signal-following — it is multi-source information aggregation that would take
hours to assemble manually from five separate government portals.

---

## Current priorities

1. Data correctness — price history coverage, TPEX fallback, chips endpoint reliability
2. Cold-start UX — new users should be productive in under 30 minutes
3. Real signal tracking — the Track Record tab stays honest (live prices, not replayed backtest)
4. Test coverage — zero-network test suite that catches regressions immediately

---

## What we want to add

- **TPEX direct price API** — the current endpoint is dead; find a replacement so OTC stocks
  don't fall back to yfinance
- **Options open interest** — TWSE publishes OI data for free; high-demand signal for warrant traders
- **Warrant flow** — complement the "四師" panel with actual warrant OI changes
- **Institutional sector breakdown** — TWSE publishes sector-level foreign flow, not just per-stock
- **Better mobile view** — the HUD was designed for desktop; responsive pass needed

---

## What this project is not, and won't become

| Not this | Why |
|---|---|
| Paid signal service | Signals are ~50% without confirmed edge; monetizing them would be dishonest |
| Real-time tick data | TWSE MIS is ~20s snapshots; true tick requires a paid broker API we don't wrap |
| Broker branch tracking | TWSE branch reports have CAPTCHA; not worth the fragility |
| Automated trade execution | Out of scope; this is a scanner, not an order management system |
| General-purpose stock screener for other markets | Taiwan-specific data (T86, TDCC) is the differentiator; keep it focused |

---

## Contributing

One PR per topic. The test suite (`python -m unittest discover -s tests/`) is the source of truth
for correctness — every change that touches indicator logic or chips parsing needs a test.

Good first contributions: TPEX price fallback, English translations for `analyst.py` prompts,
additional free Taiwan data sources (options OI, warrants).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.
