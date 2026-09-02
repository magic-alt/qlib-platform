# Changelog

All notable user-visible changes to `qlib-platform` are documented here.

The format follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/) where practical. Because this project includes research contracts and governed artifacts, a version bump may also be required when schemas, identity semantics, portfolio policy behavior, or operational contracts change even if the Python API remains source-compatible.

## [Unreleased]

### Added

- Apache License 2.0 project licensing.
- Repository CODEOWNERS and project Code of Conduct.
- Maintainer-facing release policy and generated-release-note categories.
- MkDocs Material documentation-site configuration and documentation build workflow.
- Project brand assets and architecture overview artwork.

### Changed

- Public repository metadata and package metadata are being aligned with the open-source project surface.

## [0.3.0] - 2026-09-02

`0.3.0` is the package version that existed when the public changelog discipline was introduced. Earlier commit-level history remains available in Git history; this changelog does not attempt to retroactively reconstruct release notes that were not recorded at release time.

### Project state at adoption

- Microsoft Qlib-backed A-share Research Plane / Alpha Factory.
- Immutable `DataRelease` and `DatasetVersion` lineage model.
- PIT-aware feature/research workflows and governed walk-forward evaluation.
- Research backtest, portfolio-policy, artifact-contract, feedback, and operations tooling.
- Artifact Contract v2 boundary to the optional `magic-alt/platform` Execution Plane.

[Unreleased]: https://github.com/magic-alt/qlib-platform/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/magic-alt/qlib-platform/releases/tag/v0.3.0
