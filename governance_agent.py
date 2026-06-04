"""
ProfileAgent — Two-phase catalog agent orchestrator.

Main entry point for CSV data profiling and metadata catalog generation.

Implements a ReAct-style agent that:
  Phase 1: Profiles CSV columns via tool loop
  Phase 2: Synthesizes metadata catalog via LLM
"""

import json
import math
import os
import re
import sys
import time
import uuid

from dotenv import load_dotenv
from google import genai
from google.genai import types

from governance_memory.semantic import SemanticMemory
from governance_memory.store import SQLiteEpisodeStore
from governance_skills import (
    get_dataset_profile,
    load_csv,
    profile_column,
    save_catalog,
    validate_column,
)

# Load environment variables from .env file
load_dotenv()


PROFILING_TOOLS = [
    {
        "name": "get_dataset_profile",
        "description": (
            "Load the entire dataset and return a comprehensive profile for ALL columns at once. "
            "Call this first to discover all columns and their statistics."
        ),
        "parameters": {
            "type": "object",
            "properties": {"filepath": {"type": "string"}},
            "required": ["filepath"],
        },
    },
    {
        "name": "validate_column",
        "description": (
            "Comprehensive validation of an entire column for data quality issues. "
            "Scans ALL rows. Use for email, phone, duplicates, or null validation. "
            "Returns detailed results with row numbers of all issues found."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "column_name": {"type": "string"},
                "validation_type": {
                    "type": "string",
                    "enum": ["email", "phone", "duplicates", "null_check"],
                },
            },
            "required": ["filepath", "column_name", "validation_type"],
        },
    },
    {
        "name": "query_semantic_memory",
        "description": (
            "Query the governance semantic memory for rules, guidelines, or standards. "
            "Use this to find specific governance policies when profiling a new column "
            "or resolving a ambiguity in metadata classification."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]

PHASE1_SYSTEM = """You are a data profiling agent. Your ONLY job in this phase is to:

1. Call get_dataset_profile to get the full statistical overview of the dataset.
2. Examine the returned column statistics.
3. MANDATORY: Identify and validate columns:
   - Carefully examine the `sample_values` for every column.
   - All columns containing data that looks like EMAIL addresses (contains @, domains) → call validate_column
   - All columns containing data that looks like PHONE numbers → call validate_column
   - All columns with potential duplicates (low uniqueness) → call validate_column

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
        self.client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.model = "gemini-3.1-flash-lite"
        self.semantic_memory = SemanticMemory(client=self.client)
        self.episode_store = SQLiteEpisodeStore()

    def run(self, filepath: str) -> dict:
        """Profile a CSV and generate a full metadata catalog."""
        episode_id = str(uuid.uuid4())
        self.episode_store.create_episode(
            episode_id, filepath, summary=f"Profiling {filepath}"
        )

        start_time = time.time()
        print(f"\n{'=' * 60}")
        print(f"  🤖  CSV Catalog Agent")
        print(f"  📄  File : {filepath}")
        print(f"  🆔  Episode: {episode_id}")
        print(f"{'=' * 60}\n")

        print("  📊  Profiling columns\n")
        collected = self._run_profiling_phase(episode_id, filepath)

        catalog = self._run_synthesis_phase(episode_id, filepath, collected)

        paths = save_catalog(catalog)

        # Display performance metrics
        duration = time.time() - start_time
        overview = collected.get("overview", {})
        shape = overview.get("shape", {"rows": 0, "columns": 0})

        self.episode_store.complete_episode(episode_id, "completed", duration, catalog)

        print("\n" + "=" * 70)
        print(f"✨ Profiling complete in {duration:.2f}s")
        print(f"📊 Stats   : {shape.get('rows')} rows, {shape.get('columns')} columns")
        print(f"📄 JSON    : {paths['json_path']}")
        print(f"📝 Markdown: {paths['md_path']}")
        print("=" * 70)

        return paths

    def _sanitize_result(self, result):
        """Recursively replace NaN with None in dictionaries/lists for valid JSON."""
        if isinstance(result, float) and math.isnan(result):
            return None
        elif isinstance(result, dict):
            return {k: self._sanitize_result(v) for k, v in result.items()}
        elif isinstance(result, list):
            return [self._sanitize_result(i) for i in result]
        return result

    def _run_profiling_phase(self, episode_id: str, filepath: str) -> dict:
        """Run ReAct tool loop to collect all profiles."""
        messages = [
            {
                "role": "user",
                "parts": [
                    types.Part.from_text(
                        text=f"Profile the CSV at: {filepath}\n"
                        "REQUIRED STEPS:\n"
                        "1. get_dataset_profile to discover all columns and stats\n"
                        "2. Identify columns needing validation (emails, phones, potential duplicates)\n"
                        "3. Call validate_column for each identified column\n"
                        "4. Stop when all validations are complete\n\n"
                        "Do not finish until all appropriate validations are complete."
                    )
                ],
            }
        ]

        collected = {"overview": None, "column_profiles": {}}
        MAX_ITERATIONS = 30

        for iteration in range(1, MAX_ITERATIONS + 1):
            # Retry logic for 429 Rate Limit
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=messages,
                        config=types.GenerateContentConfig(
                            system_instruction=PHASE1_SYSTEM,
                            tools=[types.Tool(function_declarations=PROFILING_TOOLS)],
                        ),
                    )
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < max_retries - 1:
                        wait_time = 60  # * (attempt + 1) This is the requests per min limit, so waiting for a min instead of exponentially backing off.
                        print(
                            f"  ⚠️  Rate limit hit (profiling). Retrying in {wait_time}s..."
                        )
                        time.sleep(wait_time)
                        continue
                    raise

            # Check if model finished
            if not response.candidates[0].content.parts[0].function_call:
                # Assuming simple text response means end of turn
                return collected

            # Handle tool calls
            messages.append(response.candidates[0].content)
            tool_results = []

            for part in response.candidates[0].content.parts:
                if part.function_call:
                    func_name = part.function_call.name
                    func_args = dict(part.function_call.args)

                    result = self._dispatch(func_name, func_args)
                    result = self._sanitize_result(result)

                    # Log step to episode store
                    self.episode_store.add_step(
                        episode_id, iteration, func_name, func_args, result
                    )

                    if "error" in result and func_name in [
                        "load_csv",
                        "profile_column",
                        "validate_column",
                    ]:
                        raise RuntimeError(f"{func_name} failed: {result['error']}")

                    if func_name == "get_dataset_profile":
                        collected["overview"] = result
                        collected["column_profiles"] = result.get("column_profiles", {})
                    elif func_name == "validate_column":
                        column_name = result.get("column_name", "?")
                        if column_name in collected["column_profiles"]:
                            collected["column_profiles"][column_name][
                                "validation_results"
                            ] = result

                    tool_results.append(
                        types.Part.from_function_response(
                            name=func_name,
                            response={"result": result},
                        )
                    )

            messages.append({"role": "user", "parts": tool_results})

        raise RuntimeError(
            f"Profiling did not complete within {MAX_ITERATIONS} iterations."
        )

    def _run_synthesis_phase(
        self, episode_id: str, filepath: str, collected: dict
    ) -> dict:
        """Synthesize final catalog from collected profiles with retry and data pruning."""
        overview = collected.get("overview", {})
        profiles = collected.get("column_profiles", {})

        # Prune data to save tokens
        pruned_profiles = {}
        for col, profile in profiles.items():
            pruned_profile = profile.copy()
            if "sample_values" in pruned_profile:
                # Keep only first 3 samples
                pruned_profile["sample_values"] = pruned_profile["sample_values"][:3]
            pruned_profiles[col] = pruned_profile

        mandatory_facts = "VALIDATION FACTS (from automated scanning tool):\n\n"
        for col_name, profile in profiles.items():
            if "validation_results" in profile:
                val_result = profile["validation_results"]
                val_type = val_result.get("validation_type", "")

                if val_type == "email":
                    invalid_count = val_result.get("invalid_emails_count", 0)
                    duplicate_count = val_result.get("duplicate_valid_emails_count", 0)
                    valid_count = val_result.get("valid_emails_count", 0)

                    mandatory_facts += f"{col_name.upper()}:\n"
                    if invalid_count > 0:
                        invalid_vals = val_result.get("invalid_emails", [])
                        mandatory_facts += (
                            f"  ❌ INVALID EMAILS: {invalid_count} entries\n"
                        )
                        mandatory_facts += f"     Examples: {invalid_vals[:3]}\n"
                    else:
                        mandatory_facts += f"  ✅ All emails valid\n"
                    mandatory_facts += f"  ✅ Valid unique: {valid_count}\n"
                    mandatory_facts += f"  ✅ Valid duplicates: {duplicate_count}\n\n"

        # Query semantic memory for relevant rules
        relevant_rules = []
        for col_name, profile in profiles.items():
            query_str = f"Profiling column {col_name} with sample values {profile.get('sample_values', [])[:3]}"
            rules = self.semantic_memory.query_rules(query_str, limit=2)
            relevant_rules.extend([r["text"] for r in rules])

        # Remove duplicates
        relevant_rules = list(set(relevant_rules))
        rules_text = "\n".join([f"- {r}" for r in relevant_rules])

        user_msg = (
            f"Source file: {filepath}\n"
            f"Dataset shape: {overview.get('shape', 'unknown')}\n\n"
            f"{mandatory_facts}\n"
            f"Column profiles (pruned):\n{json.dumps(pruned_profiles, indent=2, default=str)}\n\n"
            f"GOVERNANCE GUIDELINES (Use these for catalog synthesis):\n{rules_text}\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. Use validation facts above for data quality observations\n"
            f"2. If validation shows invalid values, include in quality_observations\n"
            f"3. Add validation-based constraints (e.g., VALID_EMAIL for email columns)\n"
            f"4. Do NOT re-validate - trust the automated tool results"
        )

        print("  🧠  Synthesising catalog (with retry logic)…")

        # Retry logic for 429 Rate Limit
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=user_msg,
                    config=types.GenerateContentConfig(
                        system_instruction=SYNTHESIS_SYSTEM,
                    ),
                )
                break
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    wait_time = 60  # * (attempt + 1) This is the requests per min limit, so waiting for a min instead of exponentially backing off.
                    print(f"  ⚠️  Rate limit hit. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                raise

        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            fixed = raw
            fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)
            fixed = re.sub(
                r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)", r'\1"\2"\3', fixed
            )

            try:
                return json.loads(fixed)
            except json.JSONDecodeError as e2:
                raise RuntimeError(f"Failed to parse catalog JSON: {e2}")

    def _dispatch(self, name: str, inputs: dict):
        """Dispatch tool calls to their implementations."""
        if name == "get_dataset_profile":
            return get_dataset_profile(inputs["filepath"])
        if name == "validate_column":
            return validate_column(
                inputs["filepath"], inputs["column_name"], inputs["validation_type"]
            )
        if name == "query_semantic_memory":
            return {"results": self.semantic_memory.query_rules(inputs["query"])}
        return {"error": f"Unknown tool: {name}"}


if __name__ == "__main__":
    # Verify API key is loaded
    if not os.getenv("GOOGLE_API_KEY"):
        print("❌ GOOGLE_API_KEY not found.")
        print("Create a .env file with: GOOGLE_API_KEY=AIza...")
        exit(1)

    # Resolve filepath
    if len(sys.argv) >= 2:
        filepath = sys.argv[1]
    else:
        filepath = "input/sample_data.csv"

    agent = ProfileAgent()
    agent.run(filepath=filepath)
