# Changelog

All notable user-visible changes to `qlib-platform` are documented here.

The format follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/) where practical. Because this project includes research contracts and governed artifacts, a version bump may also be required when schemas, identity semantics, portfolio policy behavior, or operational contracts change even if the Python API remains source-compatible.

## Unreleased

### Added

- Apache License 2.0 project licensing and package metadata.
- Repository CODEOWNERS and a research-integrity-aware Code of Conduct.
- Maintainer-facing release policy and generated-release-note categories.
- MkDocs Material documentation-site configuration, strict documentation CI, and version-controlled project brand/architecture assets.
- Dependabot policy for Python and GitHub Actions, with governed Qlib/LightGBM upgrades kept manual.
- CodeQL Python security analysis and pull-request Dependency Review.
- Tagged GitHub Release automation with wheel/source build, clean-environment smoke test, CycloneDX SBOM, SHA-256 manifest, build-provenance attestation, and SBOM attestation.
- Public project roadmap, milestone model, repository-governance guide, Good First Issue policy, and contributor-task issue form.
- `tq-research` local-research quickstart orchestration for data diagnosis/preparation, AlphaPack/model experiments, fixed OOS studies, walk-forward research, and prediction-only portfolio backtests.
- `tq-research-summary` experiment comparison output for IC, RankIC, ICIR, RankICIR, ExcessIR, drawdown, cost, and turnover when the underlying Qlib report exposes it.
- Cross-platform local qrun launchers plus a portable PyTorch `auto` model profile that resolves CUDA, Apple MPS, or CPU through the existing ModelAdapter runtime.

### Changed

- Automatic local-research source discovery now uses bounded sampled verification for active DataRelease and DatasetVersion probes instead of unconditional deep scans; command-level deep verification remains authoritative before research execution.
- README branding no longer repeats the project title beneath the wordmark.
- README no longer duplicates the CLI command catalog; command syntax and side-effect classification remain in the dedicated CLI Reference.
- `CONTRIBUTING.md` now provides first-contributor routing, risk-based change classification, review expectations, research-integrity rules, and dependency-bot policy.
- `CODE_OF_CONDUCT.md` now more clearly separates technical disagreement, research-integrity violations, conduct reporting, maintainer conflicts, and enforcement boundaries.
- Documentation navigation now exposes project roadmap, contributor onboarding, repository/supply-chain governance, and the supported local research quickstart as first-class sections.
- Public repository metadata and package metadata are being aligned with the open-source project surface.
- The local Qlib backtest example now documents Windows, macOS, and Linux launch paths while preserving DatasetVersion verification and qrun contract checks.

## 0.3.0 baseline — 2026-09-02

`0.3.0` is the package version that existed when the public changelog discipline was introduced. It is recorded here as a **baseline**, not as a claim that a `v0.3.0` Git tag or GitHub Release already exists. Earlier commit-level history remains available in Git history; this changelog does not attempt to retroactively reconstruct release notes that were not recorded at release time.

### Project state at adoption

- Microsoft Qlib-backed A-share Research Plane / Alpha Factory.
- Immutable `DataRelease` and `DatasetVersion` lineage model.
- PIT-aware feature/research workflows and governed walk-forward evaluation.
- Research backtest, portfolio-policy, artifact-contract, feedback, and operations tooling.
- Artifact Contract v2 boundary to the optional `magic-alt/platform` Execution Plane.
