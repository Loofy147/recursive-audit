import sqlite3
import json
import math
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import networkx as nx

from reas.schemas import (
    NodeSpecification,
    EdgeSpecification,
    ConfidenceVector,
    ScopeEnvelope,
    RetractionMetadata,
    ExecutionBinding,
    ClaimType,
    NodeStatus,
    EdgeType
)

class GraphStorage:
    """
    Graph Storage Engine: Handles dependency link traversals,
    cycle detection, and topological sorting using NetworkX.
    """
    def __init__(self):
        self.g = nx.DiGraph()

    def add_node(self, node_id: str, claim_type: str, claim_text: str, status: str, metadata: dict = None):
        self.g.add_node(
            node_id,
            claim_type=claim_type,
            claim_text=claim_text,
            status=status,
            metadata=metadata or {}
        )

    def add_edge(self, source_id: str, target_id: str, edge_type: str, confidence_vector: Optional[dict] = None):
        self.g.add_edge(
            source_id,
            target_id,
            edge_type=edge_type,
            confidence_vector=confidence_vector or {}
        )

    def get_successors(self, node_id: str) -> List[str]:
        if node_id in self.g:
            return list(self.g.successors(node_id))
        return []

    def get_predecessors(self, node_id: str) -> List[str]:
        if node_id in self.g:
            return list(self.g.predecessors(node_id))
        return []

    def detect_cycles(self) -> List[List[str]]:
        """
        Detects circular dependency loops.
        Returns list of cycles, where each cycle is a list of node IDs.
        """
        try:
            cycles = list(nx.simple_cycles(self.g))
            return cycles
        except Exception:
            return []

    def topological_sort(self) -> List[str]:
        """
        Returns a topological sort of the directed graph.
        Raises NetworkXUnfeasible if the graph is not a DAG (contains cycles).
        """
        return list(nx.topological_sort(self.g))

    def clear(self):
        self.g.clear()


class StateBiTemporalStore:
    """
    State & Bi-Temporal Store: Tracks valid time vs transaction time
    to maintain versioned historical state records without destructive updates.
    """
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._initialize_db()

    def _initialize_db(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS node_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT,
                node_id TEXT,
                claim_type TEXT,
                claim_text TEXT,
                status TEXT,
                confidence_vector TEXT,
                scope_envelope TEXT,
                retraction_metadata TEXT,
                execution_binding TEXT,
                valid_start TEXT,
                valid_end TEXT,
                transaction_start TEXT,
                transaction_end TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edge_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT,
                source_id TEXT,
                target_id TEXT,
                edge_type TEXT,
                confidence_vector TEXT,
                valid_start TEXT,
                valid_end TEXT,
                transaction_start TEXT,
                transaction_end TEXT
            )
        """)
        self.conn.commit()

    def _get_now_str(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_node_state(
        self,
        case_id: str,
        node: NodeSpecification,
        valid_start: Optional[str] = None,
        valid_end: Optional[str] = None
    ):
        """
        Saves a node state bi-temporally. It closes the previous transaction interval
        for this node, and inserts a new historical entry.
        """
        now = self._get_now_str()
        v_start = valid_start or now
        v_end = valid_end  # can remain None/null

        cursor = self.conn.cursor()

        # 1. Close current transaction for this specific node in this case
        cursor.execute("""
            UPDATE node_states
            SET transaction_end = ?
            WHERE case_id = ? AND node_id = ? AND transaction_end IS NULL
        """, (now, case_id, node.node_id))

        # 2. Insert new state record
        conf_str = json.dumps(node.confidence_vector.model_dump()) if node.confidence_vector else None
        scope_str = json.dumps(node.scope_envelope.model_dump()) if node.scope_envelope else None
        retract_str = json.dumps(node.retraction_metadata.model_dump()) if node.retraction_metadata else None
        exec_str = json.dumps(node.execution_binding.model_dump()) if node.execution_binding else None

        cursor.execute("""
            INSERT INTO node_states (
                case_id, node_id, claim_type, claim_text, status,
                confidence_vector, scope_envelope, retraction_metadata, execution_binding,
                valid_start, valid_end, transaction_start, transaction_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            case_id, node.node_id, node.claim_type.value, node.claim_text, node.status.value,
            conf_str, scope_str, retract_str, exec_str,
            v_start, v_end, now
        ))

        self.conn.commit()

    def save_edge_state(
        self,
        case_id: str,
        edge: EdgeSpecification,
        valid_start: Optional[str] = None,
        valid_end: Optional[str] = None
    ):
        """
        Saves an edge state bi-temporally.
        """
        now = self._get_now_str()
        v_start = valid_start or now
        v_end = valid_end

        cursor = self.conn.cursor()

        # 1. Close active transaction
        cursor.execute("""
            UPDATE edge_states
            SET transaction_end = ?
            WHERE case_id = ? AND source_id = ? AND target_id = ? AND transaction_end IS NULL
        """, (now, case_id, edge.source_id, edge.target_id))

        # 2. Insert new edge record
        conf_str = json.dumps(edge.confidence_vector.model_dump()) if edge.confidence_vector else None

        cursor.execute("""
            INSERT INTO edge_states (
                case_id, source_id, target_id, edge_type, confidence_vector,
                valid_start, valid_end, transaction_start, transaction_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            case_id, edge.source_id, edge.target_id, edge.edge_type.value, conf_str,
            v_start, v_end, now
        ))

        self.conn.commit()

    def get_node_state(
        self,
        case_id: str,
        node_id: str,
        as_of_valid_time: Optional[str] = None,
        as_of_transaction_time: Optional[str] = None
    ) -> Optional[NodeSpecification]:
        """
        Retrieves a node specification matching bi-temporal filters.
        - If as_of_valid_time is None, we query active valid records.
        - If as_of_transaction_time is None, we retrieve current active transaction records (transaction_end IS NULL).
        """
        cursor = self.conn.cursor()

        query = "SELECT * FROM node_states WHERE case_id = ? AND node_id = ?"
        params = [case_id, node_id]

        if as_of_transaction_time:
            query += " AND transaction_start <= ? AND (transaction_end IS NULL OR transaction_end > ?)"
            params.extend([as_of_transaction_time, as_of_transaction_time])
        else:
            query += " AND transaction_end IS NULL"

        if as_of_valid_time:
            query += " AND valid_start <= ? AND (valid_end IS NULL OR valid_end > ?)"
            params.extend([as_of_valid_time, as_of_valid_time])

        # Order by newest id to get the latest if multiple matches exist due to overlaps
        query += " ORDER BY id DESC LIMIT 1"
        cursor.execute(query, params)
        row = cursor.fetchone()

        if not row:
            return None

        # Build NodeSpecification from database row
        conf_data = json.loads(row["confidence_vector"]) if row["confidence_vector"] else None
        scope_data = json.loads(row["scope_envelope"]) if row["scope_envelope"] else None
        retract_data = json.loads(row["retraction_metadata"]) if row["retraction_metadata"] else None
        exec_data = json.loads(row["execution_binding"]) if row["execution_binding"] else None

        return NodeSpecification(
            node_id=row["node_id"],
            claim_type=ClaimType(row["claim_type"]),
            claim_text=row["claim_text"],
            status=NodeStatus(row["status"]),
            retraction_metadata=RetractionMetadata(**retract_data) if retract_data else None,
            execution_binding=ExecutionBinding(**exec_data) if exec_data else None,
            scope_envelope=ScopeEnvelope(**scope_data) if scope_data else None,
            confidence_vector=ConfidenceVector(**conf_data) if conf_data else None
        )

    def get_all_active_nodes(self, case_id: str) -> List[NodeSpecification]:
        """
        Retrieves all currently active nodes for a given case_id.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM node_states
            WHERE case_id = ? AND transaction_end IS NULL
        """, (case_id,))
        rows = cursor.fetchall()
        nodes = []
        for row in rows:
            conf_data = json.loads(row["confidence_vector"]) if row["confidence_vector"] else None
            scope_data = json.loads(row["scope_envelope"]) if row["scope_envelope"] else None
            retract_data = json.loads(row["retraction_metadata"]) if row["retraction_metadata"] else None
            exec_data = json.loads(row["execution_binding"]) if row["execution_binding"] else None

            nodes.append(NodeSpecification(
                node_id=row["node_id"],
                claim_type=ClaimType(row["claim_type"]),
                claim_text=row["claim_text"],
                status=NodeStatus(row["status"]),
                retraction_metadata=RetractionMetadata(**retract_data) if retract_data else None,
                execution_binding=ExecutionBinding(**exec_data) if exec_data else None,
                scope_envelope=ScopeEnvelope(**scope_data) if scope_data else None,
                confidence_vector=ConfidenceVector(**conf_data) if conf_data else None
            ))
        return nodes

    def get_all_active_edges(self, case_id: str) -> List[EdgeSpecification]:
        """
        Retrieves all currently active edges for a given case_id.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM edge_states
            WHERE case_id = ? AND transaction_end IS NULL
        """, (case_id,))
        rows = cursor.fetchall()
        edges = []
        for row in rows:
            conf_data = json.loads(row["confidence_vector"]) if row["confidence_vector"] else None
            edges.append(EdgeSpecification(
                source_id=row["source_id"],
                target_id=row["target_id"],
                edge_type=EdgeType(row["edge_type"]),
                confidence_vector=ConfidenceVector(**conf_data) if conf_data else None
            ))
        return edges

    def get_edge_state(
        self,
        case_id: str,
        source_id: str,
        target_id: str,
        as_of_valid_time: Optional[str] = None,
        as_of_transaction_time: Optional[str] = None
    ) -> Optional[EdgeSpecification]:
        """
        Retrieves an edge specification matching bi-temporal filters.
        """
        cursor = self.conn.cursor()
        query = "SELECT * FROM edge_states WHERE case_id = ? AND source_id = ? AND target_id = ?"
        params = [case_id, source_id, target_id]

        if as_of_transaction_time:
            query += " AND transaction_start <= ? AND (transaction_end IS NULL OR transaction_end > ?)"
            params.extend([as_of_transaction_time, as_of_transaction_time])
        else:
            query += " AND transaction_end IS NULL"

        if as_of_valid_time:
            query += " AND valid_start <= ? AND (valid_end IS NULL OR valid_end > ?)"
            params.extend([as_of_valid_time, as_of_valid_time])

        query += " ORDER BY id DESC LIMIT 1"
        cursor.execute(query, params)
        row = cursor.fetchone()

        if not row:
            return None

        conf_data = json.loads(row["confidence_vector"]) if row["confidence_vector"] else None

        return EdgeSpecification(
            source_id=row["source_id"],
            target_id=row["target_id"],
            edge_type=EdgeType(row["edge_type"]),
            confidence_vector=ConfidenceVector(**conf_data) if conf_data else None
        )

    def close(self):
        self.conn.close()


class VectorStore:
    """
    Vector Storage: A dense-like vector index for episodic conversational context fallback.
    Implements TF-IDF and Cosine Similarity for pure Python-based deterministic similarity query.
    """
    def __init__(self):
        self.documents = []  # List of Dict containing: {"doc_id": str, "text": str, "metadata": dict}

    def add_document(self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None):
        self.documents.append({
            "doc_id": doc_id,
            "text": text,
            "metadata": metadata or {}
        })

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'\w+', text.lower())

    def query(self, query_text: str, top_k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        """
        Queries the vector index using TF-IDF cosine similarity.
        Returns a list of tuples containing (document_dict, similarity_score).
        """
        if not self.documents:
            return []

        all_tokens = [self._tokenize(doc["text"]) for doc in self.documents]
        query_tokens = self._tokenize(query_text)

        # Build vocabulary across docs and query
        vocab = set()
        for tokens in all_tokens:
            vocab.update(tokens)
        vocab.update(query_tokens)

        vocab_list = sorted(list(vocab))
        vocab_idx = {word: idx for idx, word in enumerate(vocab_list)}

        # Document frequencies for inverse document frequency (IDF)
        df = {}
        for word in vocab_list:
            df[word] = sum(1 for tokens in all_tokens if word in tokens)

        N = len(self.documents)

        # Compute TF-IDF vectors for documents
        doc_vectors = []
        for tokens in all_tokens:
            vec = [0.0] * len(vocab_list)
            tf = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            for word, count in tf.items():
                # IDF formula with smoothing to avoid divide-by-zero
                idf = math.log((1 + N) / (1 + df.get(word, 0))) + 1
                vec[vocab_idx[word]] = count * idf
            doc_vectors.append(vec)

        # Compute TF-IDF vector for query
        q_vec = [0.0] * len(vocab_list)
        q_tf = {}
        for t in query_tokens:
            q_tf[t] = q_tf.get(t, 0) + 1
        for word, count in q_tf.items():
            idf = math.log((1 + N) / (1 + df.get(word, 0))) + 1
            q_vec[vocab_idx[word]] = count * idf

        # Compute Cosine Similarity between query vector and documents
        results = []
        for i, doc_vec in enumerate(doc_vectors):
            dot_product = sum(a * b for a, b in zip(doc_vec, q_vec))
            norm_doc = math.sqrt(sum(a * a for a in doc_vec))
            norm_q = math.sqrt(sum(b * b for b in q_vec))

            similarity = 0.0
            if norm_doc > 0.0 and norm_q > 0.0:
                similarity = dot_product / (norm_doc * norm_q)

            results.append((self.documents[i], similarity))

        # Sort by similarity score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
