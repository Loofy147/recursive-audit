from typing import List, Dict, Any
from reas.schemas import NodeSpecification, ClaimType, NodeStatus, ConfidenceVector

class RepresentationAudit:
    """
    Parses node text, checks claim classification types,
    detects enthymemes (implicit unstated assumptions),
    and calculates charity delta adjustments.
    """
    def __init__(self):
        pass

    def audit_node(self, node: NodeSpecification, predecessors: List[NodeSpecification]) -> Dict[str, Any]:
        findings = []
        reconstructed_assumptions = []
        charity_delta = 0.0

        # 1. Check claim classification types
        valid_types = {t.value for t in ClaimType}
        if node.claim_type.value not in valid_types:
            findings.append({
                "check_id": "REP_CLAIM_TYPE_INVALID",
                "node_id": node.node_id,
                "severity": "HARD",
                "message": f"Invalid claim type: {node.claim_type.value}"
            })

        # 2. Enthymeme detection
        # Look for logical indicators like "therefore", "since", "because", "implies", "so", "consequently", "as a result"
        text = node.claim_text.lower()
        logical_indicators = ["therefore", "since", "because", "implies", "so", "consequently", "as a result"]
        has_indicator = any(ind in text for ind in logical_indicators)

        # Check if there are unstated premises (e.g. no predecessors, or predecessors don't cover the terms)
        if has_indicator and len(predecessors) == 0:
            # We found an enthymeme! Reconstruct the implicit assumption.
            reconstructed_id = f"A-RECON-{node.node_id}"
            reconstructed_text = f"Implicit premise for {node.node_id}: '{node.claim_text}'"

            reconstructed_node = NodeSpecification(
                node_id=reconstructed_id,
                claim_type=ClaimType.ASSUMPTION,
                claim_text=reconstructed_text,
                status=NodeStatus.ACTIVE,
                confidence_vector=ConfidenceVector(
                    source_credibility=0.5,
                    empirical_grounding=0.0,
                    logical_soundness=0.5,
                    causal_strength=0.0,
                    temporal_stability=0.5
                )
            )
            reconstructed_assumptions.append(reconstructed_node)

            # Charity delta represents the structural adjustment (e.g., charity buffer applied)
            charity_delta = 0.2
            findings.append({
                "check_id": "REP_ENTHYMEME_DETECTED",
                "node_id": node.node_id,
                "severity": "SOFT",
                "message": f"Implicit unstated premise (enthymeme) detected in text: '{node.claim_text}'. Charitably reconstructed as {reconstructed_id}.",
                "reconstructed_node_id": reconstructed_id,
                "charity_delta": charity_delta
            })

        return {
            "findings": findings,
            "reconstructed_assumptions": reconstructed_assumptions,
            "charity_delta": charity_delta
        }
