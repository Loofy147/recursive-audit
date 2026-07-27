import unittest
import os
import tempfile
import sys
import json
from reas.schemas import (
    CaseSpecification,
    NodeSpecification,
    EdgeSpecification,
    ConfidenceVector,
    ScopeEnvelope,
    TemporalEnvelope,
    ClaimType,
    NodeStatus,
    EdgeType,
    ExecutionBinding
)
from reas.storage import GraphStorage, StateBiTemporalStore, VectorStore
from reas.representation_audit import RepresentationAudit
from reas.evidence_audit import EvidenceAudit
from reas.dependency_audit import DependencyAudit, ModelAudit
from reas.tms import TruthMaintenanceSystem
from reas.outcome_audit import OutcomeAudit
from reas.router import QueryRouter
from reas.cli import REASAuditEngine

class TestREAS(unittest.TestCase):
    def setUp(self):
        self.db_path = ":memory:"
        self.engine = REASAuditEngine(db_path=self.db_path)

    def tearDown(self):
        if hasattr(self, "engine") and self.engine and hasattr(self.engine, "tms"):
            self.engine.tms.shutdown()

    def test_schema_parsing_and_bitemporal(self):
        # Create a valid node specification
        node = NodeSpecification(
            node_id="C-01",
            claim_type=ClaimType.FACTUAL,
            claim_text="Electroweak mixing angle is 0.22",
            status=NodeStatus.ACTIVE,
            confidence_vector=ConfidenceVector(
                source_credibility=0.9,
                empirical_grounding=0.8,
                logical_soundness=0.9,
                causal_strength=0.0,
                temporal_stability=0.9
            ),
            scope_envelope=ScopeEnvelope(
                temporal=TemporalEnvelope(valid_start="2026-01-01")
            )
        )
        self.engine.bitemporal_store.save_node_state("CASE-TEST", node)

        # Query active node state
        fetched = self.engine.bitemporal_store.get_node_state("CASE-TEST", "C-01")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.node_id, "C-01")
        self.assertEqual(fetched.claim_type, ClaimType.FACTUAL)
        self.assertAlmostEqual(fetched.confidence_vector.source_credibility, 0.9)

        # Update node state and verify non-destructive update
        node.status = NodeStatus.RETIRED
        self.engine.bitemporal_store.save_node_state("CASE-TEST", node)

        fetched_new = self.engine.bitemporal_store.get_node_state("CASE-TEST", "C-01")
        self.assertEqual(fetched_new.status, NodeStatus.RETIRED)

    def test_circular_dependency(self):
        # Seed circular dependency loop: C1 -> C2 -> C3 -> C1
        nodes = [
            NodeSpecification(node_id="C1", claim_type=ClaimType.FACTUAL, claim_text="C1 text"),
            NodeSpecification(node_id="C2", claim_type=ClaimType.FACTUAL, claim_text="C2 text"),
            NodeSpecification(node_id="C3", claim_type=ClaimType.FACTUAL, claim_text="C3 text"),
        ]
        edges = [
            EdgeSpecification(source_id="C1", target_id="C2", edge_type=EdgeType.SUPPORT),
            EdgeSpecification(source_id="C2", target_id="C3", edge_type=EdgeType.SUPPORT),
            EdgeSpecification(source_id="C3", target_id="C1", edge_type=EdgeType.SUPPORT),
        ]

        findings = self.engine.dep_audit.audit_graph("CASE-LOOP", nodes, edges)
        cycle_findings = [f for f in findings if f["check_id"] == "DEP_CIRCULAR_DEPENDENCY"]
        self.assertTrue(len(cycle_findings) > 0)
        self.assertEqual(cycle_findings[0]["severity"], "HARD")

    def test_causal_rung_mismatch(self):
        # Seed a counterfactual claim with low causal_strength (should be 1.0)
        node = NodeSpecification(
            node_id="C-CAUSAL",
            claim_type=ClaimType.COUNTERFACTUAL,
            claim_text="If A were true, B would be true.",
            confidence_vector=ConfidenceVector(causal_strength=0.5)
        )
        findings = self.engine.mod_audit.audit_nodes([node])
        mismatch = [f for f in findings if f["check_id"] == "MOD_CAUSAL_RUNG_MISMATCH"]
        self.assertTrue(len(mismatch) > 0)
        self.assertEqual(mismatch[0]["severity"], "HARD")

    def test_enthymeme_reconstruction(self):
        # Text contains logical indicator "therefore" but has no predecessors
        node = NodeSpecification(
            node_id="C-ENTHYMEME",
            claim_type=ClaimType.FACTUAL,
            claim_text="The experiment succeeded, therefore our model is correct."
        )
        res = self.engine.rep_audit.audit_node(node, [])
        findings = res["findings"]
        reconstructed = res["reconstructed_assumptions"]

        self.assertEqual(res["charity_delta"], 0.2)
        self.assertTrue(any(f["check_id"] == "REP_ENTHYMEME_DETECTED" for f in findings))
        self.assertEqual(len(reconstructed), 1)
        self.assertEqual(reconstructed[0].claim_type, ClaimType.ASSUMPTION)

    def test_subprocess_runner_and_signature_sweeper(self):
        # Create a temporary python script that exits 0
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"import sys\nsys.exit(0)\n")
            script_path = f.name

        # Test sandboxed subprocess runner
        binding = ExecutionBinding(script_path=script_path, expected_exit_code=0)
        res = self.engine.evi_audit.run_script(binding)
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)

        # Test signature sweeper (including auto-regex match)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f_txt:
            f_txt.write(b"The Weinberg angle Weinberg_Angle_Sig is verified.\n")
            canon_path = f_txt.name

        node = NodeSpecification(
            node_id="E-01",
            claim_type=ClaimType.EVIDENCE,
            claim_text="Evidence node",
            retraction_metadata={"retired_by": "Test", "reason": "Test", "signatures": ["Weinberg_Angle_[a-zA-Z]+"]}
        )
        findings = self.engine.evi_audit.audit_node(node, [canon_path])
        self.assertTrue(any(f["check_id"] == "EVI_SIGNATURE_FOUND" for f in findings))

        # Cleanup files
        os.unlink(script_path)
        os.unlink(canon_path)

    def test_truth_maintenance_retraction_propagation(self):
        # Seed a retired node and downstream dependent nodes
        # C_retired (RETIRED) -> C_unack (contains signature but doesn't acknowledge)
        # C_retired -> C_ack (contains signature and acknowledges)
        # C_retired -> K_conclusion (Conclusion node)

        nodes = [
            NodeSpecification(
                node_id="C_retired",
                claim_type=ClaimType.FACTUAL,
                claim_text="Specific physics theorem signature_xyz",
                status=NodeStatus.RETIRED,
                retraction_metadata={"retired_by": "Drift", "reason": "high energyRG limit", "signatures": ["signature_xyz"]}
            ),
            NodeSpecification(
                node_id="C_unack",
                claim_type=ClaimType.FACTUAL,
                claim_text="Relying on signature_xyz to prove our system."
            ),
            NodeSpecification(
                node_id="C_ack",
                claim_type=ClaimType.FACTUAL,
                claim_text="Acknowledged signature_xyz is retracted, but keeping restatement."
            ),
            NodeSpecification(
                node_id="K_conclusion",
                claim_type=ClaimType.CONCLUSION,
                claim_text="Overall conclusion node signature_xyz"
            )
        ]
        edges = [
            EdgeSpecification(source_id="C_retired", target_id="C_unack", edge_type=EdgeType.SUPPORT),
            EdgeSpecification(source_id="C_retired", target_id="C_ack", edge_type=EdgeType.SUPPORT),
            EdgeSpecification(source_id="C_retired", target_id="K_conclusion", edge_type=EdgeType.SUPPORT),
        ]

        findings = self.engine.tms.propagate(nodes, edges, "C_retired")

        # Verify unacknowledged is HARD finding, acknowledged is SOFT finding
        hard_finding = [f for f in findings if f["check_id"] == "RETRACTION_CONTAMINATION_HARD"]
        soft_finding = [f for f in findings if f["check_id"] == "RETRACTION_AWARE_UNVERIFIED_SOFT"]

        self.assertTrue(len(hard_finding) > 0)
        self.assertTrue(len(soft_finding) > 0)

        hard_node_ids = {f["node_id"] for f in hard_finding}
        soft_node_ids = {f["node_id"] for f in soft_finding}

        self.assertIn("C_unack", hard_node_ids)
        self.assertIn("C_ack", soft_node_ids)

        # Verify conclusion node transition to REOPENED
        conclusion_node = next(n for n in nodes if n.node_id == "K_conclusion")
        self.assertEqual(conclusion_node.status, NodeStatus.REOPENED)

    def test_branch_conflict_isolation(self):
        # Seed parallel branches with BRANCH_CONFLICT. Propagation should stop and isolate.
        nodes = [
            NodeSpecification(node_id="N1", claim_type=ClaimType.FACTUAL, claim_text="N1 text", confidence_vector=ConfidenceVector(source_credibility=1.0)),
            NodeSpecification(node_id="N2", claim_type=ClaimType.FACTUAL, claim_text="N2 text", confidence_vector=ConfidenceVector(source_credibility=0.5))
        ]
        edges = [
            EdgeSpecification(source_id="N1", target_id="N2", edge_type=EdgeType.BRANCH_CONFLICT)
        ]

        findings = self.engine.tms.propagate(nodes, edges, "N1")
        # N2's confidence shouldn't change because of branch conflict isolation
        self.assertAlmostEqual(nodes[1].confidence_vector.source_credibility, 0.5)

    def test_multi_tier_query_router(self):
        # Set up a router and test routing thresholds
        store = StateBiTemporalStore(":memory:")
        vec_store = VectorStore()
        graph_store = GraphStorage()

        # Create high-confidence node
        node_high = NodeSpecification(
            node_id="HIGH-01",
            claim_type=ClaimType.FACTUAL,
            claim_text="Extremely certain physics rule.",
            confidence_vector=ConfidenceVector(
                source_credibility=0.9, empirical_grounding=0.9, logical_soundness=0.9, causal_strength=0.9, temporal_stability=0.9
            )
        )
        store.save_node_state("CASE-ROUTING", node_high)

        # Create low-confidence node
        node_low = NodeSpecification(
            node_id="LOW-01",
            claim_type=ClaimType.FACTUAL,
            claim_text="Uncertain observation.",
            confidence_vector=ConfidenceVector(
                source_credibility=0.1, empirical_grounding=0.1, logical_soundness=0.1, causal_strength=0.1, temporal_stability=0.1
            )
        )
        store.save_node_state("CASE-ROUTING", node_low)

        # Populate vector store
        vec_store.add_document("DOC-1", "Conflict history of Weinberg angle experiments", {"type": "context"})

        router = QueryRouter(store, vec_store, graph_store)

        # 1. Tier 1 routing: Target HIGH-01 has high confidence (> 0.65)
        res_t1 = router.route_query("Tell me about HIGH-01", "CASE-ROUTING", target_node_id="HIGH-01")
        self.assertEqual(res_t1["tier"], 1)
        self.assertEqual(res_t1["route"], "Tier 1: State Check")

        # 2. Tier 2 routing: Quantitative / aggregation query
        res_t2 = router.route_query("What is the count of nodes?", "CASE-ROUTING")
        self.assertEqual(res_t2["tier"], 2)
        self.assertEqual(res_t2["route"], "Tier 2: Structured Query")

        # 3. Tier 3 routing: Episodic/conflict history with matching term
        res_t3 = router.route_query("Where is the conflict history context?", "CASE-ROUTING")
        self.assertEqual(res_t3["tier"], 3)
        self.assertEqual(res_t3["route"], "Tier 3: Episodic Fallback")

        # 4. Tier 4 routing: Low confidence node HIGH-01 query with vector similarity below threshold
        res_t4 = router.route_query("Query unrelated term", "CASE-ROUTING", target_node_id="LOW-01")
        # LOW-01 has confidence 0.1 (< 0.65), so we fall through Tier 1.
        # Query text has no structured keywords, so we fall through Tier 2.
        # Query text has no episodic keywords and similarity to DOC-1 is low, so we fall through Tier 3 to Tier 4.
        self.assertEqual(res_t4["tier"], 4)
        self.assertEqual(res_t4["route"], "Tier 4: Graph Traversal")

    def test_query_router_graph_realignment(self):
        # Verifies that QueryRouter reconstructs graph_storage correctly from StateBiTemporalStore
        store = StateBiTemporalStore(":memory:")
        vec_store = VectorStore()
        graph_store = GraphStorage()

        node_1 = NodeSpecification(node_id="T4-1", claim_type=ClaimType.FACTUAL, claim_text="T4-1 text")
        node_2 = NodeSpecification(node_id="T4-2", claim_type=ClaimType.FACTUAL, claim_text="T4-2 text")
        edge = EdgeSpecification(source_id="T4-1", target_id="T4-2", edge_type=EdgeType.SUPPORT)

        store.save_node_state("CASE-T4", node_1)
        store.save_node_state("CASE-T4", node_2)
        store.save_edge_state("CASE-T4", edge)

        router = QueryRouter(store, vec_store, graph_store)

        # Query with force_tier = 4
        res = router.route_query("Reconstruct", "CASE-T4", target_node_id="T4-1", force_tier=4)

        self.assertEqual(res["tier"], 4)
        self.assertIn("T4-2", res["neighbors"]["successors"])


    def test_asynchronous_event_driven_propagation(self):
        # Setup event-driven retraction test
        nodes = [
            NodeSpecification(
                node_id="N_ret",
                claim_type=ClaimType.FACTUAL,
                claim_text="Theorem signature_abc",
                status=NodeStatus.RETIRED,
                retraction_metadata={"retired_by": "Drift", "reason": "RG Limit", "signatures": ["signature_abc"]}
            ),
            NodeSpecification(
                node_id="N_un",
                claim_type=ClaimType.FACTUAL,
                claim_text="Uses signature_abc to prove system"
            )
        ]
        edges = [
            EdgeSpecification(source_id="N_ret", target_id="N_un", edge_type=EdgeType.SUPPORT)
        ]

        # Propagate retraction and wait slightly for async handler
        findings = self.engine.tms.propagate(nodes, edges, "N_ret", case_id="CASE-ASYNC-TEST")
        import time
        time.sleep(0.05)  # Wait for async queue to process

        # Check that async findings recorded processing
        async_processed = [f for f in self.engine.tms.async_findings if f["check_id"] == "ASYNC_RETRACTION_PROCESSED"]
        self.assertEqual(len(async_processed), 1)
        self.assertEqual(async_processed[0]["node_id"], "N_ret")
        self.assertEqual(async_processed[0]["case_id"], "CASE-ASYNC-TEST")


    def test_structured_llm_parser_and_auto_patches(self):
        from reas.parser import StructuredLLMParser, generate_patches_for_dossier
        from reas.schemas import CaseSpecification, NodeSpecification, ClaimType
        text = "Claim C-1: Weinberg angle is correct\nEvidence E-1: Weinberg script, script: tests/check_weinberg.py\nE-1 SUPPORT C-1"
        parser = StructuredLLMParser()
        record = parser.parse_text(text, "MOCK_CASE")

        self.assertEqual(len(record.extracted_claims), 1)
        self.assertEqual(len(record.extracted_evidence), 1)
        self.assertEqual(len(record.extracted_edges), 1)

        case_spec = CaseSpecification(
            case_id="MOCK_CASE_PATCH",
            nodes=[NodeSpecification(node_id="E-1", claim_type=ClaimType.EVIDENCE, claim_text="No binding")],
            edges=[]
        )
        dossier = {"findings": [{"check_id": "EVI_NO_BINDING", "node_id": "E-1", "severity": "SOFT"}]}
        patches = generate_patches_for_dossier(case_spec, dossier)
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["op"], "add")


    def test_telemetry_and_visualization_export(self):
        from reas.telemetry import AuditTelemetry, export_visualization
        from reas.schemas import CaseSpecification, NodeSpecification, ClaimType
        import tempfile
        import os

        # Test telemetry singleton
        telemetry = AuditTelemetry()
        telemetry.record_query_latency(1, 0.002)
        telemetry.record_state_check(hit=True)
        telemetry.record_gate_outcome(hard_count=0, soft_count=1, info_count=1)

        summary = telemetry.get_metrics_summary()
        self.assertIn("query_latency_averages", summary)
        self.assertIn("cache_state_check", summary)

        # Test prometheus format
        prom = telemetry.export_prometheus_format()
        self.assertIn("reas_cache_hits_total", prom)

        # Test visualization file creation
        case_spec = CaseSpecification(
            case_id="MOCK_VIZ",
            nodes=[NodeSpecification(node_id="N1", claim_type=ClaimType.FACTUAL, claim_text="test text")],
            edges=[]
        )
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            export_visualization(case_spec, {}, tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, "r", encoding="utf-8") as f:
                html = f.read()
            self.assertIn("MOCK_VIZ", html)
            self.assertIn("test text", html)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

if __name__ == "__main__":
    unittest.main()
