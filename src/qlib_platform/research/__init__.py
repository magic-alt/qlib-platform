"""Governed quantitative-research workflows.

Canonical implementations are grouped by responsibility (contracts, evidence,
features, hypotheses, workflow, evaluation, diagnostics, portfolio, reporting).
The legacy ``phaseN_*`` modules are compatibility shims only; new production
logic must live in the responsibility-oriented packages.

The package initializer intentionally has no eager re-exports because research
modules participate in lineage and artifact-contract dependency graphs.
"""
