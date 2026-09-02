# Security Policy

Security issues in `qlib-platform` can affect research integrity, artifact lineage, local credentials or the boundary between research and execution. Please report them responsibly.

## Supported code

Security fixes are made against the current `main` branch. The project does not currently maintain separate long-term security branches.

Frozen certification or historical documents describe the code baseline to which they apply; they do not imply that old commits receive ongoing security support.

## Reporting a vulnerability

Prefer GitHub's private vulnerability reporting / Security Advisory channel for this repository when it is available.

If private reporting is not available, open a minimal public issue that contains **no exploit details, secrets, credentials, account identifiers or sensitive artifacts** and ask the maintainer to establish a private reporting channel.

Please include, privately where appropriate:

- affected component and commit/version;
- a concise description of the impact;
- reproduction conditions or proof of concept;
- whether exploitation requires local access, crafted data/artifacts or external connectivity;
- whether secrets, research integrity or the execution boundary could be affected;
- suggested mitigation if you have one.

Do not include live TuShare tokens, broker/QMT credentials, private keys, account identifiers, `.env` contents or proprietary datasets in a report.

## Security-sensitive issue classes

Examples include:

- credential or token exposure;
- arbitrary code execution or unsafe deserialization;
- path traversal or unintended filesystem access;
- artifact signature/hash/identity verification bypasses;
- forged or ambiguous parent/lineage references;
- authorization bypasses around governed holdout, publishing or promotion operations;
- unsafe command construction or injection through configuration/data inputs;
- durable outbox/recovery behavior that can replay or mutate state incorrectly;
- vulnerabilities that could collapse the intended Research Plane / Execution Plane boundary;
- accidental inclusion of broker state or live credentials in research artifacts/logs.

## Research correctness vs security

Not every research-quality defect is a security vulnerability. Model underperformance, weak IC, ordinary backtest disagreement or a questionable alpha hypothesis should normally be filed as a bug/research issue.

However, treat the problem as security-sensitive when an attacker or malformed input can intentionally bypass integrity checks, falsify lineage/evidence, expose secrets or cross a safety boundary.

## Disclosure expectations

Please allow maintainers a reasonable opportunity to investigate and prepare a fix before publishing exploit details. After remediation, a public issue or advisory can document the impact, affected versions and mitigation without exposing secrets.

## Operational safety

Even when testing a suspected vulnerability:

- use synthetic or disposable data where possible;
- do not use real broker credentials or accounts;
- do not trigger live execution through the sibling execution platform;
- do not mutate certified or governed evidence merely to demonstrate impact;
- preserve logs and hashes needed to reproduce the issue safely.

For general contribution guidance, see [CONTRIBUTING.md](CONTRIBUTING.md).
