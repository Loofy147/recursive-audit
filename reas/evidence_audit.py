import sys
import subprocess
import re
import os
from typing import List, Dict, Any, Optional
from reas.schemas import NodeSpecification, ExecutionBinding, ClaimType

class EvidenceAudit:
    """
    Tests the empirical ground-truth anchoring of evidence nodes.
    - Executable scripts must terminate with exit code 0.
    - Signature grep matching ensures target text/math exists verbatim or as a regex pattern in canon.
    """
    def __init__(self, canon_dir: Optional[str] = None):
        self.canon_dir = canon_dir

    def run_script(self, binding: ExecutionBinding) -> Dict[str, Any]:
        """
        Executes an external script and verifies its exit code.
        Uses current Python interpreter for script executions.
        """
        script_path = binding.script_path
        expected = binding.expected_exit_code

        # If it doesn't exist, return failure
        if not os.path.exists(script_path):
            return {
                "success": False,
                "exit_code": -1,
                "error": f"Script path does not exist: {script_path}"
            }

        try:
            if script_path.endswith(".py"):
                cmd = [sys.executable, script_path]
            else:
                cmd = [script_path]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            success = (result.returncode == expected)
            return {
                "success": success,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": None if success else f"Exit code {result.returncode} does not match expected {expected}"
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -2,
                "error": "Script execution timed out after 10 seconds"
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -3,
                "error": f"Subprocess error: {str(e)}"
            }

    def grep_signature(self, signature: str, file_path: str, is_regex: bool = False) -> bool:
        """
        Checks if a signature or regex pattern exists in a target file.
        """
        if not os.path.exists(file_path):
            return False

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if is_regex:
                return re.search(signature, content) is not None
            else:
                return signature in content
        except Exception:
            return False

    def audit_node(self, node: NodeSpecification, canon_files: List[str]) -> List[Dict[str, Any]]:
        findings = []

        if node.claim_type == ClaimType.EVIDENCE:
            if node.execution_binding:
                res = self.run_script(node.execution_binding)
                if not res["success"]:
                    findings.append({
                        "check_id": "EVI_SCRIPT_FAILED",
                        "node_id": node.node_id,
                        "severity": "HARD",
                        "message": f"Evidence check script failed: {res['error']}. Path: {node.execution_binding.script_path}"
                    })
                else:
                    findings.append({
                        "check_id": "EVI_SCRIPT_PASSED",
                        "node_id": node.node_id,
                        "severity": "INFO",
                        "message": f"Evidence check script executed successfully with exit code {res['exit_code']}."
                    })
            else:
                findings.append({
                    "check_id": "EVI_NO_BINDING",
                    "node_id": node.node_id,
                    "severity": "SOFT",
                    "message": f"Evidence node {node.node_id} has no execution binding."
                })

        # Signature matching for retired or referenced signatures
        if node.retraction_metadata and node.retraction_metadata.signatures:
            for sig in node.retraction_metadata.signatures:
                # Dynamically detect if signature contains regex special characters
                is_rx = any(char in sig for char in ".*+?^${}()|[]")
                found = False
                for cf in canon_files:
                    if self.grep_signature(sig, cf, is_regex=is_rx):
                        found = True
                        break

                if found:
                    findings.append({
                        "check_id": "EVI_SIGNATURE_FOUND",
                        "node_id": node.node_id,
                        "severity": "INFO",
                        "message": f"Signature '{sig}' found in canon."
                    })
                else:
                    findings.append({
                        "check_id": "EVI_SIGNATURE_MISSING",
                        "node_id": node.node_id,
                        "severity": "SOFT",
                        "message": f"Citing signature '{sig}' was not found verbatim or via pattern match in any canonical files."
                    })

        return findings
