import sys
import os
import json
import argparse
import subprocess
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

    def execute_audit_pipeline(
        self,
        case_spec: CaseSpecification,
        canon_files: List[str] = None,
        incremental_nodes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        findings = []
        nodes = list(case_spec.nodes)
        edges = list(case_spec.edges)
        case_id = case_spec.case_id
        c_files = canon_files or []

        # If incremental mode is on, we'll only run some audits on the modified sub-regions
        is_incremental = incremental_nodes is not None
        inc_node_set = set(incremental_nodes) if is_incremental else None

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
            # Skip if incremental and this node is not part of the modified set
            if is_incremental and node.node_id not in inc_node_set:
                continue

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
            # Skip if incremental and this node is not part of the modified set
            if is_incremental and node.node_id not in inc_node_set:
                continue
            node_findings = self.evi_audit.audit_node(node, c_files)
            findings.extend(node_findings)

        # -- Stage 4: Dependency & Model Audit --
        # Dependency check cycle detection needs full graph, so we run full or targeted
        dep_findings = self.dep_audit.audit_graph(case_id, nodes, edges)
        findings.extend(dep_findings)

        # Model audit can be filtered by changed nodes in incremental mode
        nodes_to_model_audit = [n for n in nodes if n.node_id in inc_node_set] if is_incremental else nodes
        mod_findings = self.mod_audit.audit_nodes(nodes_to_model_audit)
        findings.extend(mod_findings)

        # -- Stage 5: Propagation & Arbitration --
        # Run TMS propagation from each RETIRED node (or only the modified RETIRED nodes if incremental)
        for node in nodes:
            if node.status == NodeStatus.RETIRED:
                if is_incremental and node.node_id not in inc_node_set:
                    continue
                tms_findings = self.tms.propagate(nodes, edges, node.node_id, case_id=case_id)
                findings.extend(tms_findings)

        # -- Stage 6: Outcome Audit --
        dossier = self.out_audit.evaluate(findings)
        dossier["case_id"] = case_id

        # Record gate outcome telemetry
        hard_count = len([f for f in findings if f.get("severity") == "HARD"])
        soft_count = len([f for f in findings if f.get("severity") == "SOFT"])
        info_count = len([f for f in findings if f.get("severity") in ("INFO", "WARNING")])
        from reas.telemetry import AuditTelemetry
        AuditTelemetry().record_gate_outcome(hard_count, soft_count, info_count)

        return dossier

def get_git_staged_files() -> List[str]:
    """Retrieves list of git-staged .json files."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True
        )
        files = [line.strip() for line in res.stdout.splitlines() if line.strip().endswith(".json")]
        return files
    except Exception as e:
        print(f"Warning: Failed to fetch git-staged files ({str(e)}).", file=sys.stderr)
        return []

def get_git_head_version(filepath: str) -> Optional[str]:
    """Retrieves file content from git HEAD."""
    try:
        res = subprocess.run(
            ["git", "show", f"HEAD:{filepath}"],
            capture_output=True, text=True
        )
        if res.returncode == 0:
            return res.stdout
        return None
    except Exception:
        return None

def find_modified_nodes(current_spec: CaseSpecification, old_spec_json: str) -> List[str]:
    """Compares current specification with old JSON string to find modified/added nodes."""
    try:
        old_data = json.loads(old_spec_json)
        old_spec = CaseSpecification(**old_data)
    except Exception:
        # Fallback to full if we can't parse old version
        return [n.node_id for n in current_spec.nodes]

    old_nodes = {n.node_id: n for n in old_spec.nodes}
    modified_node_ids = []

    for node in current_spec.nodes:
        if node.node_id not in old_nodes:
            modified_node_ids.append(node.node_id)
        else:
            old_node = old_nodes[node.node_id]
            # Check if text, status, execution binding, type, or scope differs
            if (node.claim_text != old_node.claim_text or
                node.status != old_node.status or
                node.claim_type != old_node.claim_type or
                node.execution_binding != old_node.execution_binding or
                node.scope_envelope != old_node.scope_envelope or
                node.confidence_vector != old_node.confidence_vector):
                modified_node_ids.append(node.node_id)

    # If edges changed, we should also flag their target nodes as modified
    old_edges = {(e.source_id, e.target_id): e for e in old_spec.edges}
    current_edges = {(e.source_id, e.target_id): e for e in current_spec.edges}

    for (src, tgt), edge in current_edges.items():
        if (src, tgt) not in old_edges:
            modified_node_ids.append(tgt)
        else:
            old_edge = old_edges[(src, tgt)]
            if edge.edge_type != old_edge.edge_type or edge.confidence_vector != old_edge.confidence_vector:
                modified_node_ids.append(tgt)

    return list(set(modified_node_ids))

def main():
    parser = argparse.ArgumentParser(description="Recursive Evidence-Audit System (REAS) CLI Verifier")
    parser.add_argument("case_file", nargs="?", help="Path to Case Specification JSON file")
    parser.add_argument("--canon", nargs="*", help="List of canonical reference/canon files for signature sweeps")
    parser.add_argument("--diff", action="store_true", help="Audit only git-staged spec files")
    parser.add_argument("--incremental", action="store_true", help="Evaluate only modified graph regions compared to git HEAD")
    parser.add_argument("--visualize", help="Path to export Cytoscape.js interactive HTML visualization")
    parser.add_argument("--apply-patches", action="store_true", help="Apply auto-remediation patches directly to the JSON file")
    args = parser.parse_args()

    # Determine files to audit
    files_to_audit = []
    if args.diff:
        staged_files = get_git_staged_files()
        if not staged_files:
            print("No git-staged JSON specification files found. Exiting gracefully.")
            sys.exit(0)
        files_to_audit.extend(staged_files)
    elif args.case_file:
        files_to_audit.append(args.case_file)
    else:
        print("Error: Must provide a case_file or specify --diff flag.", file=sys.stderr)
        sys.exit(1)

    overall_exit_code = 0
    dossiers = []

    for case_file in files_to_audit:
        if not os.path.exists(case_file):
            print(f"Error: Case specification file not found: {case_file}", file=sys.stderr)
            sys.exit(1)

        try:
            with open(case_file, "r") as f:
                data = json.load(f)
            case_spec = CaseSpecification(**data)
        except Exception as e:
            print(f"Error: Schema validation failed or invalid JSON in {case_file}: {str(e)}", file=sys.stderr)
            sys.exit(1)

        incremental_nodes = None
        if args.incremental:
            old_version = get_git_head_version(case_file)
            if old_version:
                incremental_nodes = find_modified_nodes(case_spec, old_version)
                print(f"Incremental mode: Auditing modified nodes {incremental_nodes}")
            else:
                print("No HEAD version found or git is not initialized. Auditing entire file.")

        engine = REASAuditEngine()
        dossier = engine.execute_audit_pipeline(case_spec, args.canon, incremental_nodes)

        # -- Auto-Patching Integration --
        # We can dynamically generate auto-remediation patches for soft findings here
        from reas.parser import generate_patches_for_dossier
        patches = generate_patches_for_dossier(case_spec, dossier)
        if patches:
            dossier["patches"] = patches
            if args.apply_patches:
                from reas.parser import apply_patches_to_file
                apply_patches_to_file(case_file, patches)
                print(f"Applied {len(patches)} auto-remediation patch(es) to {case_file}")

        # -- Visualization Export Integration --
        if args.visualize:
            from reas.telemetry import export_visualization
            export_visualization(case_spec, dossier, args.visualize)
            print(f"Exported interactive HTML visualization to {args.visualize}")

        # Accumulate results
        dossiers.append(dossier)
        if dossier["exit_code"] != 0:
            overall_exit_code = dossier["exit_code"]
        engine.tms.shutdown()

    if len(dossiers) == 1:
        print(json.dumps(dossiers[0], indent=2))
    else:
        print(json.dumps({"dossiers": dossiers, "exit_code": overall_exit_code}, indent=2))

    sys.exit(overall_exit_code)

if __name__ == "__main__":
    main()
