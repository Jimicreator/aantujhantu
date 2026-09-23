# XAUUSD Bots: Diagnostic Findings and Solutions

Date: 2026-09-23

Scope: analysis only. The two trading scripts were intentionally left unchanged. This document records findings, risks, design recommendations, research references, and a practical investigation plan.

## Executive Conclusion

The main problem is not proven to be internet speed.

The strongest evidence points to four separate issues:

1. Bot 2 has a narrow entry gate, so it can legitimately produce very few trades.
2. The current data collection is incomplete and selection-biased. It records executed decisions, but not most rejected opportunities, order failures, stop-modification failures, or connection state.
3. The fixed stop and trailing calculations assume a price-to-dollar relationship that may not match the broker's XAUUSD contract.
4. Risk is defined per position but the account is exposed to multiple layers and both bots at the same time.

The most important next action is observability, not a more aggressive strategy. Before changing entry rules or adding machine learning, the system must prove whether a no-trade event came from the signal engine, spread filter, stale data, broker validation, terminal connection, server rejection, or risk control.

## Files and Data Reviewed

Canonical folder:

`C:\Shared_Quant_Lab`

Observed folders:

- `Logs`
- `Diagnostic_Charts`
- `State`
- `BOT_UPGRADE_PLAN.md`

Observed log files:

- `Logs\Unified_Trade_Log.csv`
- `Logs\Decision_Features.csv`
- `Logs\BOT1_LIVE_SUMMARY.txt`
- `Logs\BOT2_LIVE_SUMMARY.txt`

Observed diagnostic images:

- `Diagnostic_Charts\Bot1_Failure_20260922_225610.png`
- `Diagnostic_Charts\Bot2_Failure_20260922_221441.png`

The attached scripts use the correct absolute lab path. The existing logs, however, appear to come from a different or older deployment because their fields and setup reasons do not exactly match the attached current sources.

## Evidence From the Existing Logs

The existing unified ledger contained approximately:

| Event | Count |
|---|---:|
| BOT_1 OPEN | 52 |
| BOT_1 CLOSE | 9 |
| BOT_2 OPEN | 38 |
| BOT_2 CLOSE | 1 |
| Total OPEN | 90 |
| Total CLOSE | 10 |

The live summaries showed:

| Bot | Closed trades | Gross result | Fees | Net result |
|---|---:|---:|---:|---:|
| BOT_1 | 9 | -$24.42 | -$0.72 | -$25.14 |
| BOT_2 | 1 | -$5.35 | -$0.08 | -$5.43 |

Important interpretation:

- The large number of open rows compared with close rows means the ledger is not a complete reconciled account history.
- Repeated same-second opens suggest duplicate processes, duplicate submissions, or intentional layers that are not distinguishable in the data.
- An open event showing zero realized PnL is normal, but the schema does not clearly separate realized PnL, unrealized PnL, commission, swap, and slippage.
- The current feature file mostly contains successful order decisions. It does not represent all moments when the strategy evaluated the market and decided not to trade.

## Why Bot 2 Appears Slow or Inactive

### 1. Its signal conditions are unusually specific

Bot 2 uses only closed candles, which is generally good for avoiding intrabar repainting, but it requires a precise sequence.

Trending setup requires all of the following:

- Choppiness Index below 50.
- EMA 3 above EMA 7 for a buy, or below for a sell.
- The previous candle must be opposite-colored.
- The latest closed candle must break the previous candle high or low.

Range setup requires all of the following:

- Choppiness Index at or above 50.
- The previous candle must pierce a Bollinger Band.
- The latest close must reclaim the band.
- The latest close must move in the expected direction relative to the previous close.

These are not broad trend or range rules. They are exact two-candle patterns. Many valid market conditions will be classified as no trade.

### 2. The shock filter suppresses the most active periods

The bot rejects the market when five-bar average range is more than 2.5 times the fifty-bar baseline. This protects against unstable conditions, but it also blocks precisely the periods where short-term signals appear most often.

This is a deliberate safety tradeoff, not necessarily a bug. It should be measured rather than guessed. The system needs to count how often the shock filter activates and what hypothetical outcome followed each blocked event.

### 3. The bot scans frequently but uses only one-minute decision state

A 0.2 second loop does not create new one-minute information. The bot repeatedly evaluates the same closed candle until the next candle closes. This can create the impression of activity without creating new signals.

A separate bar-id or candle-time concept is needed for analysis so one market decision is distinguished from hundreds of repeated polling cycles.

### 4. Spread filtering can silently suppress entries

When the spread is above `MAX_ALLOWED_SPREAD`, the current code simply continues. It does not print or record the rejection. A no-trade period could therefore be caused by spread filtering while appearing to be a strategy failure.

### 5. Order failures are invisible

The current code checks `order_check`, but it does not record the return code. It also does not record the `order_send` return code or the terminal error when an order fails.

Therefore the available data cannot distinguish:

- no signal;
- spread too high;
- insufficient rates;
- invalid stop distance;
- invalid fill policy;
- price changed;
- timeout;
- no connection;
- autotrading disabled;
- insufficient margin;
- market closed;
- a successful signal that never became a filled order.

## Why Trailing Appears Not to Work

### 1. Trailing is gated by total basket PnL

The current trailing block runs only when:

`total basket PnL >= required add profit + 0.50`

With one layer, this is approximately `$1.25` before trailing can begin. With two layers, it is approximately `$2.00`. If one position is profitable and another is losing, total basket PnL may remain below the threshold and disable trailing for the profitable position.

This is the clearest code-level explanation for a trail that appears inactive.

### 2. The initial stop conversion is an assumption

The current formula treats a price distance as if it always maps to dollars using a fixed contract relationship. XAUUSD specifications differ between brokers and symbol suffixes.

The actual loss depends on:

- contract size;
- tick size;
- tick value;
- volume step;
- account currency;
- symbol suffix;
- spread and execution price;
- broker stop and freeze levels.

A stop that is mathematically `$3` under one contract specification may not be `$3` under another.

### 3. Trailing requests are not verified

The code sends stop modification requests but discards the response. A modification may be rejected for:

- invalid stops;
- stop too close to current price;
- freeze level;
- no changes;
- position already closed;
- too many requests;
- connection error;
- invalid symbol precision;
- terminal or server restrictions.

Without the return code, “trailing did not work” cannot be separated from “trailing was never eligible.”

### 4. Fixed trailing distance is not volatility-aware

The fixed `0.85` price distance may be too wide during quiet periods and too narrow during fast periods. It also does not account for spread, current ATR, broker stop level, or the price side used for the stop.

### 5. Frequent polling does not guarantee faster server execution

The Python loop can request changes every 0.2 seconds, but the terminal and broker server process requests asynchronously. More frequent requests can cause rate limits or repeated no-change responses. It does not fix network latency.

## Why Losses Can Exceed the Intended Amount

The configured `$3` value is per position, not a hard maximum for the whole bot or account.

With three layers, the planned exposure can be approximately:

- one layer: `$3` planned risk;
- two layers: `$6` planned risk;
- three layers: `$9` planned risk.

The actual realized result can be larger because of:

- spread expansion;
- slippage or gaps;
- commission and swap;
- wrong contract-value assumptions;
- market-close panic liquidation;
- multiple bot processes;
- both bots trading the same symbol;
- stop execution on the opposite quote side;
- delayed or rejected stop modifications;
- account-level exposure not included in either bot's local calculation.

A per-position stop is not the same as an account-level risk guarantee.

## Shared Folder Assessment

The folder is present and organized into:

- `Logs`: summaries, feature data, and unified trade records.
- `Diagnostic_Charts`: failure images.
- `State`: vault JSON files.
- `BOT_UPGRADE_PLAN.md`: earlier architecture and risk analysis.

The current folder is useful as a starting point, but it is not yet a complete audit system. Missing or insufficient categories include:

- decision evaluations, including no-trade outcomes;
- order-check responses;
- order-send responses;
- stop modification responses;
- terminal connection state;
- broker/server time;
- per-position snapshots;
- broker-confirmed deal reconciliation;
- account-wide risk state;
- slippage and execution latency;
- strategy and source version identifiers.

The two vault files are also bot-specific. A shared account-wide risk state is needed if both bots operate on the same account or symbol.

## Recommended Data Design

These are design recommendations, not implementations.

### Decision audit dataset

One row per unique candle decision, whether or not a trade occurred.

Recommended fields:

- event timestamp UTC;
- broker server timestamp;
- account login;
- bot id;
- strategy version;
- symbol actually selected;
- timeframe;
- candle open time;
- bid, ask, spread;
- rates available and latest candle age;
- regime;
- signal direction;
- signal reason;
- rejection reason;
- ATR and normalized ATR;
- Bollinger width and band position;
- Choppiness Index;
- EMA values and separation;
- spread-to-ATR ratio;
- current layer count;
- combined bot PnL;
- combined account exposure;
- cooldown state;
- order-check retcode;
- order-send retcode;
- terminal last error;
- model version and model decision, when a model exists.

### Position snapshot dataset

Record open positions periodically and at every important event.

Recommended fields:

- position ticket;
- order ticket;
- deal ticket;
- entry price;
- current bid and ask;
- current stop;
- current floating PnL;
- commission;
- swap;
- maximum favorable excursion;
- maximum adverse excursion;
- last trail request time;
- last trail result code;
- close reason.

### Reconciled trade ledger

The broker's confirmed deal history should be the source of truth for realized results. Bot-generated open and close intentions should be retained as separate events and reconciled against broker deals.

## CSV and Spreadsheet Recommendations

The visible `########` in Excel is normally a display-width issue, not proof of corrupt data. Date columns should be formatted as an explicit date/time format and widened.

For future exports:

- use ISO 8601 timestamps with UTC marker;
- use separate date and time columns if spreadsheet users need filtering;
- use empty values rather than numeric zero for fields that are not applicable;
- keep realized PnL, unrealized PnL, commission, swap, and slippage separate;
- include a schema/version row or companion schema document;
- use UTF-8 with a clear delimiter;
- avoid rewriting historical rows;
- create a separate human-readable report from the append-only event data.

A clean spreadsheet should distinguish these event types:

- `SIGNAL_EVALUATED`
- `SIGNAL_BLOCKED`
- `ORDER_CHECKED`
- `ORDER_REJECTED`
- `ORDER_FILLED`
- `STOP_MODIFY_REQUESTED`
- `STOP_MODIFY_REJECTED`
- `POSITION_SNAPSHOT`
- `CLOSE_REQUESTED`
- `DEAL_CONFIRMED`
- `RECONCILED`

## Machine Learning Assessment

There is no actual intelligence model in either supplied script. The current behavior is deterministic rule logic plus feature logging.

The current feature logging is insufficient for training because it mostly logs after a successful trade. This creates selection bias: the future model sees what the rules allowed, but not what the rules rejected.

A safer model sequence is:

1. Record every candidate and every rejection.
2. Attach future labels after fixed horizons such as 1, 5, 15, and 30 minutes.
3. Measure maximum favorable and adverse excursion.
4. Include all spread, commission, swap, and slippage costs.
5. Use chronological walk-forward validation.
6. Run the model in shadow mode.
7. Use the model first as a trade filter, not as an unrestricted order generator.
8. Promote only when it improves net expectancy after costs and remains stable across sessions and regimes.

Useful first model targets:

- probability of positive net return after costs;
- probability of hitting adverse excursion before favorable excursion;
- expected net PnL at a fixed horizon;
- probability that the current signal should be blocked.

Do not train on raw trade count or win rate alone. A model can increase win rate while losing money if its losses are larger or its costs are higher.

## Strategy Improvements to Research and Test

### Risk controls

- Define unit risk, basket risk, and account-session risk separately.
- Use account-currency risk calculations based on broker contract data.
- Include spread, commission, swap, and estimated slippage in pre-trade risk.
- Permit a new layer only if the entire basket remains within the risk budget.
- Coordinate both bots through one account-wide XAUUSD exposure limit.
- Add a daily loss lock and a consecutive-loss lock independent of the virtual vault.
- Treat the virtual vault as accounting logic, not a real withdrawal or capital segregation mechanism.

### Entry quality

- Measure signal frequency by regime before loosening thresholds.
- Consider a two-stage signal: setup detection followed by confirmation, instead of requiring one exact candle pattern.
- Normalize slope and EMA separation by ATR so thresholds adapt to volatility.
- Add higher-timeframe direction context from M5 and M15.
- Measure spread divided by ATR rather than spread alone.
- Avoid trading immediately around major news and session transitions until behavior is known.

### Position management

- Base trailing distance on ATR with broker minimum-distance checks.
- Separate initial protection, break-even protection, and profit trailing.
- Manage a profitable layer even if another layer is losing.
- Rate-limit stop modifications by time and meaningful price movement.
- Record every stop-modification response.
- Decide explicitly whether weak layers should be closed individually or whether the entire basket should close.

### Reliability

- Use a unique idempotency key based on account, symbol, strategy version, candle time, and layer number.
- Prevent duplicate running processes for the same bot and magic number.
- Verify the selected symbol and log the actual symbol suffix.
- Record terminal connection state and server return codes.
- Prefer the broker-supported filling mode rather than assuming FOK is valid for every symbol.
- Reconcile open positions and broker deals after every restart.

## MT5 Research Findings

The official MetaTrader 5 Python documentation supports these conclusions:

- `order_check()` checks whether a request is valid and funds are sufficient, but a successful check does not guarantee that the trade executes.
- `order_send()` returns a trade result whose `retcode` must be inspected.
- `symbol_info()` exposes broker-specific symbol properties used to validate precision, tick information, stop levels, and freeze levels.
- `order_calc_profit()` estimates profit in the account currency and is preferable to assuming a universal contract formula.
- Trade return codes distinguish important failures such as invalid stops, price changes, timeout, connection loss, too many requests, invalid fill, and client/server trading disablement.

References:

- https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py
- https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py
- https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfo_py
- https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py
- https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes

## Recommended Investigation Order

### Stage 1: Observe without changing strategy behavior

Collect a complete decision and execution audit for at least several trading sessions. Do not loosen entries or increase risk during this stage.

Questions to answer:

- How many evaluations end in each rejection reason?
- How often is spread above the limit?
- How often are rates missing or stale?
- How often does the shock filter activate?
- How many valid signals fail order check?
- How many sent orders fail at the server?
- What are the exact stop-modification return codes?
- What is the measured request-to-confirmation latency?
- Are multiple processes using the same magic number?

### Stage 2: Reconcile reality

Compare bot logs with MT5 history by account, symbol, magic number, order ticket, deal ticket, and position ticket. Resolve duplicate opens and missing closes before assessing strategy performance.

### Stage 3: Test risk in a controlled environment

Use a demo account or a very small test environment. Validate actual tick value, stop distance, filling mode, spread behavior, slippage, and server response under normal and fast markets.

### Stage 4: Evaluate strategy frequency and expectancy

Only after the data is trustworthy, compare the current strict signal rules with candidate thresholds using walk-forward testing. Track net expectancy, drawdown, adverse excursion, and cost-adjusted results.

### Stage 5: Shadow model

Train a simple trade-filter candidate offline, run it without order authority, and compare its hypothetical blocked and allowed trades with the current rule engine.

## Stop Conditions Before Live Expansion

Do not increase lot size, maximum layers, or signal aggressiveness until all of these are true:

- every order and stop modification has a recorded result code;
- account and broker history reconcile with the local ledger;
- duplicate processes are ruled out;
- basket and account-wide risk are bounded;
- actual broker contract math is verified;
- the strategy has positive cost-adjusted expectancy out of sample;
- the model has passed shadow mode;
- the system can explain every no-trade and every loss.

## Final Assessment

Bot 2 is probably not failing because it is computationally slow. It is mostly either waiting for a highly specific setup or silently filtering/rejecting events that are not recorded. The trailing issue is more plausibly caused by its basket-level eligibility gate, fixed stop assumptions, and unverified modification results than by internet speed alone.

The cleanest improvement is to make the system explain itself first. Once the audit data proves where the failure occurs, strategy adjustments and machine-learning features can be evaluated without guessing or risking another uncontrolled multi-dollar loss.
