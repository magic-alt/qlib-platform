# Changelog

All notable user-visible changes to `qlib-platform` are documented here.

The format follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/) where practical. Because this project includes research contracts and governed artifacts, a version bump may also be required when schemas, identity semantics, portfolio policy behavior, or operational contracts change even if the Python API remains source-compatible.

## Unreleased

### Added

- FeatureSnapshot manifests now record a semantic raw-feature recipe identity plus cache-build provenance, including the exact source FeatureSnapshot/DatasetVersion for safe incremental reuse.
- LightGBM runtime evidence now reports the best-effort physical accelerator name in addition to the backend/index, using OpenCL enumeration when available and `nvidia-smi` as an NVIDIA fallback without changing device selection.
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

- Raw FeatureSnapshot caching now reuses exact immutable DatasetVersion coverage, safely extends a snapshot on the same DatasetVersion using the AlphaPack warm-up window, and may reuse across DatasetVersions only when the new manifest names exactly one direct `updated_from` parent and its sync delta proves the requested history is unchanged or tail-only. Historical symbol revisions, PIT changes, missing/ambiguous parent lineage, or insufficient warm-up evidence fail closed to full materialization. Requested loads also read only overlapping yearly partitions, and concurrent identical publishers are checksum-verified before reuse.
- Raw feature recipe fingerprints no longer depend on cache-orchestration or fitted-processor implementation files; they remain bound to the immutable DatasetVersion, universe, AlphaPack contract, feature-defining handler/fundamental implementation, loader contract, and Qlib revision.
- Standalone research now explicitly clears the inherited `cn_tushare_v1` experiment DataRelease binding; when a local DatasetVersion has no upstream DataRelease, canonical research identity falls back to its immutable manifest `version_id` instead of the profile-level `local` label, while explicit DataRelease mismatches remain fail-closed.
- Generated `tq-research` AlphaPack overlays now pin the already-resolved project root, registry, Qlib provider path, and DatasetVersion root before spawning child CLI processes, preventing nested quickstart output directories from rebasing inherited relative paths and losing `standalone-current`.
- Deep DatasetVersion proof reuse now performs the full inventory guard with bounded worker batches, resolves only unique partition parent directories, and uses one non-following stat per file instead of resolving every partition path; this keeps the same existence/size/mtime fail-closed checks while removing the Windows startup bottleneck on large providers.
- Deep DatasetVersion verification can now reuse manifest-bound prior deep evidence for immutable payloads: all partition existence/size/mtime guards remain fail-closed while only a deterministic content sample is rehashed; stale evidence automatically falls back to a fresh full deep pass.
- Automatic local-research source discovery and `doctor`/`prepare` now use bounded sampled verification by default instead of unconditional deep scans; `run`/`matrix` and explicit `--verify-mode deep` retain authoritative full verification before research execution.
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
