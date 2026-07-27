from typing import List, Dict, Any
from reas.schemas import NodeSpecification, EdgeSpecification, EdgeType, NodeStatus, ClaimType
from reas.storage import GraphStorage

class DependencyAudit:
    """
    Dependency Audit Module:
    - Scans graph topologies for circular dependency loops.
    - Resolves nonmonotonic default inferences against active defeaters and attack edges.
    """
    def __init__(self, graph_storage: GraphStorage):
        self.graph_storage = graph_storage

    def audit_graph(self, case_id: str, nodes: List[NodeSpecification], edges: List[EdgeSpecification]) -> List[Dict[str, Any]]:
        findings = []

        # 1. Circular Dependency Check
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
                edge_type=edge.edge_type.value,
                confidence_vector=edge.confidence_vector.model_dump() if edge.confidence_vector else None
            )

        cycles = self.graph_storage.detect_cycles()
        if cycles:
            for cycle in cycles:
                findings.append({
                    "check_id": "DEP_CIRCULAR_DEPENDENCY",
                    "severity": "HARD",
                    "message": f"Circular dependency loop detected: {' -> '.join(cycle)} -> {cycle[0]}"
                })

        # 2. Nonmonotonic default inference resolution against active defeaters and attack edges
        for edge in edges:
            if edge.edge_type in (EdgeType.DEFEAT, EdgeType.ATTACK):
                source_node = next((n for n in nodes if n.node_id == edge.source_id), None)
                target_node = next((n for n in nodes if n.node_id == edge.target_id), None)

                if source_node and target_node:
                    source_active = (source_node.status != NodeStatus.RETIRED)
                    if source_active:
                        findings.append({
                            "check_id": "DEP_DEFEATER_ACTIVE",
                            "node_id": target_node.node_id,
                            "severity": "SOFT",
                            "message": f"Node {target_node.node_id} is actively DEFEATED/ATTACKED by {source_node.node_id}."
                        })

        return findings


class ModelAudit:
    """
    Model Audit Module:
    - Validates causal rungs (association, intervention, counterfactual) against specified statistical/causal constraints.
    - Under REAS, causal rung height mapped as:
      - association = 0.33
      - intervention = 0.66
      - counterfactual = 1.0
    """
    def __init__(self):
        pass

    def audit_nodes(self, nodes: List[NodeSpecification]) -> List[Dict[str, Any]]:
        findings = []

        for node in nodes:
            if node.claim_type == ClaimType.COUNTERFACTUAL:
                req_causal = 1.0
                if node.confidence_vector and node.confidence_vector.causal_strength < req_causal:
                    findings.append({
                        "check_id": "MOD_CAUSAL_RUNG_MISMATCH",
                        "node_id": node.node_id,
                        "severity": "HARD",
                        "message": f"Counterfactual claim {node.node_id} requires causal rung height {req_causal}, but causal_strength is {node.confidence_vector.causal_strength}."
                    })
            elif node.claim_type == ClaimType.CAUSAL:
                req_causal = 0.66
                if node.confidence_vector and node.confidence_vector.causal_strength < req_causal:
                    findings.append({
                        "check_id": "MOD_CAUSAL_RUNG_MISMATCH",
                        "node_id": node.node_id,
                        "severity": "SOFT",
                        "message": f"Causal claim {node.node_id} requires causal rung height {req_causal} (intervention), but causal_strength is {node.confidence_vector.causal_strength}."
                    })

        return findings
