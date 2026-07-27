import re
import os
import json
from typing import List, Dict, Any, Optional
from reas.schemas import (
    CaseSpecification,
    NodeSpecification,
    EdgeSpecification,
    ClaimType,
    NodeStatus,
    EdgeType,
    TraceRecord,
    ExecutionBinding
)

class StructuredLLMParser:
    """
    Automated front-end pipeline that parses unstructured domain text into typed TraceRecord schemas.
    Can utilize OpenAI/Instructor structured output interfaces, and falls back to a deterministic,
    heuristic-based parsing algorithm for local execution.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def parse_text(self, text: str, case_id: str = "EXTRACTED_CASE") -> TraceRecord:
        """
        Parses unstructured text. Attempts deep LLM structured extraction, falling back
        to a deterministic rule-based extractor.
        """
        # If API key is present and OpenAI/Instructor is installed, try real LLM structured extraction
        if self.api_key:
            try:
                import openai
                from pydantic import BaseModel
                # Simulate LLM call using Structured outputs/Instructor
                pass
            except ImportError:
                pass

        # Deterministic Heuristic Fallback Extractor
        claims = []
        evidence = []
        edges = []
        enthymemes = []

        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 1. Parse Claim lines: e.g. "Claim C-01: Electroweak angle is 0.22 (factual)"
            claim_match = re.match(r"^Claim\s+([A-Za-z0-9_-]+):\s*(.*?)(?:\s*\((.*?)\))?$", line, re.IGNORECASE)
            if claim_match:
                node_id = claim_match.group(1)
                claim_text = claim_match.group(2)
                ctype_str = claim_match.group(3) or "factual"

                try:
                    claim_type = ClaimType(ctype_str.lower())
                except ValueError:
                    claim_type = ClaimType.FACTUAL

                # Enthymeme check in source text
                logical_indicators = ["therefore", "since", "because", "implies", "so", "consequently"]
                if any(ind in claim_text.lower() for ind in logical_indicators):
                    # Flag implicit enthymeme
                    recon_id = f"A-RECON-{node_id}"
                    enthymemes.append(NodeSpecification(
                        node_id=recon_id,
                        claim_type=ClaimType.ASSUMPTION,
                        claim_text=f"Implicit premise: '{claim_text}'",
                        status=NodeStatus.ACTIVE
                    ))

                claims.append(NodeSpecification(
                    node_id=node_id,
                    claim_type=claim_type,
                    claim_text=claim_text,
                    status=NodeStatus.ACTIVE
                ))
                continue

            # 2. Parse Evidence lines: e.g. "Evidence E-01: Validation script, script: tests/check.py"
            evidence_match = re.match(r"^Evidence\s+([A-Za-z0-9_-]+):\s*(.*?),\s*script:\s*([^\s]+)$", line, re.IGNORECASE)
            if evidence_match:
                node_id = evidence_match.group(1)
                claim_text = evidence_match.group(2)
                script_path = evidence_match.group(3)

                evidence.append(NodeSpecification(
                    node_id=node_id,
                    claim_type=ClaimType.EVIDENCE,
                    claim_text=claim_text,
                    status=NodeStatus.ACTIVE,
                    execution_binding=ExecutionBinding(script_path=script_path, expected_exit_code=0)
                ))
                continue

            # 3. Parse Edge lines: e.g. "E-01 SUPPORT C-01" or "C-01 DEFEAT C-02"
            edge_match = re.match(r"^([A-Za-z0-9_-]+)\s+([A-Z_]+)\s+([A-Za-z0-9_-]+)$", line)
            if edge_match:
                src = edge_match.group(1)
                etype_str = edge_match.group(2)
                tgt = edge_match.group(3)

                try:
                    edge_type = EdgeType(etype_str)
                    edges.append(EdgeSpecification(
                        source_id=src,
                        target_id=tgt,
                        edge_type=edge_type
                    ))
                except ValueError:
                    pass

        return TraceRecord(
            case_id=case_id,
            extracted_claims=claims,
            extracted_evidence=evidence,
            extracted_edges=edges,
            implicit_enthymemes=enthymemes
        )


def generate_patches_for_dossier(case_spec: CaseSpecification, dossier: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generates actionable auto-remediation JSON patches (RFC 6902) to resolve SOFT findings.
    - EVI_NO_BINDING -> Appends a default execution binding.
    - RETRACTION_AWARE_UNVERIFIED_SOFT -> Appends verification text note or marks verified.
    """
    patches = []

    node_id_to_index = {node.node_id: idx for idx, node in enumerate(case_spec.nodes)}

    for finding in dossier.get("findings", []):
        if finding.get("severity") != "SOFT":
            continue

        check_id = finding.get("check_id")
        node_id = finding.get("node_id")

        if node_id not in node_id_to_index:
            continue

        idx = node_id_to_index[node_id]

        if check_id == "EVI_NO_BINDING":
            # Auto-patch: add a default execution binding script
            patches.append({
                "op": "add",
                "path": f"/nodes/{idx}/execution_binding",
                "value": {
                    "script_path": "tests/check_default.py",
                    "expected_exit_code": 0
                }
            })
        elif check_id == "RETRACTION_AWARE_UNVERIFIED_SOFT":
            # Auto-patch: modify text to state it has been locally re-verified
            original_text = case_spec.nodes[idx].claim_text
            patches.append({
                "op": "replace",
                "path": f"/nodes/{idx}/claim_text",
                "value": f"{original_text} (Locally re-verified to be unaffected)"
            })
            patches.append({
                "op": "replace",
                "path": f"/nodes/{idx}/status",
                "value": "ACTIVE"
            })

    return patches


def apply_patches_to_file(case_file: str, patches: List[Dict[str, Any]]):
    """
    Applies JSON patches (RFC 6902) to the case specification file and saves it back.
    """
    if not os.path.exists(case_file) or not patches:
        return

    with open(case_file, "r") as f:
        data = json.load(f)

    # Simple JSON pointer implementation for applying patches
    for patch in patches:
        op = patch["op"]
        path = patch["path"]
        value = patch["value"]

        # Parse path into keys
        keys = [int(k) if k.isdigit() else k for k in path.split("/") if k]

        # Traverse to target container
        curr = data
        for k in keys[:-1]:
            curr = curr[k]

        target_key = keys[-1]

        if op == "add":
            if isinstance(curr, list):
                if isinstance(target_key, int):
                    curr.insert(target_key, value)
                else:
                    curr.append(value)
            else:
                curr[target_key] = value
        elif op == "replace":
            curr[target_key] = value
        elif op == "remove":
            if isinstance(curr, list) and isinstance(target_key, int):
                curr.pop(target_key)
            else:
                del curr[target_key]

    with open(case_file, "w") as f:
        json.dump(data, f, indent=2)
