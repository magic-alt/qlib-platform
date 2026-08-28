---
status: ACTIVE
owner: data
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Data Schema

The authoritative field set is the selected DataRelease profile plus the configured Qlib include fields.
This page records the current integrated research profile.

| Field family | Fields | Semantics |
| --- | --- | --- |
| Price | `open high low close vwap change` | price/return inputs under the declared adjustment contract |
| Activity | `volume money turnover_rate turnover_rate_f volume_ratio` | volume and traded-notional measures; units come from source schema |
| Adjustment | `factor` | reversible total-return adjustment factor used consistently with price/volume |
| Tradability | `paused up_limit down_limit is_limit_up is_limit_down` | session-level trading constraints; missing is not silently tradable |
| Security state | `is_st listed_days` | PIT eligibility fields |
| Shares/value | `total_share float_share free_share total_mv circ_mv` | share counts and market values in source-declared units |
| Valuation/yield | `pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm` | PIT valuation measures |
| Flow | `net_mf_amount big_net_amount` | money-flow measures under source schema |
| Industry | `industry_l1_code` | PIT industry classification |
| Fundamentals | `roe_waa_pit roa_pit netprofit_margin_pit netprofit_yoy_pit or_yoy_pit debt_to_assets_pit ocf_to_or_pit` | announcement-timed PIT accounting ratios |

## PIT timing

Financial observations become visible on the first open session strictly after the trusted publication
timestamp. Restatements replace an affected report only from their own effective session. Industry and
universe membership use their configured effective lag.

## Missing semantics

Missing accounting, industry, ST or tradability state is not zero. Each consumer must apply the governed
eligibility/missing policy. Research code must not backfill by report period or use later revisions before
their publication session.

## FeatureSnapshot participation

Raw fields participate only through the selected handler, AlphaPack and processor recipe recorded in the
FeatureSnapshot. Presence in a DataRelease does not imply that a field was used by a model.
