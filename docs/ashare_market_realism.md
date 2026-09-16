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

Broker commission is an explicit research assumption (`commission_bps` plus `min_commission`). Statutory/market fees are selected by trade date:

| Regime | Effective interval | Sell stamp tax | Transfer fee |
| --- | --- | ---: | ---: |
| `cn_equity_fee_2015_08_01` | 2015-08-01 .. 2022-04-28 | 10 bps | 0.2 bps |
| `cn_equity_fee_2022_04_29` | 2022-04-29 .. 2023-08-27 | 10 bps | 0.1 bps |
| `cn_equity_fee_2023_08_28` | 2023-08-28 .. current | 5 bps | 0.1 bps |

The implementation records commission, transfer fee, stamp tax, total fees and the selected `fee_regime_id` on every fill. The schedule intentionally fails closed outside its certified history instead of extrapolating a current fee backward.

Sources represented by the rule contract include the Shanghai Stock Exchange, Shenzhen Stock Exchange and Beijing Stock Exchange trading rules, China Securities Depository and Clearing transfer-fee schedules, and the Ministry of Finance / State Taxation Administration 2023 stamp-tax reduction. The contract stores source descriptions rather than fetching web content during CI.

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
2. official Alpha158 parity runs bind `qlib_official_parity_v1`;
3. the production-realism contract is a versioned local research adapter;
4. a future Qlib/LEAN secondary execution validation must report the active rule/cost/fill identities and attribute deviations to those dimensions.

This separation preserves upstream parity while making China-market extensions explicit, testable and replaceable.

## Configuration and identity

Governed Qlib research defaults to the official-parity rule identity to avoid silently changing the frozen benchmark protocol. A caller that explicitly selects a production-realism research adapter uses:

```yaml
research:
  market_rule_profile: production_realism_v1
```

Changing `market_rule_profile`, the resolved rule-set version, cost model, fill model, broker-cost assumption, deal price, trade unit or participation rate changes the execution-contract hash. Resolved `ResearchExperimentSpec` objects bind that contract, so a market-rule change changes `researchExperimentId` even when the model, alpha and dataset are unchanged.

## Offline conformance corpus

`tests/test_ashare_market_realism_conformance.py` is the blocking, no-network corpus. It covers T+1, sellable quantity, board/odd lots, directional upper/lower limits, suspension vs missing bars, historical regime boundaries, fee boundaries, corporate-action NAV continuity, adjusted-price double-count protection, listing lifecycle, execution-profile separation and experiment identity.

Real Tushare release spot checks can be added as governed data validation, but ordinary PR CI must remain deterministic and must not require vendor credentials or network access.
