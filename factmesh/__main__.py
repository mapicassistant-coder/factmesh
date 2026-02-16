"""
FactMesh — CLI entry point.

Usage:
    python -m factmesh input/SYC2024_Staff_Report/
    python -m factmesh input/SYC2024_Staff_Report/ --llm    # LLM-enhanced resolution
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from factmesh.graph import build_graph
from factmesh.viz import render_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("factmesh")


def main():
    parser = argparse.ArgumentParser(
        prog="factmesh",
        description="FactMesh — Automated macro-consistency verification for IMF Staff Reports.",
    )
    parser.add_argument("input_dir", type=str, help="Path to pdf_engineer output directory")
    parser.add_argument("--output", type=str, default=None, help="Output directory (default: output/<report_name>/)")
    parser.add_argument("--llm", action="store_true", help="Use LLM for claim-to-cell resolution (requires OPENAI_API_KEY)")
    parser.add_argument("--api-key", type=str, default=None, help="OpenAI API key (or set OPENAI_API_KEY env var)")

    args = parser.parse_args()
    input_dir = Path(args.input_dir)

    if not input_dir.exists():
        print(f"Error: {input_dir} does not exist")
        sys.exit(1)

    claims_path = input_dir / "narrative_claims.json"
    tables_dir = input_dir / "tables"
    metadata_path = input_dir / "metadata.json"

    if not claims_path.exists():
        print(f"Error: {claims_path} not found")
        sys.exit(1)

    if not tables_dir.exists():
        print(f"Error: {tables_dir} not found")
        sys.exit(1)

    report_name = input_dir.name
    output_dir = Path(args.output) if args.output else Path("output") / report_name
    output_dir.mkdir(parents=True, exist_ok=True)

    mode_str = "LLM-enhanced" if args.llm else "Deterministic"

    logger.info("=" * 60)
    logger.info("FactMesh — Consistency Verification")
    logger.info("=" * 60)
    logger.info("Report: %s", report_name)
    logger.info("Mode:   %s", mode_str)
    logger.info("Input:  %s", input_dir)
    logger.info("Output: %s", output_dir)
    logger.info("")

    # Build graph
    logger.info("Building verification graph...")
    graph = build_graph(
        claims_path, tables_dir, metadata_path,
        use_llm=args.llm,
        api_key=args.api_key,
    )

    summary = graph.summary()
    logger.info("  Claims:        %d", summary["total_claims"])
    logger.info("  Tables:        %d", summary["total_tables"])
    logger.info("  Verifications: %d", len(graph.verifications))
    logger.info("    MATCH:         %d", summary["match"])
    logger.info("    MISMATCH:      %d", summary["mismatch"])
    logger.info("    UNVERIFIABLE:  %d", summary["unverifiable"])
    logger.info("    QUALITATIVE:   %d", summary["qualitative"])
    logger.info("  Cross-table:   %d checks (%d consistent, %d inconsistent)",
                summary["cross_table_checks"],
                summary["cross_table_consistent"],
                summary["cross_table_inconsistent"])
    logger.info("")

    # Save graph JSON
    graph_path = output_dir / "consistency_graph.json"
    with open(graph_path, "w") as f:
        json.dump(graph.to_dict(), f, indent=2)
    logger.info("Graph saved: %s", graph_path)

    # Save markdown report
    report_path = output_dir / "consistency_report.md"
    _write_markdown_report(graph, report_name, report_path)
    logger.info("Report saved: %s", report_path)

    # Save HTML dashboard
    html_path = output_dir / "verification_summary.html"
    render_html(graph, report_name, html_path)
    logger.info("Dashboard saved: %s", html_path)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Done. Open %s in a browser.", html_path)
    logger.info("=" * 60)


def _write_markdown_report(graph, report_name: str, output_path: Path):
    """Write a markdown consistency report."""
    summary = graph.summary()
    total_numeric = summary["match"] + summary["mismatch"] + summary["unverifiable"]
    match_pct = round(summary["match"] / total_numeric * 100) if total_numeric > 0 else 0

    lines = [
        f"# FactMesh Consistency Report: {report_name}\n",
        f"## Summary\n",
        f"| Metric | Count |",
        f"| --- | ---: |",
        f"| Total claims | {summary['total_claims']} |",
        f"| Tables checked | {summary['total_tables']} |",
        f"| **Verified match** | **{summary['match']}** |",
        f"| **Mismatch** | **{summary['mismatch']}** |",
        f"| Unverifiable | {summary['unverifiable']} |",
        f"| Qualitative (no numbers) | {summary['qualitative']} |",
        f"| **Match rate** | **{match_pct}%** |",
        "",
        f"## Cross-Table Consistency\n",
        f"| Metric | Count |",
        f"| --- | ---: |",
        f"| Checks performed | {summary['cross_table_checks']} |",
        f"| Consistent | {summary['cross_table_consistent']} |",
        f"| **Inconsistent** | **{summary['cross_table_inconsistent']}** |",
        "",
    ]

    # Cross-table inconsistencies
    ct_inconsistent = [c for c in graph.cross_table_checks if c.status == "INCONSISTENT"]
    if ct_inconsistent:
        lines.append("### Cross-Table Inconsistencies\n")
        for c in ct_inconsistent:
            lines.append(f"- **{c.variable}** ({c.year}): {c.detail}")
        lines.append("")

    # Mismatches
    mismatches = [v for v in graph.verifications if v.status == "MISMATCH"]
    if mismatches:
        lines.append("## Claim-Table Mismatches\n")
        for v in mismatches:
            claim_node = next((n for n in graph.nodes if n.id == v.claim_id), None)
            claim_text = claim_node.metadata.get("full_text", "") if claim_node else ""
            lines.append(f"### {v.variable} ({v.year})")
            lines.append(f"- **Claim:** \"{claim_text[:200]}\"")
            lines.append(f"- **Claim value:** {v.claim_value}")
            lines.append(f"- **Table value:** {v.table_value} ({v.table_id})")
            lines.append(f"- **Method:** {v.resolution_method}")
            lines.append(f"- **Detail:** {v.detail}")
            lines.append("")

    # LLM-resolved matches
    llm_matches = [v for v in graph.verifications if v.status == "MATCH" and v.resolution_method == "llm"]
    det_matches = [v for v in graph.verifications if v.status == "MATCH" and v.resolution_method == "deterministic"]

    if det_matches:
        lines.append(f"## Deterministic Matches ({len(det_matches)})\n")
        for v in det_matches[:20]:
            lines.append(f"- **{v.variable}** ({v.year}): claim={v.claim_value}, table={v.table_value} [{v.table_id}]")
        if len(det_matches) > 20:
            lines.append(f"- ... ({len(det_matches) - 20} more)")
        lines.append("")

    if llm_matches:
        lines.append(f"## LLM-Resolved Matches ({len(llm_matches)})\n")
        for v in llm_matches[:20]:
            lines.append(f"- **{v.variable}** ({v.year}): claim={v.claim_value}, table={v.table_value} [{v.table_id}] — {v.detail}")
        if len(llm_matches) > 20:
            lines.append(f"- ... ({len(llm_matches) - 20} more)")
        lines.append("")

    output_path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
