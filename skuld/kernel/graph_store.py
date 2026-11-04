"""Single-store facade for the V2.0.0 Persistent Subject Hypergraph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from skuld.kernel.schemas import (
    SourceEvent,
    SubjectEdge,
    SubjectHyperedge,
    SubjectHyperedgeParticipant,
    SubjectNode,
    canonical_hash,
)
from skuld.kernel.personal_ai_store import PersonalAIStore
from skuld.kernel import record_index
from skuld.kernel.schemas import AuthorityClass

if TYPE_CHECKING:
    from skuld.kernel.personal_ai_store import PersonalAIStore


class SubjectGraphInvariantError(ValueError):
    """Raised before a write could create a second authority or broken lineage."""


class PersistentSubjectHypergraphStore:
    """Operate the authoritative graph inside an existing PersonalAIStore.

    The constructor deliberately accepts no path or SQLite connection. This
    prevents Pending, Current World, Prediction or Action code from opening a
    second semantic database while pretending it is only a graph region.
    """

    SCHEMA_VERSION = "skuld-v200-subject-hypergraph/v1"

    def __init__(self, personal_store: PersonalAIStore) -> None:

        if not isinstance(personal_store, PersonalAIStore):
            raise TypeError(
                "PersistentSubjectHypergraphStore requires an existing "
                "PersonalAIStore; independent graph databases are forbidden"
            )
        if personal_store.SCHEMA_VERSION < 46:
            raise SubjectGraphInvariantError("PersonalAIStore schema 46 is required")
        self._personal_store = personal_store
        self._conn = personal_store._conn
        self._graph_ids: dict[str, str] = {}

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _graph_id(user_id: str) -> str:
        digest = hashlib.sha256(
            f"skuld-v200-persistent-subject:{user_id}".encode()
        ).hexdigest()
        return f"subject_graph_{digest[:32]}"

    def ensure_graph(self, user_id: str) -> str:
        if not user_id.strip():
            raise SubjectGraphInvariantError("user_id is required")
        graph_id = self._graph_id(user_id)
        if self._graph_ids.get(user_id) == graph_id:
            return graph_id
        payload = {
            "graph_id": graph_id,
            "user_id": user_id,
            "authority_kind": "PersistentSubjectHypergraph",
            "schema_version": self.SCHEMA_VERSION,
        }
        with self._personal_store.transaction():
            existing = self._conn.execute(
                "SELECT graph_id,authority_kind,schema_version "
                "FROM subject_graphs_v200 WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["graph_id"] != graph_id
                    or existing["authority_kind"] != "PersistentSubjectHypergraph"
                    or existing["schema_version"] != self.SCHEMA_VERSION
                ):
                    raise SubjectGraphInvariantError(
                        "user already has a conflicting authoritative graph"
                    )
                self._graph_ids[user_id] = graph_id
                return str(existing["graph_id"])
            self._conn.execute(
                """INSERT INTO subject_graphs_v200(
                    graph_id,user_id,authority_kind,created_at,
                    schema_version,data_json
                ) VALUES(?,?,?,?,?,?)""",
                (
                    graph_id,
                    user_id,
                    "PersistentSubjectHypergraph",
                    "1970-01-01T00:00:00+00:00",
                    self.SCHEMA_VERSION,
                    self._dump(payload),
                ),
            )
        return graph_id

    def append_source_event(self, event: SourceEvent) -> SourceEvent:
        graph_id = self.ensure_graph(event.user_id)
        payload = event.to_dict()
        encoded = self._dump(payload)
        with self._personal_store.transaction():
            existing = self._conn.execute(
                "SELECT user_id,data_json FROM source_events_v200 "
                "WHERE source_event_id=?",
                (event.source_event_id,),
            ).fetchone()
            if existing is not None:
                if existing["user_id"] != event.user_id or existing["data_json"] != encoded:
                    raise SubjectGraphInvariantError("source event identity collision")
                return SourceEvent(**json.loads(existing["data_json"]))
            self._conn.execute(
                """INSERT INTO source_events_v200(
                    source_event_id,user_id,graph_id,source_type,source_ref,
                    content_hash,received_at,schema_version,data_json
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    event.source_event_id,
                    event.user_id,
                    graph_id,
                    event.source_type,
                    event.source_ref,
                    event.content_hash,
                    event.received_at,
                    event.schema_version,
                    encoded,
                ),
            )
        return event

    def get_source_event(self, user_id: str, source_event_id: str) -> SourceEvent | None:
        """Return one immutable tenant-scoped source event."""

        with self._personal_store._lock:
            row = self._conn.execute(
                "SELECT data_json FROM source_events_v200 "
                "WHERE user_id=? AND source_event_id=?",
                (user_id, source_event_id),
            ).fetchone()
        return SourceEvent(**json.loads(row["data_json"])) if row else None

    def list_source_events(
        self,
        user_id: str,
        *,
        source_type: str | None = None,
        source_ref: str | None = None,
        limit: int | None = None,
    ) -> list[SourceEvent]:
        clauses = ["user_id=?"]
        parameters: list[str | int] = [user_id]
        if source_type is not None:
            clauses.append("source_type=?")
            parameters.append(source_type)
        if source_ref is not None:
            clauses.append("source_ref=?")
            parameters.append(source_ref)
        suffix = " ORDER BY received_at,source_event_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("source event limit must be positive")
            suffix += " LIMIT ?"
            parameters.append(limit)
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM source_events_v200 WHERE "
                + " AND ".join(clauses)
                + suffix,
                parameters,
            ).fetchall()
        return [SourceEvent(**json.loads(row["data_json"])) for row in rows]

    def _require_source_event(self, user_id: str, source_event_id: str) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM source_events_v200 WHERE user_id=? AND source_event_id=?",
            (user_id, source_event_id),
        ).fetchone()
        if row is None:
            raise SubjectGraphInvariantError(
                "active graph object requires a same-user source event"
            )

    def _node_row(self, user_id: str, node_id: str):
        return self._conn.execute(
            "SELECT semantic_identity_key,data_json FROM subject_nodes_v200 "
            "WHERE user_id=? AND node_id=?",
            (user_id, node_id),
        ).fetchone()

    def _require_inference_contract(self, node: SubjectNode) -> None:
        """D3: an inferred belief exists only together with the Prediction that will settle it."""
        row = self._node_row(node.user_id, str(node.content.get("prediction_id") or ""))
        data = json.loads(row["data_json"]) if row else None
        if not data or data["node_type"] != "Prediction" or data["content"].get("inference_key") != node.semantic_identity_key:
            raise SubjectGraphInvariantError("an inferred belief needs its prediction contract")

    def append_node(self, node: SubjectNode) -> SubjectNode:
        graph_id = self.ensure_graph(node.user_id)
        encoded = self._dump(node.to_dict())
        with self._personal_store.transaction():
            self._require_source_event(node.user_id, node.source_event_id)
            existing_any = self._conn.execute(
                "SELECT user_id,data_json FROM subject_nodes_v200 WHERE node_id=?",
                (node.node_id,),
            ).fetchone()
            if existing_any is not None:
                if existing_any["user_id"] != node.user_id or existing_any["data_json"] != encoded:
                    raise SubjectGraphInvariantError("subject node identity collision")
                return SubjectNode.from_dict(json.loads(existing_any["data_json"]))
            if node.authority_class == AuthorityClass.SYSTEM_INFERRED:
                self._require_inference_contract(node)
            self._conn.execute(
                """INSERT INTO subject_nodes_v200(
                    node_id,user_id,graph_id,node_type,created_at,
                    source_event_id,semantic_identity_key,content_hash,
                    schema_version,epistemic_state,lifecycle_state,modal_state,
                    temporal_state,resolution_state,authority_class,valid_from,
                    valid_until,superseded_by,migration_origin_json,data_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    node.node_id,
                    node.user_id,
                    graph_id,
                    node.node_type,
                    node.created_at,
                    node.source_event_id,
                    node.semantic_identity_key,
                    node.content_hash,
                    node.schema_version,
                    node.epistemic_state.value,
                    node.lifecycle_state.value,
                    node.modal_state.value,
                    node.temporal_state.value,
                    node.resolution_state.value,
                    node.authority_class.value,
                    node.valid_from,
                    node.valid_until,
                    node.superseded_by,
                    self._dump(node.migration_origin or {}),
                    encoded,
                ),
            )
            self._index_record(node)
        return node

    def get_node(self, user_id: str, node_id: str) -> SubjectNode | None:
        with self._personal_store._lock:
            row = self._conn.execute(
                "SELECT data_json FROM subject_nodes_v200 WHERE user_id=? AND node_id=?",
                (user_id, node_id),
            ).fetchone()
        return SubjectNode.from_dict(json.loads(row["data_json"])) if row else None

    def list_pending_nodes(
        self,
        user_id: str,
        *,
        source_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[SubjectNode]:
        clauses = ["n.user_id=?", "n.resolution_state='PENDING'"]
        parameters: list[str | int] = [user_id]
        if source_event_id is not None:
            clauses.append("n.source_event_id=?")
            parameters.append(source_event_id)
        suffix = " ORDER BY n.created_at,n.node_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("pending node limit must be positive")
            suffix += " LIMIT ?"
            parameters.append(limit)
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT n.data_json FROM subject_nodes_v200 AS n WHERE "
                + " AND ".join(clauses)
                + """ AND NOT EXISTS (
                    SELECT 1 FROM subject_edges_v200 AS e
                    WHERE e.user_id=n.user_id AND e.target_node_id=n.node_id
                      AND e.relation_type IN (
                        'RESOLVES','INVALIDATES','SUPERSEDES','WITHDRAWS'
                      )
                  )"""
                + suffix,
                parameters,
            ).fetchall()
        return [SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows]

    def list_nodes(
        self,
        user_id: str,
        *,
        node_type: str | None = None,
        lifecycle_state: str | None = None,
        source_event_id: str | None = None,
        resolution_state: str | None = None,
        limit: int | None = None,
    ) -> list[SubjectNode]:
        """Return a tenant-scoped deterministic node projection."""

        clauses = ["user_id=?"]
        parameters: list[str | int] = [user_id]
        if node_type is not None:
            clauses.append("node_type=?")
            parameters.append(node_type)
        if lifecycle_state is not None:
            clauses.append("lifecycle_state=?")
            parameters.append(lifecycle_state)
        if source_event_id is not None:
            clauses.append("source_event_id=?")
            parameters.append(source_event_id)
        if resolution_state is not None:
            clauses.append("resolution_state=?")
            parameters.append(resolution_state)
        suffix = " ORDER BY semantic_identity_key,node_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("node limit must be positive")
            suffix += " LIMIT ?"
            parameters.append(limit)
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM subject_nodes_v200 WHERE "
                + " AND ".join(clauses)
                + suffix,
                parameters,
            ).fetchall()
        return [SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows]

    def list_latest_nodes(
        self,
        user_id: str,
        *,
        node_type: str,
        limit: int = 256,
    ) -> list[SubjectNode]:
        """Return a bounded newest-first node window for one indexed type."""

        if limit <= 0:
            raise ValueError("latest node limit must be positive")
        with self._personal_store._lock:
            rows = self._conn.execute(
                """SELECT data_json FROM subject_nodes_v200
                WHERE user_id=? AND node_type=?
                ORDER BY created_at DESC,node_id DESC LIMIT ?""",
                (user_id, node_type, limit),
            ).fetchall()
        return [SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows]

    def append_edge(self, edge: SubjectEdge) -> SubjectEdge:
        graph_id = self.ensure_graph(edge.user_id)
        with self._personal_store.transaction():
            self._require_source_event(edge.user_id, edge.source_event_id)
            source = self._node_row(edge.user_id, edge.source_node_id)
            target = self._node_row(edge.user_id, edge.target_node_id)
            if source is None or target is None:
                raise SubjectGraphInvariantError(
                    "edge endpoints must exist for the same user"
                )
            expected_hash = canonical_hash(
                {
                    "relation_type": edge.relation_type,
                    "source_semantic_identity_key": source["semantic_identity_key"],
                    "target_semantic_identity_key": target["semantic_identity_key"],
                    "status": edge.status,
                    "valid_from": edge.valid_from,
                    "valid_until": edge.valid_until,
                    "schema_version": edge.schema_version,
                }
            )
            if edge.semantic_hash and edge.semantic_hash != expected_hash:
                raise SubjectGraphInvariantError("subject edge semantic_hash mismatch")
            stored = replace(edge, semantic_hash=expected_hash)
            encoded = self._dump(stored.to_dict())
            existing = self._conn.execute(
                "SELECT user_id,data_json FROM subject_edges_v200 WHERE edge_id=?",
                (edge.edge_id,),
            ).fetchone()
            if existing is not None:
                if existing["user_id"] != edge.user_id or existing["data_json"] != encoded:
                    raise SubjectGraphInvariantError("subject edge identity collision")
                return SubjectEdge.from_dict(json.loads(existing["data_json"]))
            self._conn.execute(
                """INSERT INTO subject_edges_v200(
                    edge_id,user_id,graph_id,source_node_id,relation_type,
                    target_node_id,created_at,source_event_id,status,valid_from,
                    valid_until,superseded_by,semantic_hash,schema_version,data_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stored.edge_id,
                    stored.user_id,
                    graph_id,
                    stored.source_node_id,
                    stored.relation_type,
                    stored.target_node_id,
                    stored.created_at,
                    stored.source_event_id,
                    stored.status,
                    stored.valid_from,
                    stored.valid_until,
                    stored.superseded_by,
                    stored.semantic_hash,
                    stored.schema_version,
                    encoded,
                ),
            )
        return stored

    def get_edge(self, user_id: str, edge_id: str) -> SubjectEdge | None:
        with self._personal_store._lock:
            row = self._conn.execute(
                "SELECT data_json FROM subject_edges_v200 WHERE user_id=? AND edge_id=?",
                (user_id, edge_id),
            ).fetchone()
        return SubjectEdge.from_dict(json.loads(row["data_json"])) if row else None

    def list_edges(
        self,
        user_id: str,
        *,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        relation_type: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[SubjectEdge]:
        """Return a tenant-scoped deterministic edge projection."""

        clauses = ["user_id=?"]
        parameters: list[str | int] = [user_id]
        for field, value in (
            ("source_node_id", source_node_id),
            ("target_node_id", target_node_id),
            ("relation_type", relation_type),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{field}=?")
                parameters.append(value)
        suffix = " ORDER BY created_at,relation_type,edge_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("edge limit must be positive")
            suffix += " LIMIT ?"
            parameters.append(limit)
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM subject_edges_v200 WHERE "
                + " AND ".join(clauses)
                + suffix,
                parameters,
            ).fetchall()
        return [SubjectEdge.from_dict(json.loads(row["data_json"])) for row in rows]

    def append_hyperedge(self, hyperedge: SubjectHyperedge) -> SubjectHyperedge:
        if len(hyperedge.participants) < 2:
            raise SubjectGraphInvariantError(
                "hyperedge requires at least two participants"
            )
        graph_id = self.ensure_graph(hyperedge.user_id)
        with self._personal_store.transaction():
            self._require_source_event(hyperedge.user_id, hyperedge.source_event_id)
            normalized: list[SubjectHyperedgeParticipant] = []
            semantic_participants: list[dict[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for ordinal, participant in enumerate(hyperedge.participants):
                key = (participant.role, participant.node_id)
                if key in seen:
                    raise SubjectGraphInvariantError("duplicate hyperedge participant")
                seen.add(key)
                node = self._node_row(hyperedge.user_id, participant.node_id)
                if node is None:
                    raise SubjectGraphInvariantError(
                        "hyperedge participant must exist for the same user"
                    )
                normalized.append(replace(participant, ordinal=ordinal))
                semantic_participants.append(
                    {
                        "role": participant.role,
                        "semantic_identity_key": node["semantic_identity_key"],
                    }
                )
            expected_hash = canonical_hash(
                {
                    "relation_type": hyperedge.relation_type,
                    "participants": semantic_participants,
                    "status": hyperedge.status,
                    "valid_from": hyperedge.valid_from,
                    "valid_until": hyperedge.valid_until,
                    "schema_version": hyperedge.schema_version,
                }
            )
            if hyperedge.semantic_hash and hyperedge.semantic_hash != expected_hash:
                raise SubjectGraphInvariantError("subject hyperedge semantic_hash mismatch")
            stored = replace(
                hyperedge,
                participants=tuple(normalized),
                semantic_hash=expected_hash,
            )
            encoded = self._dump(stored.to_dict())
            existing = self._conn.execute(
                "SELECT user_id,data_json FROM subject_hyperedges_v200 "
                "WHERE hyperedge_id=?",
                (stored.hyperedge_id,),
            ).fetchone()
            if existing is not None:
                if existing["user_id"] != stored.user_id or existing["data_json"] != encoded:
                    raise SubjectGraphInvariantError("subject hyperedge identity collision")
                return SubjectHyperedge.from_dict(json.loads(existing["data_json"]))
            self._conn.execute(
                """INSERT INTO subject_hyperedges_v200(
                    hyperedge_id,user_id,graph_id,relation_type,participant_count,
                    created_at,source_event_id,status,valid_from,valid_until,
                    superseded_by,semantic_hash,schema_version,data_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stored.hyperedge_id,
                    stored.user_id,
                    graph_id,
                    stored.relation_type,
                    len(stored.participants),
                    stored.created_at,
                    stored.source_event_id,
                    stored.status,
                    stored.valid_from,
                    stored.valid_until,
                    stored.superseded_by,
                    stored.semantic_hash,
                    stored.schema_version,
                    encoded,
                ),
            )
            self._conn.executemany(
                """INSERT INTO subject_hyperedge_participants_v200(
                    user_id,hyperedge_id,ordinal,role,node_id
                ) VALUES(?,?,?,?,?)""",
                [
                    (
                        stored.user_id,
                        stored.hyperedge_id,
                        item.ordinal,
                        item.role,
                        item.node_id,
                    )
                    for item in stored.participants
                ],
            )
        return stored

    def get_hyperedge(
        self, user_id: str, hyperedge_id: str
    ) -> SubjectHyperedge | None:
        with self._personal_store._lock:
            row = self._conn.execute(
                "SELECT data_json FROM subject_hyperedges_v200 "
                "WHERE user_id=? AND hyperedge_id=?",
                (user_id, hyperedge_id),
            ).fetchone()
        return SubjectHyperedge.from_dict(json.loads(row["data_json"])) if row else None

    def find_hyperedges_by_participant(
        self, user_id: str, node_id: str
    ) -> list[SubjectHyperedge]:
        with self._personal_store._lock:
            rows = self._conn.execute(
                """SELECT h.data_json
                FROM subject_hyperedge_participants_v200 AS p
                JOIN subject_hyperedges_v200 AS h
                  ON h.user_id=p.user_id AND h.hyperedge_id=p.hyperedge_id
                WHERE p.user_id=? AND p.node_id=?
                ORDER BY h.created_at,h.hyperedge_id""",
                (user_id, node_id),
            ).fetchall()
        return [SubjectHyperedge.from_dict(json.loads(row["data_json"])) for row in rows]

    # ------------------------------------------------------------------
    # Bounded reads (S1, D4): every query below touches an index and returns at most
    # what it was asked for; none of them lists a whole user graph.

    def _index_record(self, node: SubjectNode) -> None:
        if node.node_type in record_index.INDEXED_KINDS:
            self._conn.execute(
                "INSERT INTO record_index(tokens,node_id,user_id) VALUES(?,?,?)",
                (record_index.document(record_index.record_text(node.content)), node.node_id, node.user_id),
            )

    @staticmethod
    def _chunks(values: list[str], size: int = 400) -> list[list[str]]:
        values = list(dict.fromkeys(values))
        return [values[i:i + size] for i in range(0, len(values), size)]

    def _select_in(self, sql: str, user_id: str, values: list[str], extra: tuple = ()) -> list:
        rows = []
        with self._personal_store._lock:
            for chunk in self._chunks(values):
                rows += self._conn.execute(
                    sql.replace("{in}", ",".join("?" * len(chunk))), (user_id, *extra, *chunk)
                ).fetchall()
        return rows

    def nodes_by_ids(self, user_id: str, node_ids: list[str]) -> list[SubjectNode]:
        """The existing tenant nodes among node_ids, in the order given."""
        rows = self._select_in(
            "SELECT node_id,data_json FROM subject_nodes_v200 WHERE user_id=? AND node_id IN ({in})",
            user_id, node_ids,
        )
        found = {row["node_id"]: SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows}
        return [found[i] for i in dict.fromkeys(node_ids) if i in found]

    def nodes_by_identity(self, user_id: str, keys: list[str]) -> list[SubjectNode]:
        rows = self._select_in(
            "SELECT data_json FROM subject_nodes_v200 WHERE user_id=? AND semantic_identity_key IN ({in})",
            user_id, keys,
        )
        return [SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows]

    def latest_node_ids(self, user_id: str, node_types: tuple[str, ...], limit: int) -> list[str]:
        """Ids of the newest nodes of these types, newest first."""
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT node_id FROM subject_nodes_v200 WHERE user_id=? AND node_type IN ("
                + ",".join("?" * len(node_types))
                + ") ORDER BY created_at DESC,node_id DESC LIMIT ?",
                (user_id, *node_types, limit),
            ).fetchall()
        return [row["node_id"] for row in rows]

    def search_records(self, user_id: str, text: str, limit: int) -> list[str]:
        """Ids of the record nodes whose words best match text (FTS5 bm25), best first."""
        match = record_index.query(text)
        if not match:
            return []
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT node_id FROM record_index WHERE record_index MATCH ? AND user_id=? ORDER BY rank LIMIT ?",
                (match, user_id, limit),
            ).fetchall()
        return [row["node_id"] for row in rows]

    def invalidations(self, user_id: str, node_ids: list[str]) -> list[dict[str, Any]]:
        """Active SUPERSEDES / WITHDRAWS / INVALIDATES edges into node_ids, with the edge time
        and their origin node's times."""
        rows = self._select_in(
            """SELECT e.target_node_id,e.source_node_id,e.created_at,e.relation_type,n.node_type AS origin_node_type,
                      n.created_at AS origin_created_at,n.valid_from AS origin_valid_from,
                      n.valid_until AS origin_valid_until
               FROM subject_edges_v200 AS e
               JOIN subject_nodes_v200 AS n ON n.user_id=e.user_id AND n.node_id=e.source_node_id
               WHERE e.user_id=? AND e.status='ACTIVE'
                 AND e.relation_type IN ('SUPERSEDES','WITHDRAWS','INVALIDATES')
                 AND e.target_node_id IN ({in})""",
            user_id, node_ids,
        )
        return [dict(row) for row in rows]

    def source_events_by_ids(self, user_id: str, source_event_ids: list[str]) -> dict[str, SourceEvent]:
        rows = self._select_in(
            "SELECT data_json FROM source_events_v200 WHERE user_id=? AND source_event_id IN ({in})",
            user_id, source_event_ids,
        )
        events = [SourceEvent(**json.loads(row["data_json"])) for row in rows]
        return {e.source_event_id: e for e in events}

    def edges_touching(self, user_id: str, node_ids: list[str]) -> list[SubjectEdge]:
        """Edges whose source or target is one of node_ids, in list_edges order."""
        found = {}
        for column in ("source_node_id", "target_node_id"):
            for row in self._select_in(
                f"SELECT edge_id,data_json FROM subject_edges_v200 WHERE user_id=? AND {column} IN ({{in}})",
                user_id, node_ids,
            ):
                found[row["edge_id"]] = SubjectEdge.from_dict(json.loads(row["data_json"]))
        return sorted(found.values(), key=lambda e: (e.created_at, e.relation_type, e.edge_id))

    def source_events_by_ref_prefix(self, user_id: str, source_type: str, prefix: str, since: str | None = None) -> list[SourceEvent]:
        """Source events of one type whose ref starts with prefix (an index range), oldest first."""
        sql = "SELECT data_json FROM source_events_v200 WHERE user_id=? AND source_type=? AND source_ref>=? AND source_ref<?"
        params = [user_id, source_type, prefix, prefix + "￿"]
        if since is not None:
            sql += " AND received_at>=?"
            params.append(since)
        with self._personal_store._lock:
            rows = self._conn.execute(sql + " ORDER BY received_at,source_event_id", params).fetchall()
        return [SourceEvent(**json.loads(row["data_json"])) for row in rows]

    def nodes_by_identity_prefix(self, user_id: str, prefix: str) -> list[SubjectNode]:
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM subject_nodes_v200 WHERE user_id=? AND semantic_identity_key>=? AND semantic_identity_key<?",
                (user_id, prefix, prefix + "￿"),
            ).fetchall()
        return [SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows]

    def nodes_by_authority(self, user_id: str, authority: str) -> list[SubjectNode]:
        with self._personal_store._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM subject_nodes_v200 WHERE user_id=? AND authority_class=? ORDER BY created_at,node_id",
                (user_id, authority),
            ).fetchall()
        return [SubjectNode.from_dict(json.loads(row["data_json"])) for row in rows]

    def foreign_key_violations(self) -> list[dict[str, Any]]:
        with self._personal_store._lock:
            rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
        return [dict(row) for row in rows]


__all__ = ["PersistentSubjectHypergraphStore", "SubjectGraphInvariantError"]
