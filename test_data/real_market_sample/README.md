# Real Market Test Fixture — `test_data/real_market_sample/`

This is a **small, reproducible, real** A-share market data fixture used
only for automated tests and the minimal demo. It is **not** a
representative market sample and is **not** investment data.

## Provenance

| Field | Value |
|---|---|
| Security | 600519 (Kweichow Moutai, A-share) |
| Date range | 2024-01-01 .. 2024-01-10 (inclusive) |
| Data source | The project's standalone `src/data_sources/astock.py` via
  `src/real_data_adapter.py`; OHLCV source `eastmoney_http`. No external
  Agent repository is loaded or called at runtime. |
| Fetch date | 2026-07-17 |
| Snapshot fundamentals | **Disabled** (`--no_snapshot_fundamentals`) |
| Adapter / data source version | 0.2 / 1.0 |
| Volume unit | Shares (`Eastmoney` board lots converted ×100) |

## Fetch command

```bash
python -B src/run_fetch_real_data.py ^
  --tickers 600519 ^
  --start_date 2024-01-01 ^
  --end_date 2024-01-10 ^
  --output_dir test_data/real_market_sample ^
  --no_snapshot_fundamentals
```

## Files and row counts

| File | Rows | Purpose |
|---|---|---|
| `price.csv` | 7 | Real OHLC (trade_date, ticker, open, high, low, close) |
| `volume.csv` | 7 | Real volume (date, stock_code, volume; turnover empty — no reliable source) |
| `fundamentals.csv` | 0 (header only) | Header only — see "Why no fundamentals" below |
| `industry.csv` | 1 | Real industry name from Eastmoney f127 (白酒Ⅱ) |
| `calendar.csv` | 10 | 2024-01-01..2024-01-10; `is_trading_day` marked from real OHLCV dates |
| `fetch_metadata.json` | — | Auditable fetch metadata (ticker, dates, source, row counts, command) |

## Why `--no_snapshot_fundamentals`

The quote service's PE/PB/ROE are **current snapshots**, not a historical
point-in-time fundamentals database. Backfilling a current
snapshot into 2024 historical dates would **fabricate `announce_date`**
and introduce **look-ahead bias**. To keep this fixture honest for
2024-01-01..2024-01-10, fundamentals are fetched as header-only
(`--no_snapshot_fundamentals`). The pipeline continues with a warning
(not a failure) when fundamentals are empty; the Critic treats
"fundamental values present without announce_date" as a failure but
"no fundamental values" as a benign warning.

## Data integrity

- All OHLCV rows are **real** market data returned by the adapter; no
  row was hand-filled or fabricated.
- No API key, cookie, token, or personal cache is committed.
- `fetch_metadata.json` records the auditable fetch information
  (ticker, dates, source label, row counts, the exact command).
- Code-attribution and license details for adapted parsing logic are in the
  repository-level `NOTICE` and `third_party/licenses/Apache-2.0.txt`.

## Disclaimer

This fixture is for **software testing only**. It is a tiny slice of one
security over 10 calendar days and does **not** represent the broader
market. It is **not investment advice** and must not be used for
trading, backtesting, or any investment decision.
