"""
ProfileAgent — Two-phase catalog agent orchestrator.

Phase 1: Collect all profiles via ReAct tool loop
Phase 2: Synthesize final catalog JSON
"""

import anthropic
import json
import re

from .profiling import load_csv, profile_column
from .validation import validate_column
from .catalog import save_catalog


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


SYNTHESIS_SYSTEM = """You are a senior data catalog and data governance engineer. Given column profiles and validation results, produce a production-quality metadata catalog.

Return ONLY a valid JSON object — no markdown fences, no explanation, no preamble.

*** CRITICAL INSTRUCTION FOR VALIDATION FACTS ***
When you see validation results: use these numbers as the SOURCE OF TRUTH.
For invalid values: report as DATA QUALITY ISSUES.
For duplicates: report separately as business duplicates.

For every column include:
  name, description, semantic_type, data_type, tags, pii_risk,
  nullable, null_percentage, uniqueness_percentage, sample_values,
  stats, quality_observations, business_glossary_term, recommended_constraints

Root object: { "source_file": "<filename>", "total_rows": <int>, "columns": [...] }"""


class ProfileAgent:
    """Orchestrates two-phase data profiling and catalog synthesis."""
    
    def __init__(self):
        self.client = anthropic.Anthropic()
    
    def run(self, filepath: str) -> dict:
        """Profile a CSV and generate a full metadata catalog."""
        print(f"\n{'='*60}")
        print(f"  🤖  CSV Catalog Agent")
        print(f"  📄  File : {filepath}")
        print(f"{'='*60}\n")

        print("  📊  Profiling columns\n")
        collected = self._run_profiling_phase(filepath)

        catalog = self._run_synthesis_phase(filepath, collected)

        paths = save_catalog(catalog)
        return paths
    
    def _run_profiling_phase(self, filepath: str) -> dict:
        """Run ReAct tool loop to collect all profiles."""
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
        MAX_ITERATIONS = 30

        for iteration in range(1, MAX_ITERATIONS + 1):

            response = self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=8192,
                system=PHASE1_SYSTEM,
                tools=PROFILING_TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
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

                result = self._dispatch(block.name, block.input)

                if block.name == "load_csv":
                    if "error" in result:
                        raise RuntimeError(f"load_csv failed: {result['error']}")
                    collected["overview"] = result

                elif block.name == "profile_column":
                    if "error" in result:
                        raise RuntimeError(f"profile_column failed: {result['error']}")
                    column_name = result.get("column_name", "?")
                    collected["column_profiles"][column_name] = result
                    null_pct = result.get("null_percentage", 0)
                    unique_n = result.get("unique_count", "?")

                elif block.name == "validate_column":
                    if "error" in result:
                        raise RuntimeError(f"validate_column failed: {result['error']}")
                    column_name = result.get("column_name", "?")
                    validation_type = result.get("validation_type", "?")
                    issues = result.get("issues_found", 0)
                    
                    if column_name in collected["column_profiles"]:
                        collected["column_profiles"][column_name]["validation_results"] = result

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str, ensure_ascii=False),
                })

            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(f"Profiling did not complete within {MAX_ITERATIONS} iterations.")

    def _run_synthesis_phase(self, filepath: str, collected: dict) -> dict:
        """Synthesize final catalog from collected profiles."""
        overview  = collected.get("overview", {})
        profiles  = collected.get("column_profiles", {})
        
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

        response = self.client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=16384,
            system=SYNTHESIS_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            catalog = json.loads(raw)
            print("  ✅  Catalog JSON parsed successfully\n")
            return catalog
        except json.JSONDecodeError as e:
            print(f"  ⚠️  JSON parse error: {e}")
            fixed = raw
            fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)
            fixed = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', fixed)
            
            try:
                catalog = json.loads(fixed)
                print("  ✅  Fixed and parsed successfully\n")
                return catalog
            except json.JSONDecodeError as e2:
                raise RuntimeError(f"Failed to parse catalog JSON: {e2}")

    def _dispatch(self, name: str, inputs: dict):
        """Dispatch tool calls to their implementations."""
        if name == "load_csv":
            return load_csv(inputs["filepath"])
        if name == "profile_column":
            return profile_column(inputs["filepath"], inputs["column_name"])
        if name == "validate_column":
            return validate_column(inputs["filepath"], inputs["column_name"], inputs["validation_type"])
        return {"error": f"Unknown tool: {name}"}