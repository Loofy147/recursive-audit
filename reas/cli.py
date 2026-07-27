import sys
import os
import json
import argparse
from typing import List, Dict, Any, Optional

from reas.schemas import CaseSpecification, NodeSpecification, EdgeSpecification, NodeStatus, ClaimType
from reas.storage import GraphStorage, StateBiTemporalStore, VectorStore
from reas.representation_audit import RepresentationAudit
from reas.evidence_audit import EvidenceAudit
from reas.dependency_audit import DependencyAudit, ModelAudit
from reas.tms import TruthMaintenanceSystem
from reas.outcome_audit import OutcomeAudit

class REASAuditEngine:
    """
    Integrates and orchestrates the complete REAS pipeline:
    Stage 1: Ingest & Construct
    Stage 2: Representation Audit
    Stage 3: Evidence Verification
    Stage 4: Dependency & Model Audit
    Stage 5: Propagation & Arbitration
    Stage 6: Outcome Audit
    """
    def __init__(self, db_path: str = ":memory:", canon_dir: str = None):
        self.graph_storage = GraphStorage()
        self.bitemporal_store = StateBiTemporalStore(db_path)
        self.vector_store = VectorStore()
        self.rep_audit = RepresentationAudit()
        self.evi_audit = EvidenceAudit(canon_dir)
        self.dep_audit = DependencyAudit(self.graph_storage)
        self.mod_audit = ModelAudit()
        self.tms = TruthMaintenanceSystem(self.graph_storage)
        self.out_audit = OutcomeAudit()

    def execute_audit_pipeline(self, case_spec: CaseSpecification, canon_files: List[str] = None) -> Dict[str, Any]:
        findings = []
        nodes = list(case_spec.nodes)
        edges = list(case_spec.edges)
        case_id = case_spec.case_id
        c_files = canon_files or []

        # -- Stage 1: Ingest & Construct --
        for node in nodes:
            self.vector_store.add_document(
                doc_id=node.node_id,
                text=f"{node.claim_text} (status: {node.status.value})",
                metadata={"claim_type": node.claim_type.value, "status": node.status.value}
            )
            self.bitemporal_store.save_node_state(case_id, node)

        for edge in edges:
            self.bitemporal_store.save_edge_state(case_id, edge)

        # -- Stage 2: Representation Audit --
        reconstructed_nodes = []
        for node in nodes:
            predecessors_ids = [e.source_id for e in edges if e.target_id == node.node_id]
            predecessors = [n for n in nodes if n.node_id in predecessors_ids]

            rep_res = self.rep_audit.audit_node(node, predecessors)
            if rep_res["findings"]:
                findings.extend(rep_res["findings"])
            if rep_res["reconstructed_assumptions"]:
                reconstructed_nodes.extend(rep_res["reconstructed_assumptions"])

        for r_node in reconstructed_nodes:
            self.bitemporal_store.save_node_state(case_id, r_node)
            nodes.append(r_node)

        # -- Stage 3: Evidence Audit --
        for node in nodes:
            node_findings = self.evi_audit.audit_node(node, c_files)
            findings.extend(node_findings)

        # -- Stage 4: Dependency & Model Audit --
        dep_findings = self.dep_audit.audit_graph(case_id, nodes, edges)
        findings.extend(dep_findings)

        mod_findings = self.mod_audit.audit_nodes(nodes)
        findings.extend(mod_findings)

        # -- Stage 5: Propagation & Arbitration --
        for node in nodes:
            if node.status == NodeStatus.RETIRED:
                tms_findings = self.tms.propagate(nodes, edges, node.node_id)
                findings.extend(tms_findings)

        # -- Stage 6: Outcome Audit --
        dossier = self.out_audit.evaluate(findings)
        dossier["case_id"] = case_id
        return dossier

def main():
    parser = argparse.ArgumentParser(description="Recursive Evidence-Audit System (REAS) CLI Verifier")
    parser.add_argument("case_file", help="Path to Case Specification JSON file")
    parser.add_argument("--canon", nargs="*", help="List of canonical reference/canon files for signature sweeps")
    args = parser.parse_args()

    if not os.path.exists(args.case_file):
        print(f"Error: Case specification file not found: {args.case_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.case_file, "r") as f:
            data = json.load(f)

        case_spec = CaseSpecification(**data)
    except Exception as e:
        print(f"Error: Schema validation failed or invalid JSON: {str(e)}", file=sys.stderr)
        sys.exit(1)

    engine = REASAuditEngine()
    dossier = engine.execute_audit_pipeline(case_spec, args.canon)

    print(json.dumps(dossier, indent=2))
    sys.exit(dossier["exit_code"])

if __name__ == "__main__":
    main()
