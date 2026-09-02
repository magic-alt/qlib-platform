# Code of Conduct

`qlib-platform` is an engineering and quantitative-research project. Participation requires both professional conduct and research integrity.

This policy applies to issues, pull requests, reviews, discussions, project-managed chat or events, and other spaces where a participant is representing the project.

## Our commitment

We are committed to a project environment where people can participate without harassment, intimidation, discrimination, retaliation, or personal attacks.

Technical disagreement is expected. Evidence may be challenged strongly. People must still be treated with respect.

## Expected behavior

Participants should:

- focus criticism on code, methodology, evidence, assumptions, and trade-offs rather than people;
- make review feedback specific, actionable, and proportionate to the risk of the change;
- distinguish measured facts, assumptions, hypotheses, benchmarks, and opinions;
- correct material mistakes when evidence changes the conclusion;
- preserve privacy and confidentiality, including credentials, account identifiers, proprietary datasets, and embargoed security reports;
- disclose material conflicts of interest when they could affect technical or research judgment;
- represent exploratory research, backtests, certification, and production status accurately;
- respect sealed-holdout, lineage, licensing, security, and Research Plane / Execution Plane boundaries;
- keep project spaces focused on work relevant to the repository.

## Unacceptable behavior

Unacceptable conduct includes:

- harassment, threats, stalking, sustained unwanted attention, or deliberate intimidation;
- insults, slurs, degrading comments, identity-based discrimination, or sexualized conduct in project spaces;
- doxxing or publishing private information without permission;
- knowingly exposing secrets, tokens, broker/account identifiers, private keys, or embargoed vulnerability details;
- fabricating or manipulating benchmark results, research evidence, lineage records, review status, certification status, or release provenance;
- intentionally introducing look-ahead leakage or bypassing a sealed holdout while representing the result as governed research;
- pressuring another contributor to disable, weaken, or bypass safety, integrity, licensing, review, or security controls without transparent review;
- retaliation against anyone who raises a good-faith conduct, security, engineering-safety, or research-integrity concern;
- repeated disruption after a maintainer has asked for a behavior to stop.

## Technical and research disagreement

Strong criticism is appropriate when the work warrants it. Examples include:

- identifying look-ahead bias or PIT violations;
- rejecting an unsafe migration or unverifiable data release;
- disputing benchmark methodology or statistical interpretation;
- requesting stronger evidence for a performance claim;
- blocking a change that crosses the Research Plane / Execution Plane boundary;
- refusing to weaken a gate merely to make a result pass.

Reviewers should explain the invariant, failure mode, or evidence gap whenever practical. Contributors should address the substance of the review rather than treating technical rejection as a personal judgment.

No participant is entitled to have a contribution merged, a result accepted, or a model promoted.

## Reporting concerns

Do not escalate a conduct dispute by publishing sensitive personal information in the same public thread.

For ordinary conduct concerns, contact a project maintainer through an available private GitHub contact channel. If no suitable private project channel is available, use GitHub's platform reporting mechanisms and avoid putting sensitive details in a public issue.

For vulnerabilities, leaked credentials, integrity bypasses, or other security-sensitive matters, follow [`SECURITY.md`](SECURITY.md).

Good-faith reports should include enough context to evaluate the concern while minimizing unnecessary personal or confidential information.

## Enforcement

Maintainers are responsible for interpreting and enforcing this policy. Responses are based on severity, impact, intent, and prior behavior, and may include:

1. **Correction** — clarify the standard and request an edit or behavior change.
2. **Warning** — document that continued behavior will result in restrictions.
3. **Temporary restriction** — limit participation or interaction for a defined period.
4. **Content removal** — hide, edit, lock, or remove project-managed material when necessary.
5. **Permanent restriction** — remove access to project-managed spaces for severe or repeated violations.

Serious threats, doxxing, credential disclosure, targeted harassment, or deliberate security/research-integrity abuse may skip intermediate steps.

Maintainers should apply enforcement consistently, avoid conflicts of interest where practical, preserve reporter privacy, and record material decisions when doing so does not create additional risk.

## Maintainer conflicts

A maintainer who is directly involved in a conduct dispute should avoid being the sole decision-maker when another suitable maintainer or platform moderation path is available.

When the project has only one active maintainer, GitHub's platform-level reporting and moderation mechanisms remain available as an independent escalation path.

## Scope and enforcement boundaries

This Code of Conduct governs participation in project-managed spaces. It does not turn maintainers into arbiters of unrelated private disputes.

Security incidents, licensing questions, research-governance decisions, and code-review outcomes may have separate technical processes. A conduct report does not override those processes, and a technical rejection is not by itself a conduct violation.

## Attribution

This policy is informed by the [Contributor Covenant 3.0](https://www.contributor-covenant.org/version/3/0/) and adapted for the engineering, quantitative-research, and governance risks of `qlib-platform`.

Contributor Covenant is maintained by the Organization for Ethical Source and licensed under CC BY-SA 4.0.
