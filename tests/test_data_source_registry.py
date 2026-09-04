from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd

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
