# QMT Read-only Broker Gateway

This service exposes a local GET-only HTTP boundary between `qlib-platform` and a logged-in MiniQMT client. It has no order submission, cancel, replacement, or POST API.

## Prerequisites

1. Use 64-bit Python 3.11 and install this project with `pip install -e '.[qmt-gateway,dev]'`.
2. Start MiniQMT under the same Windows user that will run the gateway.
3. Confirm the client has XtQuant query permission. The configured `QMT_USERDATA_PATH` must be MiniQMT's `userdata_mini` directory. When available, `up_queue_xtquant` is a useful readiness signal.
4. Install the compatible `xtquant` package supplied or approved by the broker. The gateway deliberately does not download or pin a public replacement.

Set these variables in the user environment; never put their values in source control:

```text
QMT_USERDATA_PATH
QMT_ACCOUNT_ID
QMT_ACCOUNT_TYPE=STOCK
QMT_SESSION_ID
QMT_GATEWAY_TOKEN
QMT_GATEWAY_STATE_DIR
BROKER_READONLY_TOKEN
```

`QMT_GATEWAY_TOKEN` and `BROKER_READONLY_TOKEN` must represent the same local Gateway credential. `QMT_ACCOUNT_ID` is the funding account, not a stock code or session ID.

## Start and validate

```powershell
tq-qmt-gateway serve
```

The service only listens on `127.0.0.1:8765`. It provides unauthenticated local readiness at `GET /v1/health`; the four account endpoints require `Authorization: Bearer <BROKER_READONLY_TOKEN>`.

Run the two NAV maintenance operations locally, never over HTTP:

```powershell
tq-qmt-gateway nav-capture --trade-date YYYY-MM-DD
tq-qmt-gateway nav-cash-flow --trade-date YYYY-MM-DD --amount <signed-cny> --reference <local-reference>
```

Capture the closing total asset after each trading day. Record every same-day external deposit as positive and withdrawal as negative before pretrade. The account endpoint fails closed until it finds a prior closing NAV.

Copy the `production.broker` block from `configs/qmt_gateway_broker.example.yaml` into an untracked local pipeline configuration after the four endpoint smoke test succeeds.

## Windows startup

Run the following once from the repository root while logged into the Windows account that starts MiniQMT:

```powershell
.\scripts\install_qmt_gateway_task.ps1
```

It creates an at-logon scheduled task, not a Session 0 Windows service, so it can access the same user-session MiniQMT instance. To remove it, run the script with `-Uninstall`.

## Smoke and shadow acceptance

1. First run the broker-provided QMT Python example inside the QMT client to validate the broker's configured environment.
2. Verify `GET /v1/health` returns `ready`, then verify account, positions, orders, and fills with the configured Bearer token.
3. Keep `production.broker.kind: inbox` until QMT snapshots match the client UI.
4. Run shadow/pretrade for 1–2 weeks. Require cash and total asset accuracy to CNY 0.01, exact position quantities, and reconcilable daily orders/fills before switching the local pipeline configuration.

The canonical endpoint details are in `src/tushare_qlib/qmt_gateway/openapi.yaml`.
