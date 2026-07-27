# Recursive Evidence-Audit System (REAS)
An enterprise-grade, graph-based epistemic verification and truth maintenance system written in Python.

REAS transitions automated verification from flat sentence-level string checking to structural audits over **Directed Claim Graphs**. It provides deterministic, bi-temporal, and multi-tier logical reasoning to maintain epistemic consistency across support networks, empirical evidence bindings, explicit scope envelopes, and qualification vectors.

---

## 🚀 Key Architectural Pillars

### 1. Production CI/CD Gateways & Developer Tooling
- **Git Pre-commit Hook:** Automatically audit staged specification files before commits. (Configured in `.pre-commit-config.yaml`).
- **GitHub Action Workflow:** Automated CI gate that blocks merges/pull requests whenever `HARD` findings are detected in any case specification. (Configured in `.github/workflows/reas-audit.yml`).
- **Diff-Aware Incremental Sweeps:** Minimize overhead on large repositories. Run `python -m reas.cli --diff` to audit only git-staged spec files, or use `--incremental` to evaluate only modified sub-regions of the graph compared to git `HEAD`.

### 2. Scalable Graph & Event-Driven Infrastructure
- **Unified Interface:** Operations are decoupled behind `BaseGraphStorage` so you can seamlessly swap between the in-memory `NetworkXGraphStorage` and high-performance, concurrent distributed backends like Neo4j via `DistributedGraphStorage`.
- **Asynchronous Propagation:** Decouple retraction propagation and re-open triggers using an event-driven `EventBus`. Setting a claim status to `RETIRED` triggers an asynchronous `RetractionEvent` handled by a background worker pool to process downstream retraction signature sweeps across bi-temporal stores.

### 3. Agentic Parser & Automated JSON Auto-Patching
- **Structured LLM Parser:** Parse unstructured domain text into typed `TraceRecord` schemas (extracting claims, evidence, edges, and enthymemes) using `StructuredLLMParser`. Integrates with Instructor/OpenAI JSON mode, falling back to a robust, deterministic, rule-based parsing engine locally.
- **Auto-Patching for Soft Findings:** Correct non-fatal `SOFT` findings automatically (e.g. appending retraction notes or missing default script execution bindings) via RFC 6902 JSON Patches. Emitted in the audit dossier and applied directly using the `--apply-patches` CLI flag.

### 4. Interactive Visualization & Telemetry Dashboard
- **Visual Graph Explorer:** Generate self-contained, interactive HTML files featuring Cytoscape.js via `--visualize <output.html>`. Visually highlights active, reopened, retired, and conflated/conflict nodes, along with active conflict branches (`BRANCH_CONFLICT`) and interactive tooltip details (confidence vectors, scope limits, and statements).
- **Audit Telemetry & Metrics:** Track query latency distributions across Tiers 1-4, cache hit ratios, and gate failure rates in real-time. Accessible via local JSON logging or exported directly as standard Prometheus exposition format.

---

## 🛠️ Usage & Installation

### Setup Environment
```bash
# Install dependencies
pip install pydantic networkx numpy
```

### CLI Options
`reas/cli.py` acts as a deterministic verifier and pipeline executor:
```bash
# General full audit of a case file
python -m reas.cli path/to/case.json --canon path/to/canon_file.txt

# Audit ONLY git-staged specification files
python -m reas.cli --diff

# Audit ONLY modified nodes/edges compared to git HEAD
python -m reas.cli path/to/case.json --incremental

# Generate interactive Cytoscape.js HTML visualization
python -m reas.cli path/to/case.json --visualize graph_dashboard.html

# Automatically apply JSON remediation patches for SOFT findings
python -m reas.cli path/to/case.json --apply-patches
```

---

## 📊 Pipeline Stages & Architecture

1. **Ingest & Construct:** Initializes Pydantic-compliant `CaseSpecification` and records active state records bi-temporally (separating transaction time from valid time) inside SQLite.
2. **Representation Audit:** Audits structural syntax, claim classifications, and handles enthymeme extraction (implicitly tracking charity delta adjustments).
3. **Evidence Audit:** Tests empirical anchoring. Enforces the "exit code 0 means verified" pattern in sandboxed execution subprocesses, alongside canonical signature grep sweeps.
4. **Dependency & Model Audit:** Identifies structural cycle loops, resolves nonmonotonic default defeaters, and checks causal rung validity (association=0.33, intervention=0.66, counterfactual=1.0).
5. **Propagation & Arbitration (TMS):** Propagates confidence vectors, isolates conflicting branches (`BRANCH_CONFLICT`), and executes recursive retraction sweeps (transitioning affected conclusions to `REOPENED`).
6. **Outcome Audit:** Separates `HARD` blocking errors from informational `SOFT` findings and controls the CLI process exit status (non-zero exits block merges on `HARD` failures).

---

## 🧪 Testing

Execute the comprehensive test suite to run unit and integration tests (including async event propagation, structured LLM parsing, JSON patch remediation, and telemetry exports):
```bash
python -m unittest tests/test_reas.py
```
