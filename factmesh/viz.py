"""
FactMesh — HTML visualization for verification results.

Generates a single-page HTML dashboard showing the consistency graph.
"""

import json
from pathlib import Path

from factmesh.graph import ConsistencyGraph


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FactMesh — {report_name}</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f6fa; color: #2d3436; padding: 20px; }
  .container { max-width: 1200px; margin: 0 auto; }
  h1 { font-size: 1.8rem; margin-bottom: 8px; color: #0a3d62; }
  .subtitle { color: #636e72; margin-bottom: 24px; font-size: 0.95rem; }
  .summary-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
  .summary-card { background: white; border-radius: 12px; padding: 20px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  .summary-card .number { font-size: 2.4rem; font-weight: 700; }
  .summary-card .label { font-size: 0.85rem; color: #636e72; margin-top: 4px; }
  .match .number { color: #00b894; }
  .mismatch .number { color: #d63031; }
  .unverifiable .number { color: #fdcb6e; }
  .qualitative .number { color: #74b9ff; }
  .section { background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  .section h2 { font-size: 1.2rem; margin-bottom: 16px; color: #0a3d62; border-bottom: 2px solid #dfe6e9; padding-bottom: 8px; }
  .verification { padding: 12px 16px; border-left: 4px solid; margin-bottom: 8px; border-radius: 0 8px 8px 0; background: #fafafa; }
  .verification.MATCH { border-color: #00b894; }
  .verification.MISMATCH { border-color: #d63031; background: #fff5f5; }
  .verification.UNVERIFIABLE { border-color: #fdcb6e; }
  .verification.QUALITATIVE { border-color: #74b9ff; opacity: 0.7; }
  .verification .status { font-weight: 700; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .verification.MATCH .status { color: #00b894; }
  .verification.MISMATCH .status { color: #d63031; }
  .verification.UNVERIFIABLE .status { color: #e17055; }
  .verification.QUALITATIVE .status { color: #74b9ff; }
  .verification .claim { margin-top: 4px; font-size: 0.9rem; color: #2d3436; }
  .verification .detail { margin-top: 4px; font-size: 0.8rem; color: #636e72; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; background: #dfe6e9; color: #636e72; margin-right: 4px; }
  .progress-bar { height: 32px; border-radius: 8px; overflow: hidden; display: flex; margin-bottom: 8px; }
  .progress-bar .segment { display: flex; align-items: center; justify-content: center; color: white; font-size: 0.8rem; font-weight: 600; }
  .progress-bar .segment.match { background: #00b894; }
  .progress-bar .segment.mismatch { background: #d63031; }
  .progress-bar .segment.unverifiable { background: #fdcb6e; color: #2d3436; }
  .progress-bar .segment.qualitative { background: #74b9ff; }
  .graph-section { margin-top: 16px; }
  .edge-list { font-size: 0.8rem; color: #636e72; }
  .edge-list li { margin-bottom: 2px; }
  .filter-bar { margin-bottom: 16px; display: flex; gap: 8px; }
  .filter-btn { padding: 6px 16px; border-radius: 20px; border: 1px solid #dfe6e9; background: white; cursor: pointer; font-size: 0.85rem; transition: all 0.2s; }
  .filter-btn:hover, .filter-btn.active { background: #0a3d62; color: white; border-color: #0a3d62; }
</style>
</head>
<body>
<div class="container">
  <h1>FactMesh Verification Report</h1>
  <p class="subtitle">{report_name} — {total_claims} claims verified against {total_tables} tables</p>

  <div class="summary-grid">
    <div class="summary-card match">
      <div class="number">{match_count}</div>
      <div class="label">Verified Match</div>
    </div>
    <div class="summary-card mismatch">
      <div class="number">{mismatch_count}</div>
      <div class="label">Mismatch</div>
    </div>
    <div class="summary-card unverifiable">
      <div class="number">{unverifiable_count}</div>
      <div class="label">Unverifiable</div>
    </div>
    <div class="summary-card qualitative">
      <div class="number">{qualitative_count}</div>
      <div class="label">Qualitative</div>
    </div>
  </div>

  <div class="section">
    <h2>Verification Coverage</h2>
    <div class="progress-bar">
      {progress_segments}
    </div>
    <p style="font-size: 0.85rem; color: #636e72; margin-top: 8px;">
      {match_pct}% of numeric claims verified against source tables
    </p>
  </div>

  {mismatch_section}

  <div class="section">
    <h2>All Verifications</h2>
    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterResults('all')">All ({total_verifications})</button>
      <button class="filter-btn" onclick="filterResults('MATCH')">Match ({match_count})</button>
      <button class="filter-btn" onclick="filterResults('MISMATCH')">Mismatch ({mismatch_count})</button>
      <button class="filter-btn" onclick="filterResults('UNVERIFIABLE')">Unverifiable ({unverifiable_count})</button>
      <button class="filter-btn" onclick="filterResults('QUALITATIVE')">Qualitative ({qualitative_count})</button>
    </div>
    {verification_items}
  </div>

  <div class="section">
    <h2>Graph Statistics</h2>
    <p style="font-size: 0.9rem; color: #636e72;">
      {node_count} nodes ({claim_nodes} claims, {table_nodes} tables, {var_nodes} variables, {cell_nodes} cells)
      &middot; {edge_count} edges
    </p>
  </div>
</div>

<script>
function filterResults(status) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.verification').forEach(el => {
    if (status === 'all' || el.classList.contains(status)) {
      el.style.display = '';
    } else {
      el.style.display = 'none';
    }
  });
}
</script>
</body>
</html>"""


def render_html(
    graph: ConsistencyGraph,
    report_name: str,
    output_path: Path,
) -> Path:
    """Render the verification dashboard as HTML."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = graph.summary()
    total_numeric = summary["match"] + summary["mismatch"] + summary["unverifiable"]
    match_pct = round(summary["match"] / total_numeric * 100) if total_numeric > 0 else 0

    # Progress bar segments
    segments = []
    total_v = len(graph.verifications)
    for status, cls in [("match", "match"), ("mismatch", "mismatch"), ("unverifiable", "unverifiable"), ("qualitative", "qualitative")]:
        count = summary[status]
        if count > 0:
            pct = count / total_v * 100
            segments.append(f'<div class="segment {cls}" style="width:{pct}%">{count}</div>')

    # Mismatch section (highlighted)
    mismatches = [v for v in graph.verifications if v.status == "MISMATCH"]
    mismatch_section = ""
    if mismatches:
        mismatch_items = []
        for v in mismatches:
            claim_node = next((n for n in graph.nodes if n.id == v.claim_id), None)
            claim_text = claim_node.metadata.get("full_text", claim_node.label) if claim_node else v.claim_id
            mismatch_items.append(f'''
            <div class="verification MISMATCH">
              <div class="status">MISMATCH</div>
              <div class="claim">"{claim_text[:200]}"</div>
              <div class="detail">
                <span class="tag">{v.variable}</span>
                <span class="tag">{v.year}</span>
                <span class="tag">{v.table_id}</span>
                Claim says <b>{v.claim_value}</b>, table shows <b>{v.table_value}</b>
              </div>
            </div>''')
        mismatch_section = f'''
        <div class="section" style="border: 2px solid #d63031;">
          <h2 style="color: #d63031;">Mismatches Requiring Attention ({len(mismatches)})</h2>
          {"".join(mismatch_items)}
        </div>'''

    # All verification items
    v_items = []
    for v in graph.verifications:
        claim_node = next((n for n in graph.nodes if n.id == v.claim_id), None)
        claim_text = claim_node.metadata.get("full_text", claim_node.label) if claim_node else v.claim_id
        page = claim_node.metadata.get("page", "") if claim_node else ""

        tags = []
        if v.variable:
            tags.append(f'<span class="tag">{v.variable}</span>')
        if v.year and v.year != "unknown":
            tags.append(f'<span class="tag">{v.year}</span>')
        if v.table_id:
            tags.append(f'<span class="tag">{v.table_id}</span>')
        if page:
            tags.append(f'<span class="tag">{page}</span>')

        v_items.append(f'''
        <div class="verification {v.status}">
          <div class="status">{v.status}</div>
          <div class="claim">"{claim_text[:200]}"</div>
          <div class="detail">{"".join(tags)} {v.detail}</div>
        </div>''')

    # Node counts
    node_types = {}
    for n in graph.nodes:
        node_types[n.type] = node_types.get(n.type, 0) + 1

    replacements = {
        "{report_name}": report_name,
        "{total_claims}": str(summary["total_claims"]),
        "{total_tables}": str(summary["total_tables"]),
        "{match_count}": str(summary["match"]),
        "{mismatch_count}": str(summary["mismatch"]),
        "{unverifiable_count}": str(summary["unverifiable"]),
        "{qualitative_count}": str(summary["qualitative"]),
        "{total_verifications}": str(len(graph.verifications)),
        "{progress_segments}": "".join(segments),
        "{match_pct}": str(match_pct),
        "{mismatch_section}": mismatch_section,
        "{verification_items}": "".join(v_items),
        "{node_count}": str(len(graph.nodes)),
        "{claim_nodes}": str(node_types.get("claim", 0)),
        "{table_nodes}": str(node_types.get("table", 0)),
        "{var_nodes}": str(node_types.get("variable", 0)),
        "{cell_nodes}": str(node_types.get("cell", 0)),
        "{edge_count}": str(len(graph.edges)),
    }
    html = HTML_TEMPLATE
    for key, val in replacements.items():
        html = html.replace(key, val)

    output_path.write_text(html)
    logger.info("HTML report saved: %s", output_path)
    return output_path


# Need logger
import logging
logger = logging.getLogger("factmesh.viz")
