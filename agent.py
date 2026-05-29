"""
agent.py — Two-phase catalog agent.

WHY TWO PHASES?
  Generating the full catalog JSON for 20+ columns is large (~3-5k tokens).
  If we ask Claude to call save_catalog (a tool) with that payload, it has
  to fit the entire JSON inside a tool_use block within its max_tokens budget
  — which causes it to stall in a loop saying "I'll do it now" without ever
  actually doing it.

  Fix: separate data collection from synthesis.
    Phase 1 (tool loop)  — Claude calls load_csv + profile_column freely.
                           We collect every profile result in Python.
    Phase 2 (one shot)   — We hand Claude all profiles and ask for pure JSON.
                           Claude returns text (not a tool call), so there's
                           no token-budget conflict. We parse + save it ourselves.
"""

import anthropic
import json
import re

from tools import load_csv, profile_column, save_catalog, validate_column


# ─────────────────────────────────────────────
# Phase 1 tools  (profiling + validation)
# ─────────────────────────────────────────────
PROFILING_TOOLS = [
    {
        "name": "load_csv",
        "description": (
            "Load a CSV file and return its shape, column names, pandas dtypes, "
            "null summary, and first 5 sample rows. Call this first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"}
            },
            "required": ["filepath"],
        },
    },
    {
        "name": "profile_column",
        "description": (
            "Deep statistical profile of one column analyzing ALL rows: null %, uniqueness %, "
            "sample values, value distribution (categoricals), numeric stats. "
            "Call this for EVERY column before finishing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath":    {"type": "string"},
                "column_name": {"type": "string"},
            },
            "required": ["filepath", "column_name"],
        },
    },
    {
        "name": "validate_column",
        "description": (
            "Comprehensive validation of an entire column for data quality issues. "
            "Scans ALL rows. Use for email, phone, duplicates, or null validation. "
            "Returns detailed results with row numbers of all issues found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath":        {"type": "string"},
                "column_name":     {"type": "string"},
                "validation_type": {
                    "type": "string",
                    "enum": ["email", "phone", "duplicates", "null_check"]
                }
            },
            "required": ["filepath", "column_name", "validation_type"],
        },
    },
]

PHASE1_SYSTEM = """You are a data profiling agent. Your ONLY job in this phase is to:

1. Call load_csv to understand the dataset
2. Call profile_column for EVERY column
3. MANDATORY: After profiling all columns, you MUST identify and validate:
   - All columns that appear to contain EMAIL addresses → call validate_column with validation_type="email"
   - All columns that appear to contain PHONE numbers → call validate_column with validation_type="phone"
   - All columns with potential duplicates (low uniqueness) → call validate_column with validation_type="duplicates"
   
Based on the column names, descriptions, and semantic types from profiling, determine which columns need validation.

Do not proceed to synthesis until you have called validate_column for all identified columns."""


# ─────────────────────────────────────────────
# Phase 2 synthesis prompt
# ─────────────────────────────────────────────
SYNTHESIS_SYSTEM = """You are a senior data catalog and data governance engineer \
(think Atlan, Alation, Collibra). Given column profiles and validation results, produce a production-quality metadata catalog.

Return ONLY a valid JSON object — no markdown fences, no explanation, no preamble.

*** CRITICAL INSTRUCTION FOR VALIDATION FACTS ***
When you see validation results in the column profiles:
- This data is the SOURCE OF TRUTH from automated scanning
- You MUST use these numbers, not recalculate or second-guess them
- For INVALID values detected: report them as DATA QUALITY ISSUES
- For duplicates: report them separately as business duplicates
- Write quality_observations EXACTLY based on these facts

For every column include:
  name, description, semantic_type, data_type, tags, pii_risk,
  nullable, null_percentage, uniqueness_percentage, sample_values,
  stats (min/max/mean for numerics, else null), quality_observations,
  business_glossary_term, recommended_constraints

semantic_type choices : identifier | categorical | continuous | timestamp |
                        text | boolean | currency | email | phone |
                        address | url | code | unknown
data_type choices     : string | integer | float | boolean | date | datetime | unknown
pii_risk choices      : none | low | medium | high
recommended_constraints examples: NOT_NULL, UNIQUE, POSITIVE_VALUES_ONLY,
                                   VALID_EMAIL, RANGE_0_100, FOREIGN_KEY(table.col)

FOR VALIDATED COLUMNS:
- If validation results show invalid values, include them in quality_observations as DATA QUALITY ISSUES
- If validation results show duplicates, report the duplicate count and example values
- Include appropriate constraints based on validation findings (e.g., VALID_EMAIL, VALID_PHONE)

Root object shape:
{
  "source_file": "<filename>",
  "total_rows": <int>,
  "columns": [ { ...per-column fields... }, ... ]
}"""


# ─────────────────────────────────────────────
# Tool dispatcher (Phase 1 only)
# ─────────────────────────────────────────────
def _dispatch(name: str, inputs: dict):
    if name == "load_csv":
        return load_csv(inputs["filepath"])
    if name == "profile_column":
        return profile_column(inputs["filepath"], inputs["column_name"])
    if name == "validate_column":
        return validate_column(inputs["filepath"], inputs["column_name"], inputs["validation_type"])
    return {"error": f"Unknown tool: {name}"}


# ─────────────────────────────────────────────
# Phase 1 — collect all profiles via ReAct tool loop
# ─────────────────────────────────────────────
def _run_profiling_phase(client: anthropic.Anthropic, filepath: str) -> dict:
    """Run a ReAct-style tool loop for profiling and collect every profile result."""

    messages = [{"role": "user", "content": (
        f"Profile the CSV at: {filepath}\n"
        "REQUIRED STEPS:\n"
        "1. load_csv to discover all columns\n"
        "2. profile_column for EVERY column\n"
        "3. Identify columns needing validation (emails, phones, potential duplicates)\n"
        "4. Call validate_column for each identified column\n"
        "5. Stop when all validations are complete\n\n"
        "Do not finish until all appropriate validations are complete."
    )}]

    collected = {"overview": None, "column_profiles": {}}
    validations_attempted = {}  # Track which columns have been validated
    MAX_ITERATIONS = 50

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"  🔁  Profiling agent iteration {iteration}/{MAX_ITERATIONS}")

        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=8192,
            system=PHASE1_SYSTEM,
            tools=PROFILING_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            # Count how many columns were profiled
            profiled_count = len(collected['column_profiles'])
            overview_cols = len(collected.get('overview', {}).get('columns', []))
            
            print(f"\n  ✅  Profiling done — {profiled_count} columns profiled (expected: {overview_cols})\n")
            return collected

        if response.stop_reason != "tool_use":
            raise RuntimeError(f"Unexpected profiling stop reason: {response.stop_reason}")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            result = _dispatch(block.name, block.input)

            if block.name == "load_csv":
                if "error" in result:
                    raise RuntimeError(f"load_csv failed: {result['error']}")

                collected["overview"] = result
                print(f"  🔧  load_csv → {result.get('shape', {})}")

            elif block.name == "profile_column":
                if "error" in result:
                    column_name = block.input.get("column_name", "?")
                    raise RuntimeError(f"profile_column failed for '{column_name}': {result['error']}")

                column_name = block.input.get("column_name", result.get("column_name", "?"))

                if column_name in collected["column_profiles"]:
                    print(f"  ⚠️   duplicate profile_column({column_name}) call")

                collected["column_profiles"][column_name] = result
                null_pct = result.get("null_percentage", 0)
                unique_n = result.get("unique_count", "?")
                print(f"  🔧  profile_column({column_name}) → nulls={null_pct}%  unique={unique_n}")

            elif block.name == "validate_column":
                if "error" in result:
                    column_name = block.input.get("column_name", "?")
                    raise RuntimeError(f"validate_column failed for '{column_name}': {result['error']}")

                column_name = block.input.get("column_name", "?")
                validation_type = block.input.get("validation_type", "?")
                issues = result.get("issues_found", 0)
                print(f"  🔧  validate_column({column_name}, {validation_type}) → {issues} issues found")
                
                # Track this validation attempt
                validations_attempted[column_name] = validation_type
                
                # Store validation results in the profile
                if column_name in collected["column_profiles"]:
                    collected["column_profiles"][column_name]["validation_results"] = result

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, default=str, ensure_ascii=False),
            })

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Profiling did not complete within {MAX_ITERATIONS} iterations.")


# ─────────────────────────────────────────────
# Phase 2 — one-shot JSON synthesis
# ─────────────────────────────────────────────
def _run_synthesis_phase(client: anthropic.Anthropic, filepath: str, collected: dict) -> dict:
    """Ask Claude to produce the catalog JSON from the collected profiles."""
    overview  = collected.get("overview", {})
    profiles  = collected.get("column_profiles", {})

    # Debug: Print what we're actually getting
    print("\n  📋  DEBUG: Validation results in collected data:")
    validation_summary = {}
    for col_name, profile in profiles.items():
        if "validation_results" in profile:
            val_result = profile["validation_results"]
            val_type = val_result.get('validation_type', '?')
            issues = val_result.get('issues_found', 0)
            validation_summary[col_name] = {"type": val_type, "issues": issues}
            print(f"      {col_name}: {val_type} validation - {issues} issues found")
        else:
            print(f"      {col_name}: NO validation performed")
    
    # Build comprehensive validation facts
    mandatory_facts = "VALIDATION FACTS (from automated scanning tool):\n\n"
    
    for col_name, profile in profiles.items():
        if "validation_results" in profile:
            val_result = profile["validation_results"]
            val_type = val_result.get("validation_type", "")
            
            if val_type == "email":
                invalid_count = val_result.get('invalid_emails_count', 0)
                duplicate_count = val_result.get('duplicate_valid_emails_count', 0)
                valid_count = val_result.get('valid_emails_count', 0)
                
                mandatory_facts += f"{col_name.upper()}:\n"
                if invalid_count > 0:
                    invalid_vals = val_result.get('invalid_emails', [])
                    mandatory_facts += f"  ❌ INVALID EMAILS: {invalid_count} entries\n"
                    mandatory_facts += f"     Examples: {invalid_vals[:3]}\n"
                else:
                    mandatory_facts += f"  ✅ All emails valid\n"
                mandatory_facts += f"  ✅ Valid unique: {valid_count}\n"
                mandatory_facts += f"  ✅ Valid duplicates: {duplicate_count}\n\n"
            
            elif val_type == "phone":
                issues = val_result.get('issues_found', 0)
                mandatory_facts += f"{col_name.upper()}:\n"
                mandatory_facts += f"  Issues found: {issues}\n\n"
            
            elif val_type == "duplicates":
                issues = val_result.get('issues_found', 0)
                mandatory_facts += f"{col_name.upper()}:\n"
                mandatory_facts += f"  Duplicate groups: {issues}\n\n"

    user_msg = (
        f"Source file: {filepath}\n"
        f"Dataset shape: {overview.get('shape', 'unknown')}\n\n"
        f"{mandatory_facts}\n"
        f"Column profiles:\n{json.dumps(profiles, indent=2, default=str)}\n\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"1. Use validation facts above for data quality observations\n"
        f"2. If validation shows invalid values, include in quality_observations\n"
        f"3. Add validation-based constraints (e.g., VALID_EMAIL for email columns)\n"
        f"4. Do NOT re-validate - trust the automated tool results"
    )

    print("  🧠  Synthesising catalog…")

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=16384,
        system=SYNTHESIS_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()

    # Strip any accidental ```json fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        catalog = json.loads(raw)
        print("  ✅  Catalog JSON parsed successfully\n")
        return catalog
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON parse error: {e}")
        print(f"  Error at line {e.lineno}, column {e.colno}")
        print("  Raw response (chars around error):")
        start = max(0, e.pos - 100)
        end = min(len(raw), e.pos + 100)
        print(f"    ...{raw[start:end]}...")
        print("\n  Attempting to fix common JSON issues...")
        
        # Try to fix common issues
        fixed = raw
        # Remove trailing commas before closing braces/brackets
        fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)
        # Ensure all keys are quoted
        fixed = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', fixed)
        
        try:
            catalog = json.loads(fixed)
            print("  ✅  Fixed and parsed successfully\n")
            return catalog
        except json.JSONDecodeError as e2:
            print(f"  ❌  Still invalid: {e2}")
            raise RuntimeError(
                f"Failed to parse/fix catalog JSON. "
                f"Error: {e}. "
                f"Please check the raw response or increase max_tokens in synthesis phase."
            )


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────
def run_catalog_agent(filepath: str) -> dict:
    client = anthropic.Anthropic()

    print(f"\n{'='*60}")
    print(f"  🤖  CSV Catalog Agent  (two-phase)")
    print(f"  📄  File : {filepath}")
    print(f"{'='*60}\n")

    # ── Phase 1: profile all columns ──────────
    print("  📊  Phase 1 — Profiling columns\n")
    collected = _run_profiling_phase(client, filepath)

    # ── Phase 2: synthesise catalog JSON ──────
    print("  🗂️   Phase 2 — Synthesising catalog\n")
    catalog = _run_synthesis_phase(client, filepath, collected)

    # ── Save outputs ──────────────────────────
    paths = save_catalog(catalog)
    return paths