"""Governed research workflows and diagnostics.

The package initializer intentionally has no eager re-exports: research
modules depend on lineage, while lineage is also used by research contracts.
Callers should import the concrete research submodule they use.
"""
