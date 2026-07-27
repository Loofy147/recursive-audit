from typing import List, Dict, Any

class OutcomeAudit:
    """
    Outcome Audit Module:
    - Evaluates itemized findings without collapsing them.
    - Categorizes into HARD vs SOFT severity levels.
    - Emits detailed itemized dossier.
    - Controls process exit status (non-zero for any HARD finding).
    """
    def __init__(self):
        pass

    def evaluate(self, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Evaluates compiled findings and decides whether to gate/fail.
        """
        hard_findings = [f for f in findings if f.get("severity") == "HARD"]
        soft_findings = [f for f in findings if f.get("severity") == "SOFT"]
        info_findings = [f for f in findings if f.get("severity") in ("INFO", "WARNING")]

        passed = len(hard_findings) == 0
        exit_code = 0 if passed else 1

        dossier = {
            "passed": passed,
            "exit_code": exit_code,
            "summary": {
                "total_findings": len(findings),
                "hard_count": len(hard_findings),
                "soft_count": len(soft_findings),
                "info_count": len(info_findings)
            },
            "findings": findings
        }

        return dossier
