from typing import List, Dict, Any, Optional
from reas.schemas import (
    NodeSpecification,
    EdgeSpecification,
    ConfidenceVector,
    NodeStatus,
    EdgeType,
    ClaimType
)
from reas.storage import GraphStorage

class TruthMaintenanceSystem:
    """
    Truth Maintenance System (TMS) & Propagation Engine.
    Implements:
    - Nonmonotonic State Arbitration
    - Parallel Conflict Branch Isolation (BRANCH_CONFLICT)
    - Recursive Retraction Propagation Sweep
    - Stop rules (Convergence threshold epsilon, Branch isolation)
    """
    def __init__(self, graph_storage: GraphStorage):
        self.graph_storage = graph_storage

    def propagate(
        self,
        nodes: List[NodeSpecification],
        edges: List[EdgeSpecification],
        start_node_id: str,
        epsilon: float = 1e-5
    ) -> List[Dict[str, Any]]:
        """
        Runs the propagation sweep starting from start_node_id.
        Updates confidence vectors of downstream nodes in-place.
        Returns a list of findings (e.g. retraction contamination, etc.).
        """
        findings = []
        node_map = {n.node_id: n for n in nodes}

        # Build networkx graph for path analysis
        self.graph_storage.clear()
        for node in nodes:
            self.graph_storage.add_node(
                node_id=node.node_id,
                claim_type=node.claim_type.value,
                claim_text=node.claim_text,
                status=node.status.value
            )
        for edge in edges:
            self.graph_storage.add_edge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                edge_type=edge.edge_type.value
            )

        # 1. Recursive Retraction Sweep
        start_node = node_map.get(start_node_id)
        if start_node and start_node.status == NodeStatus.RETIRED:
            if start_node.retraction_metadata and start_node.retraction_metadata.signatures:
                signatures = start_node.retraction_metadata.signatures

                # Find all downstream descendants in graph
                descendants = set()
                queue = [start_node_id]
                visited = set()
                while queue:
                    curr = queue.pop(0)
                    if curr not in visited:
                        visited.add(curr)
                        successors = self.graph_storage.get_successors(curr)
                        for succ in successors:
                            # Skip if BRANCH_CONFLICT edge isolates this branch
                            edge_type = None
                            for e in edges:
                                if e.source_id == curr and e.target_id == succ:
                                    edge_type = e.edge_type
                                    break
                            if edge_type == EdgeType.BRANCH_CONFLICT:
                                continue  # Branch isolation

                            descendants.add(succ)
                            queue.append(succ)

                # For each downstream descendant, check for signature contamination
                for desc_id in descendants:
                    desc_node = node_map.get(desc_id)
                    if not desc_node:
                        continue

                    desc_text = desc_node.claim_text.lower()
                    for sig in signatures:
                        if sig.lower() in desc_text:
                            awareness_keywords = ["retracted", "retired", "deprecated", "invalidated", "acknowledged", "reopened", "aware"]
                            is_aware = any(kw in desc_text for kw in awareness_keywords) or desc_node.status == NodeStatus.REOPENED

                            if is_aware:
                                findings.append({
                                    "check_id": "RETRACTION_AWARE_UNVERIFIED_SOFT",
                                    "node_id": desc_id,
                                    "severity": "SOFT",
                                    "message": f"Downstream node {desc_id} acknowledges retired signature '{sig}' but remains unverified."
                                })
                            else:
                                findings.append({
                                    "check_id": "RETRACTION_CONTAMINATION_HARD",
                                    "node_id": desc_id,
                                    "severity": "HARD",
                                    "message": f"Downstream node {desc_id} contains retired signature '{sig}' without acknowledging its retraction."
                                })

                            if desc_node.claim_type in (ClaimType.CONCLUSION, "conclusion"):
                                desc_node.status = NodeStatus.REOPENED

        # 2. Confidence Propagation Sweep
        queue = [start_node_id]
        visited_count = {}

        while queue:
            curr_id = queue.pop(0)
            curr_node = node_map.get(curr_id)
            if not curr_node:
                continue

            visited_count[curr_id] = visited_count.get(curr_id, 0) + 1
            if visited_count[curr_id] > 50:
                continue

            out_edges = [e for e in edges if e.source_id == curr_id]
            for edge in out_edges:
                target_id = edge.target_id
                target_node = node_map.get(target_id)
                if not target_node:
                    continue

                if edge.edge_type == EdgeType.BRANCH_CONFLICT:
                    continue

                old_cv = target_node.confidence_vector or ConfidenceVector()
                new_cv_dict = old_cv.model_dump()

                inc_edges = [e for e in edges if e.target_id == target_id]

                for inc_edge in inc_edges:
                    src_node = node_map.get(inc_edge.source_id)
                    if not src_node:
                        continue

                    src_cv = src_node.confidence_vector or ConfidenceVector()
                    edge_cv = inc_edge.confidence_vector or ConfidenceVector(
                        source_credibility=1.0, empirical_grounding=1.0,
                        logical_soundness=1.0, causal_strength=1.0, temporal_stability=1.0
                    )

                    if inc_edge.edge_type == EdgeType.SUPPORT:
                        for dim in new_cv_dict.keys():
                            p_src = getattr(src_cv, dim)
                            p_edge = getattr(edge_cv, dim)
                            p_curr = new_cv_dict[dim]
                            new_cv_dict[dim] = p_curr + (1.0 - p_curr) * p_src * p_edge

                    elif inc_edge.edge_type == EdgeType.REFINE:
                        new_cv_dict["temporal_stability"] = min(
                            new_cv_dict["temporal_stability"],
                            src_cv.temporal_stability
                        )

                    elif inc_edge.edge_type == EdgeType.SUPERSEDE:
                        if src_node.status != NodeStatus.RETIRED:
                            target_node.status = NodeStatus.RETIRED
                            for dim in new_cv_dict.keys():
                                new_cv_dict[dim] = 0.0

                    elif inc_edge.edge_type in (EdgeType.DEFEAT, EdgeType.ATTACK):
                        for dim in new_cv_dict.keys():
                            p_src = getattr(src_cv, dim)
                            p_edge = getattr(edge_cv, dim)
                            new_cv_dict[dim] = new_cv_dict[dim] * (1.0 - p_src * p_edge)

                new_cv = ConfidenceVector(**new_cv_dict)

                diffs = [
                    abs(getattr(new_cv, dim) - getattr(old_cv, dim))
                    for dim in new_cv_dict.keys()
                ]
                max_diff = max(diffs) if diffs else 0.0

                if max_diff >= epsilon:
                    target_node.confidence_vector = new_cv
                    queue.append(target_id)

        return findings
