# Multi-asset ResearchProfile extension guide

This guide defines the research-side extension contract for adding another security or market to
`qlib-platform`. It closes the design/compatibility portion of Issue #107; it does **not** certify every
asset named below.

## Current certified research scope

The implemented ResearchProfiles are:

- `ashare_equity_v1`: A-share common-stock research;
- `ashare_etf_v1`: A-share ETF research using the dedicated ETF data/universe contract and
  `etf_core_v1` feature pack.

Both are `research_only`. Neither profile grants LEAN validation, Paper, broker routing, or Live
execution authority.

The cross-market contract in `research/contracts/cross_market.py` is certification infrastructure. Its
CN/US fixtures prove timezone, DST, holiday, `available_at`, and FX behavior, but **do not** declare a
production US or Hong Kong ResearchProfile. A real new market still needs the complete checklist below.

## Frozen A-share compatibility anchor

The pre-ResearchProfile baseline is commit
`9efc465c3556a642bba68368f7cc9cfc2d69a34b`. The tracked compatibility fixture
`tests/fixtures/research_profile/ashare_equity_v1_pre_profile_golden.json` freezes:

- the historical mini-Qlib A-share fixture source blob;
- the train/select implementation blob used by that fixture;
- the `AlphaPackSpec` implementation blob;
- all seven pre-profile A-share AlphaPack fingerprints;
- the default A-share research semantics; and
- a canonical feature/label/date/split fixture output SHA-256.

PRs that intentionally change one of those identities must introduce a new version and explain the
migration. Adding a new profile must not refresh the old golden simply to make CI pass.

## Instrument identity

Every profile must use a stable provider-neutral `instrument_id`. Provider, storage, and broker symbols
are aliases with their own validity interval; aliases are not the permanent identity.

At minimum, an `InstrumentSpec` must bind:

- asset class and subtype;
- market and venue;
- quote/valuation currency;
- calendar ID;
- vendor aliases; and
- whether the object is actually tradable.

Missing required contract attributes fail closed at governed handoff. Do not use `1`, `0`, CNY, or any
other convenient default for an unknown multiplier, currency, expiry, or underlying.

## Calendar and information-time rules

A calendar is versioned and has an IANA timezone. Cross-market research must compare absolute,
timezone-aware timestamps rather than joining rows because their local session dates happen to match.

For session observations:

1. bind the observation to its market and `calendar_id`;
2. retain explicit open/closed session evidence;
3. derive the session close using the calendar timezone;
4. require `available_at` to be no earlier than the certified session close for close-derived data;
5. include an observation only when `available_at <= as_of`.

The CI fixture deliberately covers New York's March DST transition and independent CN/US holiday
states. This means a Shanghai close can be available while the same-date New York close is still future
information.

Corporate-action, filing, index-membership, NAV, and other event data need their own factual
`available_at`; a market close timestamp is not a substitute for an event publication timestamp.

## Currency and FX

A cross-currency valuation must have an explicit as-of FX contract. The current certification helper is
intentionally conservative:

- the requested direct pair must exist;
- the rate must be finite and positive;
- future quotes are unavailable;
- stale quotes are rejected using an explicit maximum age;
- conflicting quotes at the same latest timestamp are rejected;
- no inverse, triangular, or silent last-known conversion is synthesized.

A production profile may later define a richer FX graph, but that behavior must be versioned and tested.
Unknown FX is not equivalent to `1.0`.

## AlphaPack and model preflight

Every AlphaPack declares its supported ResearchProfiles, asset classes/subtypes, label IDs, required
fields/data components, PIT requirement, lookback, and missing-data policy. Incompatibility is rejected
by `assert_alpha_pack_compatible()` before Qlib initialization/training.

Model profiles are likewise resolved and validated before training. Asset-specific model restrictions,
if introduced later, belong in the same plan/preflight phase; they must not be discovered after fitting.

Do not make a stock AlphaPack appear ETF/futures-compatible by filling unavailable stock fundamentals
with zeros. Add an applicable pack or mark the combination unsupported.

## Adding another cash-equity or ETF market

A US or Hong Kong equity/ETF profile is complete only after it has all of the following:

1. stable instrument IDs and provider aliases;
2. a versioned local exchange calendar and timezone;
3. listing/delisting and survivorship-safe universe evidence;
4. price/volume/adjustment and corporate-action semantics;
5. base/quote currency plus as-of FX evidence where needed;
6. a benchmark contract;
7. label timing and `available_at` rules;
8. one or more explicitly applicable AlphaPacks;
9. deterministic train/backtest/diagnostic/report fixtures;
10. negative tests for missing calendar, future information, unknown FX, and incompatible packs;
11. proof that existing A-share/ETF identities and frozen outputs remain unchanged.

Only after that evidence is merged should the profile be registered as `implemented=True`.

## Futures

A continuous futures series is a **research series**, not an orderable contract. Represent it with
`continuous_series=True` and keep it non-tradable. `assert_governed_handoff_ready()` rejects it.

A tradable future requires a distinct contract identity and, at minimum, currency, underlying, expiry,
and multiplier. A future production ResearchProfile also needs versioned rollover/settlement rules and a
separate LEAN validation path. Continuous research data must never be exported as if it were the actual
contract to order.

## Options

Options remain unsupported by the current production ResearchProfile registry. A future version must
add explicit chain identity and option-specific fields such as expiry, strike, call/put right, underlying,
multiplier, exercise/settlement style, and `available_at` before it can be marked implemented.

If Qlib is not the appropriate engine for an event-driven derivative task, the research capability must
remain explicitly unsupported and be validated in the LEAN-side workflow instead of creating a second
execution engine here.

## Promotion and execution boundary

`qlib-platform` remains the Research / Alpha Factory. New profiles inherit **no** execution
certification from A-share. Research artifacts can progress only through the research-owned promotion
scope allowed by the repository contract; LEAN/Paper/Live authority remains external.

Adding a profile must not:

- access the sealed final holdout merely to certify infrastructure;
- grant broker/order APIs;
- reinterpret execution rules as Qlib-owned state;
- promote a research fixture because another asset was already certified; or
- turn CI success into Paper/Live authorization.

## Minimum PR checklist

Before setting a new profile to implemented, the PR should show:

- schema/identity and backward-compatibility evidence;
- provider-neutral data and universe contracts;
- calendar/timezone/DST/holiday tests where relevant;
- PIT/`available_at` negative tests;
- FX and contract-attribute fail-closed tests where relevant;
- AlphaPack/model/label preflight failures for incompatible combinations;
- an executable offline research fixture through report generation;
- unchanged prior-profile golden identities; and
- full repository CI, Qlib capability, security/dependency, and platform contract gates green.
