# QMT gateway moved to platform

The QMT read-only gateway is owned and operated by the sibling `platform`
repository. Its canonical implementation and runbook are now:

```text
platform/web/backend/app/broker/qmt_gateway
platform/docs/qmt_gateway.md
```

P3 physically removed `src/tushare_qlib/qmt_gateway` and its installer/configuration
surface. Gateway changes and operational commands must be made in `platform`.

Qlib publishes `TARGET_PORTFOLIO`; platform owns QMT observations, hard risk,
orders, fills, reconciliation and ledger state.
