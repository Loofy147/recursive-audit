import json
import time
from typing import Dict, Any, List, Optional
from reas.schemas import CaseSpecification

class AuditTelemetry:
    """
    Audit Telemetry & Observability Manager.
    Tracks query latencies, cache hit rates, and failure distributions.
    Supports exporting Prometheus exposition format and local JSON telemetry logs.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(AuditTelemetry, cls).__new__(cls, *args, **kwargs)
            cls._instance._init_metrics()
        return cls._instance

    def _init_metrics(self):
        self.latency_records: Dict[int, List[float]] = {1: [], 2: [], 3: [], 4: []}
        self.state_check_hits = 0
        self.state_check_misses = 0
        self.hard_failures = 0
        self.soft_failures = 0
        self.info_findings = 0

    def record_query_latency(self, tier: int, duration_seconds: float):
        if tier in self.latency_records:
            self.latency_records[tier].append(duration_seconds)

    def record_state_check(self, hit: bool):
        if hit:
            self.state_check_hits += 1
        else:
            self.state_check_misses += 1

    def record_gate_outcome(self, hard_count: int, soft_count: int, info_count: int):
        self.hard_failures += hard_count
        self.soft_failures += soft_count
        self.info_findings += info_count

    def get_metrics_summary(self) -> Dict[str, Any]:
        avg_latencies = {}
        for tier, latencies in self.latency_records.items():
            avg_latencies[f"tier_{tier}_avg_seconds"] = (
                sum(latencies) / len(latencies) if latencies else 0.0
            )

        total_checks = self.state_check_hits + self.state_check_misses
        hit_rate = (self.state_check_hits / total_checks) if total_checks > 0 else 0.0

        return {
            "query_latency_averages": avg_latencies,
            "cache_state_check": {
                "hits": self.state_check_hits,
                "misses": self.state_check_misses,
                "hit_rate": hit_rate
            },
            "gate_failures": {
                "hard_failures_total": self.hard_failures,
                "soft_failures_total": self.soft_failures,
                "info_findings_total": self.info_findings
            }
        }

    def export_prometheus_format(self) -> str:
        """
        Generates standard Prometheus exposition format text for the metrics.
        """
        summary = self.get_metrics_summary()
        lines = []

        # Latency metrics
        lines.append("# HELP reas_query_latency_seconds_average Average query latency by routing tier")
        lines.append("# TYPE reas_query_latency_seconds_average gauge")
        for tier in range(1, 5):
            val = summary["query_latency_averages"][f"tier_{tier}_avg_seconds"]
            lines.append(f'reas_query_latency_seconds_average{{tier="{tier}"}} {val}')

        # Cache metrics
        lines.append("# HELP reas_cache_hits_total Total state check cache hits")
        lines.append("# TYPE reas_cache_hits_total counter")
        lines.append(f'reas_cache_hits_total {self.state_check_hits}')

        lines.append("# HELP reas_cache_misses_total Total state check cache misses")
        lines.append("# TYPE reas_cache_misses_total counter")
        lines.append(f'reas_cache_misses_total {self.state_check_misses}')

        lines.append("# HELP reas_cache_hit_ratio State check cache hit ratio")
        lines.append("# TYPE reas_cache_hit_ratio gauge")
        lines.append(f'reas_cache_hit_ratio {summary["cache_state_check"]["hit_rate"]}')

        # Gate failure metrics
        lines.append("# HELP reas_gate_failures_total Total findings by severity gate")
        lines.append("# TYPE reas_gate_failures_total counter")
        lines.append(f'reas_gate_failures_total{{severity="hard"}} {self.hard_failures}')
        lines.append(f'reas_gate_failures_total{{severity="soft"}} {self.soft_failures}')
        lines.append(f'reas_gate_failures_total{{severity="info"}} {self.info_findings}')

        return "\n".join(lines)


def export_visualization(case_spec: CaseSpecification, dossier: Dict[str, Any], output_path: str):
    """
    Generates a self-contained, interactive Cytoscape.js HTML visual graph explorer.
    Highlights statuses, active conflict branches, and multidimensional confidence vectors.
    """
    elements = []

    # 1. Gather nodes
    for node in case_spec.nodes:
        # Get info from findings if available (e.g. if there is a soft failure or info)
        status_color = "#2ECC40"  # green (ACTIVE)
        if node.status == "RETIRED":
            status_color = "#AAAAAA"  # gray
        elif node.status == "REOPENED":
            status_color = "#FFDC00"  # yellow
        elif node.status == "CONFLATED" or node.status == "CONFLATED":
            status_color = "#FF851B"  # orange

        # Gather custom metadata for tooltip
        cv = node.confidence_vector
        cv_text = ""
        if cv:
            cv_text = (
                f"Source Credibility: {cv.source_credibility}<br/>"
                f"Empirical Grounding: {cv.empirical_grounding}<br/>"
                f"Logical Soundness: {cv.logical_soundness}<br/>"
                f"Causal Strength: {cv.causal_strength}<br/>"
                f"Temporal Stability: {cv.temporal_stability}"
            )

        elements.append({
            "data": {
                "id": node.node_id,
                "label": f"{node.node_id}\n({node.claim_type.value})",
                "status": node.status.value,
                "type": node.claim_type.value,
                "text": node.claim_text,
                "confidence": cv_text or "N/A",
                "color": status_color
            }
        })

    # 2. Gather edges
    for edge in case_spec.edges:
        edge_style = "solid"
        edge_color = "#ccc"
        if edge.edge_type == "BRANCH_CONFLICT":
            edge_style = "dashed"
            edge_color = "#FF4136"  # red
        elif edge.edge_type in ("DEFEAT", "ATTACK"):
            edge_color = "#FF851B"  # orange

        elements.append({
            "data": {
                "id": f"{edge.source_id}-{edge.target_id}",
                "source": edge.source_id,
                "target": edge.target_id,
                "type": edge.edge_type.value,
                "style": edge_style,
                "color": edge_color
            }
        })

    elements_json = json.dumps(elements, indent=2)

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>REAS Claim Graph Explorer: Case {case_spec.case_id}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.26.0/cytoscape.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f8f9fa;
            color: #212529;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: #ffffff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        header {{
            border-bottom: 2px solid #e9ecef;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}
        h1 {{
            margin: 0;
            font-size: 24px;
            color: #343a40;
        }}
        #cy {{
            width: 100%;
            height: 600px;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            background-color: #f1f3f5;
        }}
        .details-panel {{
            margin-top: 20px;
            padding: 15px;
            background-color: #e9ecef;
            border-radius: 6px;
            font-size: 14px;
        }}
        .legend {{
            display: flex;
            gap: 15px;
            margin-bottom: 15px;
            font-size: 12px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        .color-box {{
            width: 12px;
            height: 12px;
            border-radius: 3px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>REAS Claim Graph Explorer — Case: {case_spec.case_id}</h1>
        </header>

        <div class="legend">
            <strong>Node Status:</strong>
            <div class="legend-item"><div class="color-box" style="background-color: #2ECC40;"></div> Active</div>
            <div class="legend-item"><div class="color-box" style="background-color: #FFDC00;"></div> Reopened</div>
            <div class="legend-item"><div class="color-box" style="background-color: #AAAAAA;"></div> Retired</div>
            <div class="legend-item"><div class="color-box" style="background-color: #FF851B;"></div> Conflated</div>
            <strong style="margin-left: 20px;">Edge Type:</strong>
            <div class="legend-item"><span style="color: #FF4136;">-- dashed --</span> Branch Conflict</div>
            <div class="legend-item"><span style="color: #FF851B;">&mdash;&mdash;</span> Defeat/Attack</div>
        </div>

        <div id="cy"></div>

        <div class="details-panel" id="details">
            <h3>Node Inspector</h3>
            <p>Click any node or edge to inspect its full properties, scope boundaries, and confidence vectors.</p>
        </div>
    </div>

    <script>
        var cy = cytoscape({{
            container: document.getElementById('cy'),
            elements: {elements_json},
            style: [
                {{
                    selector: 'node',
                    style: {{
                        'label': 'data(id)',
                        'background-color': 'data(color)',
                        'color': '#212529',
                        'text-valign': 'center',
                        'text-halign': 'center',
                        'font-size': '12px',
                        'font-weight': 'bold',
                        'width': '60px',
                        'height': '60px',
                        'border-width': '2px',
                        'border-color': '#495057'
                    }}
                }},
                {{
                    selector: 'edge',
                    style: {{
                        'width': 3,
                        'line-style': 'data(style)',
                        'line-color': 'data(color)',
                        'target-arrow-color': 'data(color)',
                        'target-arrow-shape': 'triangle',
                        'curve-style': 'bezier',
                        'label': 'data(type)',
                        'font-size': '10px',
                        'color': '#495057',
                        'text-background-opacity': 0.7,
                        'text-background-color': '#ffffff',
                        'text-background-padding': '2px',
                        'text-background-shape': 'roundrectangle'
                    }}
                }}
            ],
            layout: {{
                name: 'cose',
                directed: true,
                padding: 30,
                idealEdgeLength: 100,
                nodeRepulsion: 400000
            }}
        }});

        cy.on('tap', 'node', function(evt){{
            var node = evt.target;
            var data = node.data();
            var detailsHtml = '<h3>Node: ' + data.id + ' (' + data.type + ')</h3>' +
                               '<p><strong>Status:</strong> ' + data.status + '</p>' +
                               '<p><strong>Proposition:</strong> ' + data.text + '</p>' +
                               '<p><strong>Confidence Vector:</strong><br/>' + data.confidence + '</p>';
            document.getElementById('details').innerHTML = detailsHtml;
        }});

        cy.on('tap', 'edge', function(evt){{
            var edge = evt.target;
            var data = edge.data();
            var detailsHtml = '<h3>Edge: ' + data.source + ' &rarr; ' + data.target + '</h3>' +
                               '<p><strong>Type:</strong> ' + data.type + '</p>';
            document.getElementById('details').innerHTML = detailsHtml;
        }});
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
