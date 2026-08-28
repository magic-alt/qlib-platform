---
status: ACTIVE
owner: operations
applies_to_commit: 8692afefe1f6cc82ab1f276fca788888f9f30f3e
last_verified: 2026-08-28
---

# Health and Observability

- `health live`: process responsiveness only.
- `health ready`: local readiness under the selected profile.
- `health dependencies`: data/model/optional adapter dependency state.
- `status`: resolved mode, release/dataset capability and local operational state.
- `ops-summary`: production-run summary in this Research Plane.

Do not classify platform unavailability as research-process liveness failure. Do classify identity,
checksum, local readiness and required dependency failure as fail-closed readiness conditions.
