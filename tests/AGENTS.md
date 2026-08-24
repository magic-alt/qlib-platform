# Test rules

Tests must be deterministic and must not make live TuShare or broker/QMT calls. Use the repository-local interpreter only.

For a behavior change, test the governing invariant as well as the happy path. Add or update failure-injection coverage when fail-closed validation, artifact identity, lineage, PIT timing, fold/OOS semantics, checkpoint/resume, execution boundary, or holdout isolation changes.

Do not weaken assertions, update golden hashes, or relax coverage merely to accept a changed result. Demonstrate why an intentional contract/version change is correct and update the governing documentation/configuration and dependent tests together.
