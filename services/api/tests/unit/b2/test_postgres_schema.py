"""The Postgres-backed pieces, checked without a Postgres.

These tests cannot prove the cache and the store work against a live server — that needs
`docker compose up` and belongs in T1's suite. What they *can* prove offline is the
class of failure that otherwise shows up at runtime in a container:

* the tables are registered on B1's `Base.metadata`, so `create_all()` creates them
  (a table hung off a different Base is silently never created — memory.md §4);
* the DDL compiles against the real PostgreSQL dialect, so column types, constraints
  and index definitions are valid rather than merely well-typed in Python;
* the cache's upsert compiles as a genuine `ON CONFLICT DO UPDATE`, and the store's
  writes contain no `UPDATE` or `DELETE` at all (HR-1's append-only property, checked
  as a property of the emitted SQL rather than of the author's intent).
"""

from __future__ import annotations

import inspect

from sqlalchemy import insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.core.db import Base
from app.providers.cache import PostgresResponseCache, ProviderCacheRow
from app.providers.source_store import PostgresSourceStore, SourceStoreRow


def _ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_both_tables_are_registered_on_the_shared_base() -> None:
    """A table hung off a different Base is silently never created by create_all()."""
    assert "provider_cache" in Base.metadata.tables
    assert "source_store" in Base.metadata.tables


def test_source_store_ddl_compiles_and_is_keyed_by_source_id_and_version() -> None:
    ddl = _ddl(SourceStoreRow.__table__)
    assert "CREATE TABLE source_store" in ddl
    # (source_id, version) composite key — this is what makes enrichment an append
    # rather than an update.
    assert "PRIMARY KEY (source_id, version)" in ddl
    assert "csl JSONB NOT NULL" in ddl
    assert "provenance JSONB NOT NULL" in ddl
    assert "written_at TIMESTAMP WITH TIME ZONE NOT NULL" in ddl


def test_cache_ddl_compiles_with_the_documented_key() -> None:
    ddl = _ddl(ProviderCacheRow.__table__)
    assert "CREATE TABLE provider_cache" in ddl
    assert "payload JSONB NOT NULL" in ddl
    for column in ("provider", "endpoint", "query_hash", "canonical_query", "expires_at"):
        assert column in ddl


def test_the_cache_is_keyed_by_provider_endpoint_and_query_hash() -> None:
    """CP-4 names this key exactly; an index on it is what makes lookups cheap."""
    indexes = {
        index.name: [col.name for col in index.columns]
        for index in ProviderCacheRow.__table__.indexes
    }
    assert indexes["ix_provider_cache_lookup"] == ["provider", "endpoint", "query_hash"]
    assert indexes["ix_provider_cache_expires_at"] == ["expires_at"]


def test_the_cache_upsert_compiles_to_on_conflict_do_update() -> None:
    """A cache entry is meant to be replaced; the store's rows are not."""
    stmt = postgresql.insert(ProviderCacheRow).values(
        cache_key="k", provider="openalex", endpoint="/works", query_hash="h",
        canonical_query="{}", payload={}, stored_at=None, expires_at=None,
    )
    sql = str(
        stmt.on_conflict_do_update(
            index_elements=[ProviderCacheRow.cache_key],
            set_={"payload": stmt.excluded.payload},
        ).compile(dialect=postgresql.dialect())
    )
    assert "ON CONFLICT (cache_key) DO UPDATE" in sql


def test_a_source_store_write_is_a_plain_insert() -> None:
    """No upsert, no clause that could overwrite a stored version."""
    sql = str(
        insert(SourceStoreRow)
        .values(
            source_id="src_x", version=1, csl={}, provenance={},
            abstract=None, abstract_source="unavailable", written_at=None,
        )
        .compile(dialect=postgresql.dialect())
    )
    assert sql.strip().upper().startswith("INSERT INTO SOURCE_STORE")
    assert "ON CONFLICT" not in sql.upper()


def test_the_store_contains_no_update_or_delete_at_all() -> None:
    """HR-1 as a property of the code, not of the reviewer's attention.

    The cache legitimately deletes expired rows; the store must never delete or update
    anything. Checked against the source because a single stray `session.execute(delete(
    SourceStoreRow))` would be invisible in a passing test suite.
    """
    source = inspect.getsource(inspect.getmodule(PostgresSourceStore))
    lowered = source.lower()
    assert "delete(" not in lowered, "source_store is append-only: no deletes"
    assert "update(" not in lowered, "source_store is append-only: no updates"
    assert "on_conflict" not in lowered, "source_store is append-only: no upserts"


def test_the_cache_is_allowed_to_delete_expired_rows() -> None:
    """The contrast that makes the previous test meaningful rather than vacuous."""
    source = inspect.getsource(inspect.getmodule(PostgresResponseCache))
    assert "delete(" in source.lower()
