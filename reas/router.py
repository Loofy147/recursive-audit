import time
from typing import List, Dict, Any, Optional
from reas.schemas import NodeSpecification, ClaimType, NodeStatus
from reas.storage import StateBiTemporalStore, VectorStore, GraphStorage
from reas.telemetry import AuditTelemetry

class QueryRouter:
    """
    Multi-Tier Query & Escalation Router.
    Implements the four-tier query routing policy:
    - Tier 1 (State Check): O(1) active state lookup if confidence > theta_low (0.65).
    - Tier 2 (Structured Query): Relational query routing for quantitative/mathematical aggregation.
    - Tier 3 (Episodic Fallback): Dense vector retrieval for conversational/conflict history.
    - Tier 4 (Graph Traversal): Multi-hop graph traversal if top similarity < theta_vec (0.72).
    """
    def __init__(
        self,
        bitemporal_store: StateBiTemporalStore,
        vector_store: VectorStore,
        graph_storage: GraphStorage,
        theta_low: float = 0.65,
        theta_vec: float = 0.72
    ):
        self.bitemporal_store = bitemporal_store
        self.vector_store = vector_store
        self.graph_storage = graph_storage
        self.theta_low = theta_low
        self.theta_vec = theta_vec
        self.telemetry = AuditTelemetry()

    def route_query(
        self,
        query_text: str,
        case_id: str,
        target_node_id: Optional[str] = None,
        force_tier: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Routes the query to the appropriate tier according to REAS policy.
        """
        start_time = time.perf_counter()
        lower_query = query_text.lower()

        # --- Tier 1 (State Check) ---
        if force_tier == 1 or (target_node_id and force_tier is None):
            node_state = self.bitemporal_store.get_node_state(case_id, target_node_id)
            if node_state:
                # Calculate average confidence across dimensions
                cv = node_state.confidence_vector
                avg_confidence = 0.0
                if cv:
                    avg_confidence = (
                        cv.source_credibility +
                        cv.empirical_grounding +
                        cv.logical_soundness +
                        cv.causal_strength +
                        cv.temporal_stability
                    ) / 5.0

                if avg_confidence > self.theta_low or force_tier == 1:
                    self.telemetry.record_state_check(hit=True)
                    duration = time.perf_counter() - start_time
                    self.telemetry.record_query_latency(1, duration)
                    return {
                        "tier": 1,
                        "route": "Tier 1: State Check",
                        "node_id": target_node_id,
                        "state": node_state.model_dump(),
                        "confidence": avg_confidence
                    }
                else:
                    self.telemetry.record_state_check(hit=False)
            else:
                self.telemetry.record_state_check(hit=False)

        # --- Tier 2 (Structured Query) ---
        is_structured_signal = any(word in lower_query for word in ["how many", "count", "average", "mean", "sum", "total", "ratio", "percentage", "aggregate"])
        if force_tier == 2 or (is_structured_signal and force_tier is None):
            # Run simple query against sqlite database
            cursor = self.bitemporal_store.conn.cursor()

            # Simple automatic count/aggregations
            if "count" in lower_query or "how many" in lower_query:
                cursor.execute("SELECT COUNT(*) as count FROM node_states WHERE case_id = ? AND transaction_end IS NULL", (case_id,))
                res = cursor.fetchone()
                data = {"count": res["count"] if res else 0}
            elif "average" in lower_query or "mean" in lower_query:
                cursor.execute("SELECT status, COUNT(*) as count FROM node_states WHERE case_id = ? AND transaction_end IS NULL GROUP BY status", (case_id,))
                rows = cursor.fetchall()
                data = {"status_distribution": {r["status"]: r["count"] for r in rows}}
            else:
                cursor.execute("SELECT node_id, status, claim_type FROM node_states WHERE case_id = ? AND transaction_end IS NULL", (case_id,))
                rows = cursor.fetchall()
                data = {"nodes": [{"node_id": r["node_id"], "status": r["status"], "claim_type": r["claim_type"]} for r in rows]}

            duration = time.perf_counter() - start_time
            self.telemetry.record_query_latency(2, duration)
            return {
                "tier": 2,
                "route": "Tier 2: Structured Query",
                "result": data
            }

        # --- Tier 3 (Episodic Fallback) ---
        # Get dense similarity scores
        vector_results = self.vector_store.query(query_text, top_k=1)
        top_similarity = vector_results[0][1] if vector_results else 0.0

        is_episodic_signal = any(word in lower_query for word in ["context", "conflict", "history", "episodic", "conflated", "conversation"])

        if force_tier == 3 or (force_tier is None and (is_episodic_signal or top_similarity >= self.theta_vec)):
            duration = time.perf_counter() - start_time
            self.telemetry.record_query_latency(3, duration)
            return {
                "tier": 3,
                "route": "Tier 3: Episodic Fallback",
                "top_similarity": top_similarity,
                "results": [
                    {"doc": r[0], "similarity": r[1]}
                    for r in vector_results
                ]
            }

        # --- Tier 4 (Graph Traversal) ---
        # First, align graph storage with current active database state for case_id
        self.graph_storage.clear()
        nodes = self.bitemporal_store.get_all_active_nodes(case_id)
        edges = self.bitemporal_store.get_all_active_edges(case_id)
        for n in nodes:
            self.graph_storage.add_node(
                node_id=n.node_id,
                claim_type=n.claim_type.value,
                claim_text=n.claim_text,
                status=n.status.value
            )
        for e in edges:
            self.graph_storage.add_edge(
                source_id=e.source_id,
                target_id=e.target_id,
                edge_type=e.edge_type.value,
                confidence_vector=e.confidence_vector.model_dump() if e.confidence_vector else None
            )

        # Perform graph-based multi-hop traversal
        neighbors = {}
        if target_node_id:
            successors = self.graph_storage.get_successors(target_node_id)
            predecessors = self.graph_storage.get_predecessors(target_node_id)
            neighbors = {
                "successors": successors,
                "predecessors": predecessors
            }
        else:
            try:
                topo = self.graph_storage.topological_sort()
            except Exception:
                topo = []
            neighbors = {
                "topological_sort": topo,
                "cycles": self.graph_storage.detect_cycles()
            }

        duration = time.perf_counter() - start_time
        self.telemetry.record_query_latency(4, duration)
        return {
            "tier": 4,
            "route": "Tier 4: Graph Traversal",
            "top_similarity": top_similarity,
            "neighbors": neighbors
        }
