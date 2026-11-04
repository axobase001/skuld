"""SQLite canonical store for the Personal AI cognitive state.

The store is deliberately synchronous and dependency-free.  It uses WAL,
foreign keys, nested transactions, immutable evidence/event tables, user-scoped
indexes, and JSON payloads that round-trip the public dataclass schemas.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, Self, TypeVar




class StoreInvariantError(ValueError):
    """Raised when a write would break provenance or user isolation."""


# V4.0 schema (S1): the stored SQL of every object live code uses, in creation order.
_SCHEMA_DDL: tuple[str, ...] = (
    """CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
    """CREATE TABLE subject_graphs_v200 (
                graph_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                authority_kind TEXT NOT NULL
                    CHECK(authority_kind='PersistentSubjectHypergraph'),
                created_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(user_id,graph_id)
            )""",
    """CREATE TABLE source_events_v200 (
                source_event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                received_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(user_id,source_event_id),
                FOREIGN KEY(user_id,graph_id)
                    REFERENCES subject_graphs_v200(user_id,graph_id)
                    ON DELETE RESTRICT
            )""",
    """CREATE INDEX idx_source_events_user_received_v200
                ON source_events_v200(user_id,received_at,source_event_id)""",
    """CREATE INDEX idx_source_events_user_ref_v200
                ON source_events_v200(user_id,source_type,source_ref)""",
    """CREATE INDEX idx_source_events_user_hash_v200
                ON source_events_v200(user_id,content_hash)""",
    """CREATE TABLE subject_nodes_v200 (
                node_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                semantic_identity_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                epistemic_state TEXT NOT NULL CHECK(epistemic_state IN (
                    'DECLARED','OBSERVED','SUPPORTED','CONTESTED','SUPERSEDED',
                    'WITHDRAWN','REJECTED'
                )),
                lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN (
                    'ACTIVE','INACTIVE','TOMBSTONED'
                )),
                modal_state TEXT NOT NULL CHECK(modal_state IN (
                    'ACTUAL','INTENDED','HYPOTHETICAL','COUNTERFACTUAL',
                    'PREDICTED','QUOTED','REPORTED','CONDITIONAL'
                )),
                temporal_state TEXT NOT NULL CHECK(temporal_state IN (
                    'CURRENT','FUTURE','PAST','EXPIRED','UNRESOLVED_TIME'
                )),
                resolution_state TEXT NOT NULL CHECK(resolution_state IN (
                    'PENDING','RESOLVED','INVALIDATED'
                )),
                authority_class TEXT NOT NULL CHECK(authority_class IN (
                    'USER_FIRST_PERSON','USER_REPORTED','EXTERNAL_PRIMARY',
                    'EXTERNAL_SECONDARY','TOOL_OBSERVED','SYSTEM_DERIVED',
                    'ASSISTANT_GENERATED','SYSTEM_INFERRED','HISTORICAL_REPORTED'
                )),
                valid_from TEXT,
                valid_until TEXT,
                superseded_by TEXT,
                migration_origin_json TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(user_id,node_id),
                UNIQUE(user_id,semantic_identity_key,node_id),
                FOREIGN KEY(user_id,graph_id)
                    REFERENCES subject_graphs_v200(user_id,graph_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,source_event_id)
                    REFERENCES source_events_v200(user_id,source_event_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,superseded_by)
                    REFERENCES subject_nodes_v200(user_id,node_id)
                    ON DELETE RESTRICT
            )""",
    """CREATE INDEX idx_subject_nodes_user_resolution_v200
                ON subject_nodes_v200(user_id,resolution_state,created_at,node_id)""",
    """CREATE INDEX idx_subject_nodes_user_semantic_v200
                ON subject_nodes_v200(user_id,semantic_identity_key,node_id)""",
    """CREATE INDEX idx_subject_nodes_user_source_v200
                ON subject_nodes_v200(user_id,source_event_id,node_id)""",
    """CREATE INDEX idx_subject_nodes_user_type_v200
                ON subject_nodes_v200(user_id,node_type,lifecycle_state,node_id)""",
    """CREATE INDEX idx_subject_nodes_user_type_latest_v200
                ON subject_nodes_v200(user_id,node_type,created_at DESC,node_id DESC)""",
    """CREATE TABLE subject_edges_v200 (
                edge_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                valid_from TEXT,
                valid_until TEXT,
                superseded_by TEXT,
                semantic_hash TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(user_id,edge_id),
                FOREIGN KEY(user_id,graph_id)
                    REFERENCES subject_graphs_v200(user_id,graph_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,source_node_id)
                    REFERENCES subject_nodes_v200(user_id,node_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,target_node_id)
                    REFERENCES subject_nodes_v200(user_id,node_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,source_event_id)
                    REFERENCES source_events_v200(user_id,source_event_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,superseded_by)
                    REFERENCES subject_edges_v200(user_id,edge_id)
                    ON DELETE RESTRICT
            )""",
    """CREATE INDEX idx_subject_edges_user_relation_v200
                ON subject_edges_v200(user_id,relation_type,status,edge_id)""",
    """CREATE INDEX idx_subject_edges_user_source_v200
                ON subject_edges_v200(user_id,source_node_id,relation_type,edge_id)""",
    """CREATE INDEX idx_subject_edges_user_target_v200
                ON subject_edges_v200(user_id,target_node_id,relation_type,edge_id)""",
    """CREATE INDEX idx_subject_edges_user_source_active_v200
                ON subject_edges_v200(
                    user_id,source_node_id,status,created_at,relation_type,edge_id
                )""",
    """CREATE INDEX idx_subject_edges_user_target_active_v200
                ON subject_edges_v200(
                    user_id,target_node_id,status,created_at,relation_type,edge_id
                )""",
    """CREATE INDEX idx_subject_edges_user_time_v200
                ON subject_edges_v200(user_id,valid_from,valid_until,edge_id)""",
    """CREATE TABLE subject_hyperedges_v200 (
                hyperedge_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                participant_count INTEGER NOT NULL CHECK(participant_count >= 2),
                created_at TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                valid_from TEXT,
                valid_until TEXT,
                superseded_by TEXT,
                semantic_hash TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                data_json TEXT NOT NULL,
                UNIQUE(user_id,hyperedge_id),
                FOREIGN KEY(user_id,graph_id)
                    REFERENCES subject_graphs_v200(user_id,graph_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,source_event_id)
                    REFERENCES source_events_v200(user_id,source_event_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,superseded_by)
                    REFERENCES subject_hyperedges_v200(user_id,hyperedge_id)
                    ON DELETE RESTRICT
            )""",
    """CREATE INDEX idx_subject_hyperedges_user_relation_v200
                ON subject_hyperedges_v200(user_id,relation_type,status,hyperedge_id)""",
    """CREATE INDEX idx_subject_hyperedges_user_source_v200
                ON subject_hyperedges_v200(user_id,source_event_id,hyperedge_id)""",
    """CREATE TABLE subject_hyperedge_participants_v200 (
                user_id TEXT NOT NULL,
                hyperedge_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                role TEXT NOT NULL,
                node_id TEXT NOT NULL,
                PRIMARY KEY(user_id,hyperedge_id,ordinal),
                UNIQUE(user_id,hyperedge_id,role,node_id),
                FOREIGN KEY(user_id,hyperedge_id)
                    REFERENCES subject_hyperedges_v200(user_id,hyperedge_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY(user_id,node_id)
                    REFERENCES subject_nodes_v200(user_id,node_id)
                    ON DELETE RESTRICT
            )""",
    """CREATE INDEX idx_subject_hyperedge_participant_user_node_v200
                ON subject_hyperedge_participants_v200(user_id,node_id,hyperedge_id)""",
    """CREATE TRIGGER subject_graphs_v200_no_update
            BEFORE UPDATE ON subject_graphs_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject graphs are append-only');
            END""",
    """CREATE TRIGGER subject_graphs_v200_no_delete
            BEFORE DELETE ON subject_graphs_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject graphs are append-only');
            END""",
    """CREATE TRIGGER source_events_v200_no_update
            BEFORE UPDATE ON source_events_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 source events are append-only');
            END""",
    """CREATE TRIGGER source_events_v200_no_delete
            BEFORE DELETE ON source_events_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 source events are append-only');
            END""",
    """CREATE TRIGGER subject_nodes_v200_no_update
            BEFORE UPDATE ON subject_nodes_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject nodes are append-only');
            END""",
    """CREATE TRIGGER subject_nodes_v200_no_delete
            BEFORE DELETE ON subject_nodes_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject nodes are append-only');
            END""",
    """CREATE TRIGGER subject_edges_v200_no_update
            BEFORE UPDATE ON subject_edges_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject edges are append-only');
            END""",
    """CREATE TRIGGER subject_edges_v200_no_delete
            BEFORE DELETE ON subject_edges_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject edges are append-only');
            END""",
    """CREATE TRIGGER subject_hyperedges_v200_no_update
            BEFORE UPDATE ON subject_hyperedges_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject hyperedges are append-only');
            END""",
    """CREATE TRIGGER subject_hyperedges_v200_no_delete
            BEFORE DELETE ON subject_hyperedges_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 subject hyperedges are append-only');
            END""",
    """CREATE TRIGGER subject_hyperedge_participants_v200_no_update
            BEFORE UPDATE ON subject_hyperedge_participants_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 hyperedge participants are append-only');
            END""",
    """CREATE TRIGGER subject_hyperedge_participants_v200_no_delete
            BEFORE DELETE ON subject_hyperedge_participants_v200 BEGIN
                SELECT RAISE(ABORT, 'V2.0 hyperedge participants are append-only');
            END""",
    # S1 retrieval index (skuld.kernel.record_index): one token document per record node.
    """CREATE VIRTUAL TABLE record_index USING fts5(tokens, node_id UNINDEXED, user_id UNINDEXED)""",
)


class PersonalAIStore:
    SCHEMA_VERSION = 51

    def __init__(self, db_path: str | os.PathLike[str] = "data/personal_ai.db") -> None:
        self.db_path = str(db_path)
        self.store_session_id = secrets.token_hex(16)
        self.is_persistent = self.db_path != ":memory:"
        self.database_identity = (
            hashlib.sha256(
                ("sqlite:" + str(Path(self.db_path).resolve())).encode("utf-8")
            ).hexdigest()
            if self.is_persistent
            else f"memory:{self.store_session_id}"
        )
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            # Owner-only from the first byte: SQLite gives the -wal/-shm files the database file's mode.
            os.close(os.open(self.db_path, os.O_CREAT | os.O_RDWR, 0o600))
        self._lock = threading.RLock()
        # Transaction nesting belongs to the current execution context, not to
        # the OS thread.  asyncio tasks share threads and must never inherit one
        # another's savepoint depth.
        self._tx_depth: ContextVar[int] = ContextVar(
            f"personal_ai_tx_depth_{id(self)}", default=0
        )
        self._conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._source_schema_version = int(
            self._conn.execute("PRAGMA user_version").fetchone()[0]
        )
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        """Create the V4.0 schema on an empty database; refuse any other version.

        V4.0 starts from an empty store (scope §6, no backward compatibility), so there is no
        migration chain: _SCHEMA_DDL holds only what live code reads or writes. Runtime creates
        its own runtime_* tables."""
        if self._source_schema_version == self.SCHEMA_VERSION:
            return
        if self._source_schema_version != 0 or self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone():
            raise StoreInvariantError(
                f"database schema v{self._source_schema_version} is not the V4.0 schema "
                f"v{self.SCHEMA_VERSION}; V4.0 starts from an empty store"
            )
        with self.transaction():
            for statement in _SCHEMA_DDL:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            self._conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[PersonalAIStore]:
        """BEGIN IMMEDIATE transaction with nested savepoint support."""
        with self._lock:
            depth = self._tx_depth.get()
            savepoint = f"personal_ai_sp_{depth}"
            if depth == 0:
                self._conn.execute("BEGIN IMMEDIATE")
            else:
                self._conn.execute(f"SAVEPOINT {savepoint}")
            token = self._tx_depth.set(depth + 1)
            try:
                yield self
            except Exception:
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                if depth == 0:
                    self._conn.execute("COMMIT")
                else:
                    self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            finally:
                self._tx_depth.reset(token)

    def database_size_bytes(self) -> int:
        if self.db_path == ":memory:":
            with self._lock:
                page_count = self._conn.execute("PRAGMA page_count").fetchone()[0]
                page_size = self._conn.execute("PRAGMA page_size").fetchone()[0]
            return int(page_count * page_size)
        path = Path(self.db_path)
        return path.stat().st_size if path.exists() else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = ["PersonalAIStore", "StoreInvariantError"]
