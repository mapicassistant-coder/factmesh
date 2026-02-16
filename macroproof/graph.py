"""
MacroProof — Graph builder.

Constructs a verification graph linking narrative claims to table cells.
LLM normalizes variable names; verification is deterministic.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger("macroproof.graph")


# --- Schemas ---

@dataclass
class GraphNode:
    id: str
    type: str  # "claim", "table", "variable", "cell"
    label: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str  # "references", "mentions_variable", "contains_variable", "verified_by"
    metadata: dict = field(default_factory=dict)


@dataclass
class VerificationResult:
    claim_id: str
    status: str  # "MATCH", "MISMATCH", "UNVERIFIABLE", "QUALITATIVE"
    claim_value: str | None = None
    table_value: str | None = None
    table_id: str | None = None
    variable: str | None = None
    year: str | None = None
    detail: str = ""


@dataclass
class ConsistencyGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    verifications: list[VerificationResult] = field(default_factory=list)

    def add_node(self, node: GraphNode):
        self.nodes.append(node)

    def add_edge(self, edge: GraphEdge):
        self.edges.append(edge)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "verifications": [asdict(v) for v in self.verifications],
            "summary": self.summary(),
        }

    def summary(self) -> dict:
        statuses = [v.status for v in self.verifications]
        return {
            "total_claims": len([n for n in self.nodes if n.type == "claim"]),
            "total_tables": len([n for n in self.nodes if n.type == "table"]),
            "verifications": len(self.verifications),
            "match": statuses.count("MATCH"),
            "mismatch": statuses.count("MISMATCH"),
            "unverifiable": statuses.count("UNVERIFIABLE"),
            "qualitative": statuses.count("QUALITATIVE"),
        }


# --- Cell lookup (deterministic) ---

def _normalize_number(s: str) -> float | None:
    """Parse a number string, handling commas, negatives, percentages."""
    if not s or s.strip() in ("", "...", "—", "n.a.", "n/a"):
        return None
    s = s.strip().replace(",", "").replace("%", "").replace(" ", "")
    # Handle parenthetical negatives: (3.2) → -3.2
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _numbers_match(a: str, b: str, tolerance: float = 0.15) -> bool:
    """Check if two number strings represent the same value within tolerance."""
    na = _normalize_number(a)
    nb = _normalize_number(b)
    if na is None or nb is None:
        return False
    return abs(na - nb) <= tolerance


def _find_value_in_table(
    table_data: dict,
    variable_hint: str,
    year: str | None,
    target_value: str,
) -> tuple[str | None, str | None, str | None]:
    """
    Search a table for a value matching the claim.

    Returns (found_value, matched_row, matched_col) or (None, None, None).
    """
    data = table_data.get("data", {})
    if not data:
        return None, None, None

    variable_lower = variable_hint.lower()

    for row_label, row_data in data.items():
        row_lower = row_label.lower()

        # Check if row label is related to the variable
        # Simple keyword matching — not perfect but good enough for MVP
        row_relevant = any(
            kw in row_lower
            for kw in _variable_keywords(variable_lower)
        )

        if not row_relevant:
            continue

        if isinstance(row_data, dict):
            # Try year-specific lookup first
            if year and year in row_data:
                cell_val = str(row_data[year])
                if _numbers_match(target_value, cell_val):
                    return cell_val, row_label, year

            # Try all columns
            for col, cell_val in row_data.items():
                cell_str = str(cell_val)
                if _numbers_match(target_value, cell_str):
                    return cell_str, row_label, col

    # Broader search: check every cell regardless of row relevance
    for row_label, row_data in data.items():
        if isinstance(row_data, dict):
            for col, cell_val in row_data.items():
                cell_str = str(cell_val)
                if _numbers_match(target_value, cell_str):
                    # Only match if year matches (if provided)
                    if year and year == col:
                        return cell_str, row_label, col

    return None, None, None


def _variable_keywords(variable: str) -> list[str]:
    """Extract search keywords from a variable name."""
    # Map canonical variable names to table row keywords
    keyword_map = {
        "real_gdp": ["real gdp", "gdp growth", "gdp, real", "real gross domestic"],
        "inflation": ["inflation", "consumer price", "cpi", "price index"],
        "fiscal": ["fiscal", "overall balance", "primary balance", "budget"],
        "current_account": ["current account", "external current"],
        "debt": ["debt", "gross debt", "public debt", "government debt"],
        "revenue": ["revenue", "total revenue", "government revenue"],
        "expenditure": ["expenditure", "total expenditure", "government spending"],
        "reserves": ["reserves", "international reserves", "gross reserves"],
        "exchange": ["exchange rate", "nominal exchange", "real exchange"],
        "interest": ["interest rate", "policy rate", "monetary policy"],
        "unemployment": ["unemployment", "employment"],
        "exports": ["exports", "total exports"],
        "imports": ["imports", "total imports"],
        "money": ["money supply", "broad money", "m2", "m3"],
        "credit": ["credit", "private sector credit"],
    }

    variable_lower = variable.lower().replace("_", " ")
    for key, keywords in keyword_map.items():
        if key in variable_lower or any(kw in variable_lower for kw in keywords):
            return keywords

    # Fallback: split variable name into words
    words = re.split(r'[_\s]+', variable_lower)
    return [w for w in words if len(w) > 2]


# --- Graph construction ---

def build_graph(
    claims_path: Path,
    tables_dir: Path,
    metadata_path: Path | None = None,
) -> ConsistencyGraph:
    """
    Build a consistency verification graph from pdf_engineer output.

    1. Load claims and tables
    2. For each claim with numeric values:
       a. Find the likely table
       b. Search for matching cells
       c. Compare values
    3. Build graph with typed edges
    """
    graph = ConsistencyGraph()

    # Load claims
    with open(claims_path) as f:
        claims = json.load(f)

    # Load all tables
    tables = {}
    for table_file in sorted(tables_dir.glob("*.json")):
        with open(table_file) as f:
            table_data = json.load(f)
            table_id = table_data.get("table_id", table_file.stem)
            tables[table_id] = table_data

    # Add table nodes
    for table_id, table_data in tables.items():
        graph.add_node(GraphNode(
            id=table_id,
            type="table",
            label=table_data.get("table_title", table_id),
            metadata={
                "page": table_data.get("page_num"),
                "columns": table_data.get("columns", []),
                "units": table_data.get("units", ""),
            },
        ))

    # Process each claim
    for i, claim in enumerate(claims):
        claim_id = f"claim_{i}"
        claim_text = claim.get("claim_text", "")
        likely_table = claim.get("likely_table", "unknown")
        values = claim.get("values_mentioned", [])
        page = claim.get("page_or_section", "unknown")

        # Add claim node
        graph.add_node(GraphNode(
            id=claim_id,
            type="claim",
            label=claim_text[:120],
            metadata={
                "full_text": claim_text,
                "page": page,
                "likely_table": likely_table,
                "variables": claim.get("variables_referenced", []),
            },
        ))

        # Link claim to its likely table
        if likely_table != "unknown" and likely_table in tables:
            graph.add_edge(GraphEdge(
                source=claim_id,
                target=likely_table,
                type="references",
            ))

        # Qualitative claims (no numbers)
        if not values:
            graph.verifications.append(VerificationResult(
                claim_id=claim_id,
                status="QUALITATIVE",
                detail="No numeric values to verify",
            ))
            continue

        # For each value mentioned, try to verify
        for val in values:
            variable = val.get("variable", "")
            value_str = val.get("value", "")
            year = val.get("year", "unknown")

            if not value_str or value_str in ("unknown",):
                continue

            # Add variable node (dedup by name)
            var_node_id = f"var_{variable}"
            if not any(n.id == var_node_id for n in graph.nodes):
                graph.add_node(GraphNode(
                    id=var_node_id,
                    type="variable",
                    label=variable,
                ))

            graph.add_edge(GraphEdge(
                source=claim_id,
                target=var_node_id,
                type="mentions_variable",
                metadata={"value": value_str, "year": year},
            ))

            # Search for value in tables
            found = False

            # Priority 1: search likely table
            search_tables = []
            if likely_table != "unknown" and likely_table in tables:
                search_tables.append((likely_table, tables[likely_table]))

            # Priority 2: search all tables
            for tid, tdata in tables.items():
                if tid != likely_table:
                    search_tables.append((tid, tdata))

            for table_id, table_data in search_tables:
                cell_value, matched_row, matched_col = _find_value_in_table(
                    table_data, variable, year if year != "unknown" else None, value_str
                )

                if cell_value is not None:
                    # Found it — create cell node and verify
                    cell_id = f"cell_{table_id}_{matched_row}_{matched_col}".replace(" ", "_")[:80]

                    if not any(n.id == cell_id for n in graph.nodes):
                        graph.add_node(GraphNode(
                            id=cell_id,
                            type="cell",
                            label=f"{matched_row} / {matched_col} = {cell_value}",
                            metadata={
                                "table_id": table_id,
                                "row": matched_row,
                                "col": matched_col,
                                "value": cell_value,
                            },
                        ))

                    graph.add_edge(GraphEdge(
                        source=table_id,
                        target=cell_id,
                        type="contains_cell",
                    ))

                    if _numbers_match(value_str, cell_value):
                        status = "MATCH"
                    else:
                        status = "MISMATCH"

                    graph.add_edge(GraphEdge(
                        source=claim_id,
                        target=cell_id,
                        type="verified_by",
                        metadata={"status": status},
                    ))

                    graph.verifications.append(VerificationResult(
                        claim_id=claim_id,
                        status=status,
                        claim_value=value_str,
                        table_value=cell_value,
                        table_id=table_id,
                        variable=variable,
                        year=year,
                        detail=f"Claim: {value_str} | Table: {cell_value} ({matched_row} / {matched_col})",
                    ))
                    found = True
                    break  # Found in this table, stop searching

            if not found:
                graph.verifications.append(VerificationResult(
                    claim_id=claim_id,
                    status="UNVERIFIABLE",
                    claim_value=value_str,
                    variable=variable,
                    year=year,
                    detail=f"Value {value_str} for {variable} ({year}) not found in any table",
                ))

    return graph
