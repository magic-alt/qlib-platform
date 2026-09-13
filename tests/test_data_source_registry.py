from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import qlib_platform.data.sources.registry as registry_module
from qlib_platform.data.ingestion import Extractor
from qlib_platform.data.sources import (
    DataSourceBinding,
    FetchResult,
    RetryPolicy,
    create_data_source,
    register_data_source,
)


class _FakeClient:
    def fetch(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> FetchResult:
        del api_name, fields, required, params
        return FetchResult(pd.DataFrame(), "empty", 1)

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


class _Settings:
    def __init__(self, root, kind: str):
        self.data = {"data_source": {"kind": kind}}
        self.paths = SimpleNamespace(raw=root / "raw")
        self.source_kind = kind


def test_custom_source_registration_does_not_require_ingestion_changes(tmp_path):
    kind = "unit_test_provider"
    client = _FakeClient()
    register_data_source(
        kind,
        lambda settings, retry: DataSourceBinding(name=kind, client=client),
        replace=True,
    )
    settings = _Settings(tmp_path, kind)

    binding = create_data_source(settings, RetryPolicy(max_attempts=1))
    extractor = Extractor(settings)

    assert binding.name == kind
    assert binding.client is client
    assert extractor.data_source.name == kind
    assert extractor.client is client
    assert extractor.source_is_mysql is False
    assert {endpoint.name for endpoint in extractor.endpoints} == {
        "daily",
        "adj_factor",
        "daily_basic",
        "moneyflow",
        "stk_limit",
        "suspend_d",
        "stock_st",
    }


def test_source_aliases_resolve_to_one_adapter(tmp_path):
    kind = "unit_alias_provider"
    alias = "unit-alias"
    client = _FakeClient()
    register_data_source(
        kind,
        lambda settings, retry: DataSourceBinding(name=kind, client=client),
        aliases=(alias,),
        replace=True,
    )

    binding = create_data_source(_Settings(tmp_path, alias), RetryPolicy())

    assert binding.name == kind
    assert binding.client is client


def test_registration_rolls_back_when_late_alias_conflicts():
    first = "unit_atomic_first"
    second = "unit_atomic_second"
    shared = "unit_atomic_shared"
    fresh = "unit_atomic_fresh"
    client = _FakeClient()

    register_data_source(
        first,
        lambda settings, retry: DataSourceBinding(name=first, client=client),
        aliases=(shared,),
        replace=True,
    )
    before_factories = dict(registry_module._FACTORIES)
    before_aliases = dict(registry_module._ALIASES)

    with pytest.raises(ValueError, match="alias already registered"):
        register_data_source(
            second,
            lambda settings, retry: DataSourceBinding(name=second, client=client),
            aliases=(fresh, shared),
        )

    assert registry_module._FACTORIES == before_factories
    assert registry_module._ALIASES == before_aliases
    assert second not in registry_module._FACTORIES
    assert fresh not in registry_module._ALIASES


def test_replace_refreshes_alias_set_without_leaving_stale_aliases(tmp_path):
    kind = "unit_replace_provider"
    old_alias = "unit_replace_old"
    new_alias = "unit_replace_new"
    client = _FakeClient()

    register_data_source(
        kind,
        lambda settings, retry: DataSourceBinding(name=kind, client=client),
        aliases=(old_alias,),
        replace=True,
    )
    register_data_source(
        kind,
        lambda settings, retry: DataSourceBinding(name=kind, client=client),
        aliases=(new_alias,),
        replace=True,
    )

    assert old_alias not in registry_module._ALIASES
    assert registry_module._ALIASES[new_alias] == kind
    assert create_data_source(_Settings(tmp_path, new_alias), RetryPolicy()).name == kind


def test_equivalent_legacy_aliases_collapse_after_normalization(tmp_path):
    kind = "unit_normalized_alias_provider"
    client = _FakeClient()

    register_data_source(
        kind,
        lambda settings, retry: DataSourceBinding(name=kind, client=client),
        aliases=("unit-normalized-alias", "unit_normalized_alias"),
        replace=True,
    )

    assert registry_module._ALIASES["unit_normalized_alias"] == kind
    assert create_data_source(
        _Settings(tmp_path, "unit-normalized-alias"), RetryPolicy()
    ).name == kind
