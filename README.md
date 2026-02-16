# FactMesh

**Automated macro-consistency verification for IMF Staff Reports.**

FactMesh takes the structured output of a PDF extraction pipeline (tables + narrative claims) and builds a verification graph that connects what the text says to what the tables show — then checks if they agree.

## Why this matters

Every IMF Staff Report contains hundreds of quantitative claims woven through narrative text, each implicitly referencing data in statistical tables. Today, verifying consistency between narrative and tables is **entirely manual** — economists read the text, find the table, locate the cell, check the number. For a 150-page report with 100+ claims and 20+ tables, this takes hours and errors slip through.

FactMesh automates this first layer of verification.

## The bigger picture

This tool is **Node 1** in a larger vision:

```
Node 1 (this repo):  Single-document consistency
                      "Does the text match its own tables?"
                      Claim → Table → Cell → Value match?
                           │
Node 2 (future):      Cross-vintage consistency
                      "Do the numbers match the latest WEO?"
                      Staff Report table → WEO vintage → match?
                           │
Node 3 (future):      Cross-document reasoning
                      "Is this report consistent with the previous one?"
                      Report 2024 claims → Report 2023 claims → evolution check
                           │
Node 4 (future):      Knowledge graph + bitemporal reasoning
                      "When did this assumption change, and did downstream
                       projections update accordingly?"
                      Full GraphRAG over the country's document history
```

Each node builds on the previous one. Node 1 is the foundation — if we can't reliably link claims to table cells within a single document, nothing downstream works.

## Architecture

```
┌──────────────────────────────────────┐
│           pdf_engineer output        │
│  narrative_claims.json  tables/*.json│
└──────────────┬───────────────────────┘
               │
       ┌───────▼────────┐
       │  Variable       │   LLM normalizes variable names
       │  Normalization  │   "GDP growth" → NGDP_RPCH
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Cell Lookup    │   Deterministic: find value in table
       │  (fuzzy match)  │   row="Real GDP" col="2023" → 3.2
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Graph Builder  │   claim → table → cell → value
       │                 │   with typed edges
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Verifier       │   claim.value == cell.value?
       │                 │   MATCH / MISMATCH / UNVERIFIABLE
       └───────┬─────────┘
               │
       ┌───────▼────────┐
       │  Output         │   consistency_graph.json
       │                 │   consistency_report.md
       │                 │   verification_summary.html
       └─────────────────┘
```

### Key design principles

1. **LLM for understanding, deterministic for verification.** The LLM normalizes variable names and matches claims to table cells. But the actual value comparison is pure string/number matching — no LLM judgment on "does 3.2 equal 3.2?"

2. **Explicit uncertainty.** Every verification result is one of:
   - `MATCH` — claim value found in table, values agree
   - `MISMATCH` — claim value found in table, values disagree
   - `UNVERIFIABLE` — claim mentions a number but no corresponding table cell found
   - `QUALITATIVE` — claim has no numeric values (policy assessment, risk judgment)

3. **Graph structure for future extensibility.** The output is a graph (nodes + edges), not a flat report. This means Node 2-4 can extend it by adding more nodes (WEO data, previous reports) and edges (cross-document links) without redesigning the core.

## Input format

FactMesh expects the output of [pdf_engineer](https://github.com/xsusyagez/pdf_engineer):

```
input/SYC2024_Staff_Report/
├── narrative_claims.json    # extracted claims with likely_table
├── metadata.json            # table inventory
└── tables/
    ├── Table_1_p15.json     # individual table data
    ├── Table_3_p23.json
    └── ...
```

## Output

```
output/SYC2024_Staff_Report/
├── consistency_graph.json   # full verification graph
├── consistency_report.md    # human-readable report
└── verification_summary.html # visual dashboard
```

## Getting started

```bash
uv sync
uv run python -m factmesh input/SYC2024_Staff_Report/
```

## Status

- [x] Node 1 MVP: single-document claim-table verification
- [ ] Node 2: WEO vintage cross-check
- [ ] Node 3: cross-document consistency
- [ ] Node 4: knowledge graph + bitemporal reasoning
