"""Provider-neutral ingestion, market-data contracts, and storage primitives.

Import concrete services from their domain modules. Keeping package import
lightweight prevents unrelated storage/symbol imports from initializing the
ingestion provider stack.
"""
