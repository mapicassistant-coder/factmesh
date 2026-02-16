"""
FactMesh — LLM-based claim-to-cell resolution.

Uses OpenAI to match narrative claims to specific table cells.
This replaces the naive keyword matching with semantic understanding.
"""

import json
import logging
import os
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger("factmesh.resolver")


class CellMatch(BaseModel):
    """A resolved match between a claim value and a table cell."""
    variable: str = Field(description="Variable name from the claim")
    claim_value: str = Field(description="Value as stated in the claim")
    year: str = Field(description="Year the value refers to")
    table_id: str | None = Field(description="Table ID where the value was found, or null if not found")
    row_label: str | None = Field(description="Exact row label in the table")
    col_label: str | None = Field(description="Exact column label in the table")
    table_value: str | None = Field(description="Value found in the table cell, or null")
    match_status: str = Field(description="MATCH, MISMATCH, or NOT_FOUND")
    reasoning: str = Field(description="Brief explanation of how the match was determined")


class ClaimResolution(BaseModel):
    """Resolution of all values in a single claim."""
    claim_id: str
    matches: list[CellMatch]


SYSTEM_PROMPT = """You are an IMF Staff Report verification assistant. Your job is to match
narrative claims to specific cells in statistical tables.

You will receive:
1. A narrative claim with extracted values and variables
2. A set of available tables with their full data

For each value in the claim, find the EXACT cell in the tables that contains this value.

IMPORTANT RULES:
- Match by MEANING, not just keywords. "Credit to the private sector" = "Private_Sector_Credit"
- Column headers may have suffixes like "_Prel.", "_Proj.", "_Est." — these correspond to years
- A column "2023_Proj." means year 2023 (projected)
- Values may differ slightly due to rounding (e.g., claim says 3.2, table says 3.15 → MATCH)
- Tolerance: within 0.15 absolute difference = MATCH
- If a value genuinely cannot be found in any table (e.g., it's from external data), mark as NOT_FOUND
- If the value is found but differs beyond tolerance, mark as MISMATCH
- Negative signs matter: -7.2 ≠ 7.2"""


def resolve_claims_batch(
    claims: list[dict],
    tables: dict[str, dict],
    batch_size: int = 5,
    api_key: str | None = None,
) -> list[ClaimResolution]:
    """
    Use LLM to resolve claim values to table cells in batches.

    Only processes claims that have numeric values (skips qualitative).
    """
    client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"), timeout=60.0)

    # Build compact table representation
    table_summary = _build_table_context(tables)

    # Filter to claims with values
    numeric_claims = [
        (i, c) for i, c in enumerate(claims)
        if c.get("values_mentioned") and len(c["values_mentioned"]) > 0
    ]

    logger.info("Resolving %d numeric claims against %d tables via LLM...", len(numeric_claims), len(tables))

    all_resolutions = []

    for batch_start in range(0, len(numeric_claims), batch_size):
        batch = numeric_claims[batch_start:batch_start + batch_size]

        # Collect relevant tables for this batch
        relevant_table_ids = set()
        # Always include the main macro tables
        core_tables = {"Table_1_p15", "Table_1_p49", "Table_3_p23", "Table_4_p47", "Table_9_p52", "Table_13_p58"}
        relevant_table_ids.update(core_tables & set(tables.keys()))
        for _, claim in batch:
            lt = claim.get("likely_table", "unknown")
            if lt != "unknown" and lt in tables:
                relevant_table_ids.add(lt)

        batch_tables = {tid: tables[tid] for tid in relevant_table_ids}
        batch_table_summary = _build_table_context(batch_tables)

        claims_text = []
        for idx, (claim_idx, claim) in enumerate(batch):
            claim_id = f"claim_{claim_idx}"
            values_str = json.dumps(claim.get("values_mentioned", []), indent=2)
            claims_text.append(
                f"CLAIM {claim_id} (page {claim.get('page_or_section', '?')}, "
                f"likely_table={claim.get('likely_table', 'unknown')}):\n"
                f"  Text: \"{claim['claim_text']}\"\n"
                f"  Values: {values_str}"
            )

        user_prompt = f"""Resolve these claims against the tables below.

CLAIMS:
{chr(10).join(claims_text)}

TABLES:
{batch_table_summary}

For each claim, find the exact table cell for each value. Return a ClaimResolution for each claim."""

        try:
            response = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=_BatchResolution,
                temperature=0,
            )

            batch_result = response.choices[0].message.parsed
            if batch_result:
                all_resolutions.extend(batch_result.resolutions)
                logger.info("  Batch %d-%d: resolved %d claims",
                           batch_start, batch_start + len(batch),
                           len(batch_result.resolutions))

        except Exception as e:
            logger.error("  Batch %d-%d failed: %s", batch_start, batch_start + len(batch), e)
            # Fall back to NOT_FOUND for failed batches
            for claim_idx, claim in batch:
                all_resolutions.append(ClaimResolution(
                    claim_id=f"claim_{claim_idx}",
                    matches=[
                        CellMatch(
                            variable=v.get("variable", "unknown"),
                            claim_value=v.get("value", ""),
                            year=v.get("year", "unknown"),
                            table_id=None, row_label=None, col_label=None,
                            table_value=None, match_status="NOT_FOUND",
                            reasoning=f"LLM resolution failed: {e}",
                        )
                        for v in claim.get("values_mentioned", [])
                    ],
                ))

    return all_resolutions


class _BatchResolution(BaseModel):
    """Batch of claim resolutions."""
    resolutions: list[ClaimResolution]


def _build_table_context(tables: dict[str, dict], max_rows: int = 30) -> str:
    """Build a compact text representation of all tables for the LLM prompt."""
    parts = []
    for table_id, table_data in sorted(tables.items()):
        title = table_data.get("table_title", "Untitled")
        page = table_data.get("page_num", "?")
        data = table_data.get("data", {})

        lines = [f"### {table_id} (page {page}): {title}"]

        # Get all columns
        all_cols = set()
        for row_data in data.values():
            if isinstance(row_data, dict):
                all_cols.update(row_data.keys())
        cols = sorted(all_cols)

        if not cols or not data:
            lines.append("  (empty table)")
            parts.append("\n".join(lines))
            continue

        # Header
        lines.append(f"  Columns: {', '.join(cols[:15])}")

        # Rows (truncate if too many)
        row_items = list(data.items())[:max_rows]
        for row_name, row_data in row_items:
            if isinstance(row_data, dict):
                vals = [f"{c}={row_data.get(c, '')}" for c in cols[:10] if c in row_data]
                lines.append(f"  {row_name}: {', '.join(vals)}")

        if len(data) > max_rows:
            lines.append(f"  ... ({len(data) - max_rows} more rows)")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)
