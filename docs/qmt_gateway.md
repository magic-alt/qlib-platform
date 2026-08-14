# QMT gateway moved to platform

The QMT read-only gateway is owned and operated by the sibling `platform`
repository. Its canonical implementation and runbook are now:

```text
platform/web/backend/app/broker/qmt_gateway
platform/docs/qmt_gateway.md
```

The source under `src/tushare_qlib/qmt_gateway` is retained temporarily for P3
rollback and contract comparison only. It is excluded from package discovery,
has no console entrypoint, and must not be started or extended here.

Qlib publishes `TARGET_PORTFOLIO`; platform owns QMT observations, hard risk,
orders, fills, reconciliation and ledger state.
