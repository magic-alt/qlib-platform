---
status: ACTIVE
owner: backtesting
applies_to_commit: 3a0e087cf1f14b33b9ba9b0edce883bc672b7ccd
last_verified: 2026-09-16
---

# A-share market realism contract

This document defines the deterministic A-share market rules used by qlib-platform research backtests. The contract deliberately separates upstream Qlib parity from the platform's China-market realism extension.

## Profiles

| Profile | Rule set | Cost model | Fill model | Intended use |
| --- | --- | --- | --- | --- |
| `official_parity_v1` | `qlib_official_parity_v1` | `qlib_exchange_config_cost_v1` | `qlib_exchange_daily_v0.9.7` | Reproduce the frozen upstream Qlib benchmark assumptions. |
| `production_realism_v1` | `cn_cash_equity_production_realism_v1` | `cn_cash_equity_fee_schedule_v1` | `conservative_daily_bar_v1` | Deterministic A-share research with dated market rules, fees and cash/share accounting. |

The two profiles are not interchangeable. A benchmark run that binds `official_parity_v1` must not be compared as though it used production-realistic fills and historical China-market rules. Every governed research identity records the market rule set, cost model, fill model and execution-contract hash.

## Deterministic rules

The production-realism rule set is machine readable in `qlib_platform.backtesting.market_rule_set`. Each rule has a unique identifier, effective interval, source description and a named conformance fixture.

Key certified semantics are:

- **T+1 / sellable inventory** — a position tracks total shares separately from currently sellable shares. Same-session buys remain locked until the next trading session.
- **Board lots / odd lots** — ordinary A-share buys use 100-share lots. STAR buys require at least 200 shares and then allow one-share increments. A residual position below the applicable minimum can only be sold in full; the simulator never rounds a sell in a way that creates or destroys shares.
- **Price-limit history** — main board, risk-warning/ST, ChiNext, STAR and BSE regimes are date/board aware. ChiNext switches to the 20% regime on 2020-08-24. Main-board risk-warning stocks switch from 5% to 10% on 2026-07-06. Main-board registration-based IPOs use the first-five-session no-limit regime from 2023-04-10. BSE uses first-session no-limit semantics followed by a 30% daily limit. Legacy main-board IPO first-session asymmetric limits are fail-closed unless explicit upper/lower limits are supplied by the dataset.
- **Suspension vs provider absence** — an explicit `paused`, `SUSPENDED`, `HALTED` or `TEMP_HALTED` row is a known non-tradable market state. A missing instrument/date row is instead recorded as `missing_market_data`; the two cases are never collapsed into a zero-price fill.
- **Directional limit fillability** — the conservative daily-bar model rejects buys at a locked upper limit and sells at a locked lower limit. The opposite directions remain eligible when other constraints permit them.
- **Listing lifecycle** — `PRE_LISTING`/`NOT_LISTED` and `DELISTED`/`POST_DELISTING` states are explicitly non-tradable, supporting a PIT universe without silently backfilling future listings or dead securities.
- **Capacity hook** — deterministic market conformance is separate from liquidity assumptions. `max_participation_rate`, spread, slippage and square-root impact remain explicit research parameters rather than alpha logic.

## Dated fee schedule

For the production-realism simulator, `commission_bps` is a **broker net-commission assumption**. It is added to the explicitly modeled statutory/market charges below. If a broker quote is an all-in commission that already contains exchange handling or regulatory charges, it must be decomposed before being used here; otherwise the backtest would double count those costs.

| Regime | Effective interval | Sell stamp tax | Transfer fee | CSRC regulatory fee | Exchange handling fee |
| --- | --- | ---: | ---: | ---: | ---: |
| `cn_equity_fee_2015_08_01` | 2015-08-01 .. 2022-04-28 | 10 bps | 0.2 bps | 0.2 bps | 0.487 bps |
| `cn_equity_fee_2022_04_29` | 2022-04-29 .. 2023-08-27 | 10 bps | 0.1 bps | 0.2 bps | 0.487 bps |
| `cn_equity_fee_2023_08_28` | 2023-08-28 .. current | 5 bps | 0.1 bps | 0.2 bps | 0.341 bps |

The implementation records broker commission, transfer fee, regulatory fee, exchange handling fee, stamp tax, total fees and the selected `fee_regime_id` on every fill. The schedule intentionally fails closed outside its certified history instead of extrapolating a current fee backward.

Sources represented by the rule contract include the Shanghai Stock Exchange, Shenzhen Stock Exchange and Beijing Stock Exchange trading rules, China Securities Depository and Clearing transfer-fee schedules, the 0.002% two-sided regulatory fee collected for the CSRC, the 2023 SSE/SZSE A-share handling-fee reduction from 0.00487% to 0.00341%, and the Ministry of Finance / State Taxation Administration 2023 stamp-tax reduction. The contract stores source descriptions rather than fetching web content during CI.

The legacy no-date `execution_fees()` helper remains a configured-current compatibility surface for existing callers. Production-realism fills always pass a trade date and therefore use the dated statutory schedule.

## Corporate actions and price basis

`production_realism_v1` uses **raw/unadjusted execution and NAV prices** when explicit corporate actions are applied. Supported deterministic research events are:

- `CASH_DIVIDEND`: add economic cash entitlement to NAV;
- `SHARE_MULTIPLIER`: adjust total and sellable raw-share quantities.

`effective_date` is the research **economic effective date**, normally the ex-right/ex-dividend date used to preserve raw-price NAV continuity. It is not a claim about the broker's actual cash settlement date. Where entitlement, listing or cash-in-lieu timing needs to be modeled separately, the dataset must provide a more explicit event rather than relying on inference.

Applying corporate actions while `price_basis="adjusted"` fails closed. This prevents a common double-counting error where an adjusted feature/execution price already reflects an event and the portfolio NAV is adjusted a second time.

A share multiplier that would create fractional raw shares also fails closed unless explicit cash-in-lieu evidence is modeled.

## Reproducible ledgers

The deterministic simulator exposes:

- fill ledger, including fee components, rule/cost/fill identities and post-fill cash/position state;
- rejection ledger with reason and rule identity;
- daily cash/market-value/equity ledger;
- daily total/sellable position ledger;
- corporate-action before/after ledger.

These artifacts make a frozen fixture manually reproducible day by day and allow cross-engine differences to be classified as **rule**, **cost**, or **fill** differences instead of a single return delta.

## Qlib boundary and known deviation

Qlib 0.9.7 `Exchange` remains the owner of the official-parity lane. Its generic exchange configuration can express a fixed trade unit, fixed costs, volume thresholds and limit expressions, but it does not by itself certify all dated A-share board rules, odd-lot tails, lifecycle states and explicit corporate-action cash/share accounting defined above.

Therefore:

1. upstream Qlib is not monkey-patched to pretend that it implements the full production-realism contract;
2. official Alpha158 parity runs bind `qlib_official_parity_v1` and `qlib_exchange_v0.9.7`;
3. the production-realism contract is a versioned local research adapter executed by `deterministic_ashare_simulator_v1`;
4. binding `production_realism_v1` to Qlib's generic Exchange fails closed instead of relabeling upstream behavior;
5. a future Qlib/LEAN secondary execution validation must report the active rule/cost/fill/engine identities and attribute deviations to those dimensions.

This separation preserves upstream parity while making China-market extensions explicit, testable and replaceable. The same known deviation is recorded on `backtest.exchange` in the Qlib capability matrix.

## Configuration and identity

Governed Qlib `train-select` research defaults to the official-parity rule identity and explicitly binds `qlib_exchange_v0.9.7`. It must not be relabeled as production realism. The deterministic A-share simulator binds `production_realism_v1` to `deterministic_ashare_simulator_v1`; unbound contracts may be inspected for planning, but they are not evidence that an engine executed those semantics.

Changing the resolved rule-set version, execution engine, cost model, fill model, broker-cost assumption, deal price, trade unit or participation rate changes the execution-contract hash. Resolved `ResearchExperimentSpec` objects bind the Qlib execution contract, so a market-rule or engine change changes `researchExperimentId` even when the model, alpha and dataset are unchanged.

## Offline conformance corpus

`tests/test_ashare_market_realism_conformance.py`, `tests/test_ashare_market_rule_binding.py` and `tests/test_ashare_fee_schedule.py` form the blocking, no-network corpus. They cover T+1, sellable quantity, board/odd lots, directional upper/lower limits, suspension vs missing bars, historical regime boundaries, fee boundaries/components, corporate-action NAV continuity, adjusted-price double-count protection, listing lifecycle, execution-profile separation and experiment identity.

Real Tushare release spot checks can be added as governed data validation, but ordinary PR CI must remain deterministic and must not require vendor credentials or network access.
