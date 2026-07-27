from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from enum import Enum

class NodeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    REOPENED = "REOPENED"
    SETTLED = "SETTLED"
    CONFLATED = "CONFLATED"

class EdgeType(str, Enum):
    SUPPORT = "SUPPORT"
    REFINE = "REFINE"
    SUPERSEDE = "SUPERSEDE"
    BRANCH_CONFLICT = "BRANCH_CONFLICT"
    DEFEAT = "DEFEAT"
    ATTACK = "ATTACK"

class ClaimType(str, Enum):
    FACTUAL = "factual"
    DEFINITIONAL = "definitional"
    CAUSAL = "causal"
    COUNTERFACTUAL = "counterfactual"
    PREDICTIVE = "predictive"
    DIAGNOSTIC = "diagnostic"
    NORMATIVE = "normative"
    LEGAL = "legal"
    POLICY = "policy"

    # Primitive classifications
    ASSUMPTION = "assumption"
    DEFINITION = "definition"
    EVIDENCE = "evidence"
    CONCLUSION = "conclusion"

class ConfidenceVector(BaseModel):
    source_credibility: float = Field(0.0, ge=0.0, le=1.0)
    empirical_grounding: float = Field(0.0, ge=0.0, le=1.0)
    logical_soundness: float = Field(0.0, ge=0.0, le=1.0)
    causal_strength: float = Field(0.0, ge=0.0, le=1.0)
    temporal_stability: float = Field(0.0, ge=0.0, le=1.0)

    class Config:
        populate_by_name = True

class TemporalEnvelope(BaseModel):
    valid_start: Optional[str] = None
    valid_end: Optional[str] = None
    transaction_start: Optional[str] = None
    transaction_end: Optional[str] = None

class ScopeEnvelope(BaseModel):
    temporal: Optional[TemporalEnvelope] = None
    spatial: Optional[str] = None
    institutional: Optional[str] = None
    parametric: Optional[Dict[str, Any]] = None
    value_frame: Optional[str] = None

class RetractionMetadata(BaseModel):
    retired_by: str
    reason: str
    signatures: List[str]

class ExecutionBinding(BaseModel):
    script_path: str
    expected_exit_code: int = 0

class NodeSpecification(BaseModel):
    node_id: str
    claim_type: ClaimType
    claim_text: str
    status: NodeStatus = NodeStatus.ACTIVE
    retraction_metadata: Optional[RetractionMetadata] = None
    execution_binding: Optional[ExecutionBinding] = None
    scope_envelope: Optional[ScopeEnvelope] = None
    confidence_vector: Optional[ConfidenceVector] = None

class EdgeSpecification(BaseModel):
    source_id: str
    target_id: str
    edge_type: EdgeType
    confidence_vector: Optional[ConfidenceVector] = None

class CaseSpecification(BaseModel):
    case_id: str
    schema_version: str = "1.0.0"
    scope_envelope: Optional[ScopeEnvelope] = None
    nodes: List[NodeSpecification]
    edges: List[EdgeSpecification]
