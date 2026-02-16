"""
FactMesh — Graph builder.

Constructs a verification graph linking narrative claims to table cells.
Supports two modes:
  - Deterministic (default): keyword matching, zero LLM calls
  - LLM-enhanced (--llm): uses OpenAI to resolve ambiguous matches

Also performs cross-table consistency checks.
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("factmesh.graph")


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
    type: str  # "references", "mentions_variable", "contains_cell", "verified_by", "cross_table"
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
    resolution_method: str = "deterministic"  # "deterministic" or "llm"


@dataclass
class CrossTableResult:
    """Result of checking the same variable across multiple tables."""
    variable: str
    year: str
    entries: list[dict]  # [{"table_id": ..., "row": ..., "col": ..., "value": ...}]
    status: str  # "CONSISTENT", "INCONSISTENT"
    detail: str = ""


@dataclass
class ConsistencyGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    verifications: list[VerificationResult] = field(default_factory=list)
    cross_table_checks: list[CrossTableResult] = field(default_factory=list)

    def add_node(self, node: GraphNode):
        self.nodes.append(node)

    def add_edge(self, edge: GraphEdge):
        self.edges.append(edge)

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "verifications": [asdict(v) for v in self.verifications],
            "cross_table_checks": [asdict(c) for c in self.cross_table_checks],
            "summary": self.summary(),
        }

    def summary(self) -> dict:
        statuses = [v.status for v in self.verifications]
        ct_statuses = [c.status for c in self.cross_table_checks]
        return {
            "total_claims": len([n for n in self.nodes if n.type == "claim"]),
            "total_tables": len([n for n in self.nodes if n.type == "table"]),
            "verifications": len(self.verifications),
            "match": statuses.count("MATCH"),
            "mismatch": statuses.count("MISMATCH"),
            "unverifiable": statuses.count("UNVERIFIABLE"),
            "qualitative": statuses.count("QUALITATIVE"),
            "cross_table_checks": len(self.cross_table_checks),
            "cross_table_consistent": ct_statuses.count("CONSISTENT"),
            "cross_table_inconsistent": ct_statuses.count("INCONSISTENT"),
        }


# --- Number utilities ---

def _normalize_number(s: str) -> float | None:
    """Parse a number string, handling commas, negatives, percentages."""
    if not s or s.strip() in ("", "...", "—", "n.a.", "n/a"):
        return None
    s = s.strip().replace(",", "").replace("%", "").replace(" ", "")
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


# --- Deterministic cell lookup ---

def _find_value_in_table(
    table_data: dict,
    variable_hint: str,
    year: str | None,
    target_value: str,
) -> tuple[str | None, str | None, str | None]:
    """Search a table for a value matching the claim. Returns (value, row, col) or (None, None, None)."""
    data = table_data.get("data", {})
    if not data:
        return None, None, None

    variable_lower = variable_hint.lower()
    keywords = _variable_keywords(variable_lower)

    # Pass 1: keyword-matched rows
    for row_label, row_data in data.items():
        row_lower = row_label.lower()
        if not any(kw in row_lower for kw in keywords):
            continue
        if isinstance(row_data, dict):
            # Try year-specific first (with column suffix variations)
            if year:
                for col, cell_val in row_data.items():
                    col_year = _extract_year_from_col(col)
                    if col_year == year and _numbers_match(target_value, str(cell_val)):
                        return str(cell_val), row_label, col
            # Try all columns
            for col, cell_val in row_data.items():
                if _numbers_match(target_value, str(cell_val)):
                    return str(cell_val), row_label, col

    # Pass 2: year-matched columns only (any row)
    if year:
        for row_label, row_data in data.items():
            if isinstance(row_data, dict):
                for col, cell_val in row_data.items():
                    col_year = _extract_year_from_col(col)
                    if col_year == year and _numbers_match(target_value, str(cell_val)):
                        return str(cell_val), row_label, col

    return None, None, None


def _extract_year_from_col(col: str) -> str | None:
    """Extract year from column labels like '2023', '2023_Proj.', '2023_Prel.'"""
    m = re.match(r'(\d{4})', str(col))
    return m.group(1) if m else None


def _variable_keywords(variable: str) -> list[str]:
    """Extract search keywords from a variable name."""
    keyword_map = {
        "real_gdp": ["real gdp", "gdp growth", "gdp, real", "real gross domestic"],
        "inflation": ["inflation", "consumer price", "cpi", "price index"],
        "fiscal": ["fiscal", "overall balance", "primary balance", "budget"],
        "current_account": ["current account", "external current"],
        "debt": ["debt", "gross debt", "public debt", "government debt"],
        "revenue": ["revenue", "total revenue", "government revenue"],
        "expenditure": ["expenditure", "total expenditure", "government spending"],
        "reserves": ["reserves", "international reserves", "gross reserves"],
        "exchange": ["exchange rate", "nominal exchange", "real exchange", "neer", "reer"],
        "interest": ["interest rate", "policy rate", "monetary policy"],
        "unemployment": ["unemployment", "employment"],
        "exports": ["exports", "total exports"],
        "imports": ["imports", "total imports"],
        "money": ["money supply", "broad money", "m2", "m3"],
        "credit": ["credit", "private sector credit", "private credit"],
        "lending": ["lending", "lending rate"],
    }

    variable_lower = variable.lower().replace("_", " ")
    for key, keywords in keyword_map.items():
        if key in variable_lower or any(kw in variable_lower for kw in keywords):
            return keywords

    words = re.split(r'[_\s]+', variable_lower)
    return [w for w in words if len(w) > 2]


# --- Cross-table consistency ---

def _check_cross_table_consistency(
    tables: dict[str, dict],
    tolerance: float = 0.15,
) -> list[CrossTableResult]:
    """
    Find variables that appear in multiple tables and check if values agree.

    Identifies rows with similar names across tables and compares
    values for the same year columns.
    """
    results = []

    # Build index: normalized_row_name → [(table_id, row_label, row_data)]
    row_index: dict[str, list[tuple[str, str, dict]]] = {}
    for table_id, table_data in tables.items():
        data = table_data.get("data", {})
        for row_label, row_data in data.items():
            if not isinstance(row_data, dict):
                continue
            norm_name = _normalize_row_name(row_label)
            if norm_name:
                row_index.setdefault(norm_name, []).append((table_id, row_label, row_data))

    # Check variables appearing in 2+ tables
    for norm_name, entries in row_index.items():
        if len(entries) < 2:
            continue

        # Find common year columns
        all_years = set()
        for _, _, row_data in entries:
            for col in row_data:
                yr = _extract_year_from_col(col)
                if yr:
                    all_years.add(yr)

        for year in sorted(all_years):
            values = []
            for table_id, row_label, row_data in entries:
                for col, val in row_data.items():
                    if _extract_year_from_col(col) == year:
                        values.append({
                            "table_id": table_id,
                            "row": row_label,
                            "col": col,
                            "value": str(val),
                        })
                        break  # one value per table per year

            if len(values) < 2:
                continue

            # Check consistency
            nums = [_normalize_number(v["value"]) for v in values]
            nums = [n for n in nums if n is not None]
            if len(nums) < 2:
                continue

            all_match = all(abs(nums[0] - n) <= tolerance for n in nums[1:])
            status = "CONSISTENT" if all_match else "INCONSISTENT"

            if not all_match:
                detail = f"{norm_name} ({year}): " + " vs ".join(
                    f"{v['value']} ({v['table_id']})" for v in values
                )
            else:
                detail = f"{norm_name} ({year}): {values[0]['value']} across {len(values)} tables"

            results.append(CrossTableResult(
                variable=norm_name,
                year=year,
                entries=values,
                status=status,
                detail=detail,
            ))

    return results


def _normalize_row_name(row_label: str) -> str | None:
    """Normalize a row label for cross-table matching."""
    s = row_label.lower().strip()
    # Remove common suffixes
    for suffix in (" 1/", " 2/", " 3/", " 4/", " 5/"):
        s = s.replace(suffix, "")
    s = re.sub(r'\(.*?\)', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    if len(s) < 3:
        return None
    return s


# --- Main graph construction ---

def build_graph(
    claims_path: Path,
    tables_dir: Path,
    metadata_path: Path | None = None,
    use_llm: bool = False,
    api_key: str | None = None,
) -> ConsistencyGraph:
    """
    Build a consistency verification graph from pdf_engineer output.

    Args:
        use_llm: If True, use LLM for claim-to-cell resolution (requires OPENAI_API_KEY)
        api_key: OpenAI API key (falls back to OPENAI_API_KEY env var)
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

    # --- LLM-enhanced resolution ---
    llm_resolutions = {}
    if use_llm:
        from factmesh.resolver import resolve_claims_batch
        resolutions = resolve_claims_batch(claims, tables, api_key=api_key)
        for r in resolutions:
            llm_resolutions[r.claim_id] = r

    # --- Process each claim ---
    for i, claim in enumerate(claims):
        claim_id = f"claim_{i}"
        claim_text = claim.get("claim_text", "")
        likely_table = claim.get("likely_table", "unknown")
        values = claim.get("values_mentioned", [])
        page = claim.get("page_or_section", "unknown")

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

        if likely_table != "unknown" and likely_table in tables:
            graph.add_edge(GraphEdge(
                source=claim_id,
                target=likely_table,
                type="references",
            ))

        if not values:
            graph.verifications.append(VerificationResult(
                claim_id=claim_id,
                status="QUALITATIVE",
                detail="No numeric values to verify",
            ))
            continue

        # Check if LLM already resolved this claim
        llm_resolution = llm_resolutions.get(claim_id)

        for val in values:
            variable = val.get("variable", "")
            value_str = val.get("value", "")
            year = val.get("year", "unknown")

            if not value_str or value_str in ("unknown",):
                continue

            # Add variable node
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

            # Try LLM resolution first
            if llm_resolution:
                llm_match = _find_llm_match(llm_resolution, variable, value_str)
                if llm_match and llm_match.match_status != "NOT_FOUND":
                    _add_verification_from_llm(graph, claim_id, llm_match, tables)
                    continue

            # Fall back to deterministic matching
            found = _try_deterministic_match(graph, claim_id, variable, value_str, year, likely_table, tables)

            if not found:
                graph.verifications.append(VerificationResult(
                    claim_id=claim_id,
                    status="UNVERIFIABLE",
                    claim_value=value_str,
                    variable=variable,
                    year=year,
                    detail=f"Value {value_str} for {variable} ({year}) not found in any table",
                ))

    # --- Cross-table consistency checks ---
    logger.info("Running cross-table consistency checks...")
    graph.cross_table_checks = _check_cross_table_consistency(tables)
    inconsistent = sum(1 for c in graph.cross_table_checks if c.status == "INCONSISTENT")
    logger.info("  %d cross-table checks, %d inconsistencies found",
                len(graph.cross_table_checks), inconsistent)

    return graph


def _find_llm_match(resolution, variable: str, value_str: str):
    """Find the matching LLM resolution for a specific variable+value."""
    for m in resolution.matches:
        if m.variable == variable and m.claim_value == value_str:
            return m
    # Try partial match
    for m in resolution.matches:
        if m.claim_value == value_str:
            return m
    return None


def _add_verification_from_llm(graph, claim_id, match, tables):
    """Add a verification result from LLM resolution."""
    table_id = match.table_id
    if table_id and table_id in tables:
        cell_id = f"cell_{table_id}_{match.row_label}_{match.col_label}".replace(" ", "_")[:80]
        if not any(n.id == cell_id for n in graph.nodes):
            graph.add_node(GraphNode(
                id=cell_id,
                type="cell",
                label=f"{match.row_label} / {match.col_label} = {match.table_value}",
                metadata={
                    "table_id": table_id,
                    "row": match.row_label,
                    "col": match.col_label,
                    "value": match.table_value,
                },
            ))
        graph.add_edge(GraphEdge(source=table_id, target=cell_id, type="contains_cell"))
        graph.add_edge(GraphEdge(
            source=claim_id, target=cell_id, type="verified_by",
            metadata={"status": match.match_status, "method": "llm"},
        ))

    status = "MATCH" if match.match_status == "MATCH" else "MISMATCH"
    graph.verifications.append(VerificationResult(
        claim_id=claim_id,
        status=status,
        claim_value=match.claim_value,
        table_value=match.table_value,
        table_id=table_id,
        variable=match.variable,
        year=match.year,
        detail=f"LLM: {match.reasoning}",
        resolution_method="llm",
    ))


def _try_deterministic_match(graph, claim_id, variable, value_str, year, likely_table, tables) -> bool:
    """Try deterministic keyword matching. Returns True if found."""
    search_tables = []
    if likely_table != "unknown" and likely_table in tables:
        search_tables.append((likely_table, tables[likely_table]))
    for tid, tdata in tables.items():
        if tid != likely_table:
            search_tables.append((tid, tdata))

    for table_id, table_data in search_tables:
        cell_value, matched_row, matched_col = _find_value_in_table(
            table_data, variable, year if year != "unknown" else None, value_str
        )
        if cell_value is not None:
            cell_id = f"cell_{table_id}_{matched_row}_{matched_col}".replace(" ", "_")[:80]
            if not any(n.id == cell_id for n in graph.nodes):
                graph.add_node(GraphNode(
                    id=cell_id, type="cell",
                    label=f"{matched_row} / {matched_col} = {cell_value}",
                    metadata={"table_id": table_id, "row": matched_row, "col": matched_col, "value": cell_value},
                ))
            graph.add_edge(GraphEdge(source=table_id, target=cell_id, type="contains_cell"))

            status = "MATCH" if _numbers_match(value_str, cell_value) else "MISMATCH"
            graph.add_edge(GraphEdge(
                source=claim_id, target=cell_id, type="verified_by",
                metadata={"status": status},
            ))
            graph.verifications.append(VerificationResult(
                claim_id=claim_id, status=status,
                claim_value=value_str, table_value=cell_value,
                table_id=table_id, variable=variable, year=year,
                detail=f"Claim: {value_str} | Table: {cell_value} ({matched_row} / {matched_col})",
            ))
            return True

    return False
