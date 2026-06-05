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
from concurrent.futures import ThreadPoolExecutor, as_completed

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

MODEL = os.getenv("GOVERNANCE_MODEL", "gemini-3.1-flash-lite")
MAX_SAMPLE_VALUES = int(os.getenv("MAX_SAMPLE_VALUES", "3"))
MAX_PROFILING_ITER = int(os.getenv("MAX_PROFILING_ITERATIONS", "30"))
MAX_VALIDATION_WORKERS = int(os.getenv("MAX_VALIDATION_WORKERS", "4"))


def _retry_with_backoff(fn, max_retries=3, base_wait=60):
    """Retry fn() on 429 with capped exponential backoff."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait = min(base_wait * (2**attempt), 300)
                print(
                    f"  Rate limit. Retrying in {wait}s ({attempt + 1}/{max_retries})..."
                )
                time.sleep(wait)
            else:
                raise


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
  stats, quality_observations, business_glossary_term, recommended_constraints,
  quality_score (0-100 integer)

Root object: { "source_file": "<filename>", "total_rows": <int>, "columns": [...], "overall_quality_score": <int> }"""


class ProfileAgent:
    """Orchestrates two-phase data profiling and catalog synthesis."""

    def __init__(self):
        self.client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        self.model = MODEL
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

        collected = self._run_parallel_validation(filepath, collected)

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

    def _run_parallel_validation(self, filepath: str, collected: dict) -> dict:
        """
        Detect columns that still need validation by inspecting sample_values
        with a regex, then run all validate_column() calls in parallel threads.
        Skips columns that already have validation_results from the ReAct loop.
        """
        profiles = collected.get("column_profiles", {})
        tasks = []
        for col_name, prof in profiles.items():
            if "validation_results" in prof:
                continue
            samples = " ".join(str(v) for v in prof.get("sample_values", []))
            if re.search(r"@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", samples):
                tasks.append((col_name, "email"))
            elif re.search(r"\+?[0-9][\s\-().]{6,}", samples):
                tasks.append((col_name, "phone"))

        if not tasks:
            return collected

        def _do(col_name, vtype):
            return col_name, validate_column(filepath, col_name, vtype)

        with ThreadPoolExecutor(max_workers=MAX_VALIDATION_WORKERS) as pool:
            futures = {pool.submit(_do, col, vtype): col for col, vtype in tasks}
            for future in as_completed(futures):
                col_name, result = future.result()
                result = self._sanitize_result(result)
                if col_name in profiles:
                    profiles[col_name]["validation_results"] = result

        collected["column_profiles"] = profiles
        return collected

    def _sanitize_result(self, result):
        """Recursively replace NaN with None in dictionaries/lists for valid JSON."""
        if isinstance(result, float) and (math.isnan(result) or math.isinf(result)):
            return None
        elif isinstance(result, dict):
            return {k: self._sanitize_result(v) for k, v in result.items()}
        elif isinstance(result, list):
            return [self._sanitize_result(i) for i in result]
        return result

    def _safe_json_parse(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            fixed = re.sub(r",(\s*[}\]])", r"\1", raw)
            fixed = re.sub(
                r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)", r'\1"\2"\3', fixed
            )
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as e:
                print(f"  WARNING JSON parse failed: {e}")
                # IMPROVEMENT: return partial dict instead of raising RuntimeError
                return {"parse_error": str(e), "raw_response": raw[:2000]}

    def _get_feedback_context(self) -> str:
        """Surface historical feedback avg to calibrate synthesis verbosity."""
        try:
            stats = self.episode_store.get_feedback_stats()
            if stats and stats.get("count", 0) > 0:
                avg = stats["avg_rating"]
                if avg < 3.0:
                    return "FEEDBACK CONTEXT: Previous runs averaged LOW quality. Produce MORE DETAILED descriptions.\n\n"
                if avg >= 4.0:
                    return "FEEDBACK CONTEXT: Previous runs averaged HIGH quality. Keep descriptions CONCISE.\n\n"
        except Exception:
            pass
        return ""

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
        profiles = collected.get("column_profiles", {})
        overview = collected.get("overview", {})

        pruned = {
            col: {
                **prof,
                "sample_values": prof.get("sample_values", [])[:MAX_SAMPLE_VALUES],
            }
            for col, prof in profiles.items()
        }

        mandatory_facts = self._build_validation_facts(profiles)
        relevant_rules = self._gather_governance_rules(profiles)
        rules_text = (
            "\n".join(f"- {r}" for r in relevant_rules) or "No specific rules found."
        )
        feedback_ctx = self._get_feedback_context()

        user_msg = (
            f"Source file: {filepath}\n"
            f"Dataset shape: {overview.get('shape', 'unknown')}\n\n"
            f"{mandatory_facts}\n"
            f"Column profiles (pruned):\n{json.dumps(pruned, indent=2, default=str)}\n\n"
            f"GOVERNANCE GUIDELINES:\n{rules_text}\n\n"
            f"{feedback_ctx}"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. Use validation facts above for data quality observations\n"
            f"2. Include a quality_score (0-100) per column and overall_quality_score\n"
            f"3. Add validation-based recommended_constraints\n"
            f"4. Do NOT re-validate -- trust the automated tool results"
        )

        print("  🧠  Synthesising catalog...")
        response = _retry_with_backoff(
            lambda: self.client.models.generate_content(
                model=self.model,
                contents=user_msg,
                config=types.GenerateContentConfig(system_instruction=SYNTHESIS_SYSTEM),
            )
        )

        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return self._safe_json_parse(raw)

    def _build_validation_facts(self, profiles: dict) -> str:
        lines = ["VALIDATION FACTS (from automated scanning tool):\n"]
        for col_name, profile in profiles.items():
            vr = profile.get("validation_results")
            if not vr:
                continue
            vtype = vr.get("validation_type", "")
            lines.append(f"{col_name.upper()}:")
            if vtype == "email":
                lines.append(
                    f"  Invalid: {vr.get('invalid_emails_count', 0)} | "
                    f"Valid: {vr.get('valid_emails_count', 0)} | "
                    f"Duplicates: {vr.get('duplicate_valid_emails_count', 0)}"
                )
                if vr.get("invalid_emails_count", 0):
                    lines.append(f"     Examples: {vr.get('invalid_emails', [])[:3]}")
            elif vtype == "phone":
                lines.append(f"  Invalid phones: {vr.get('invalid_phones_count', 0)}")
            elif vtype == "duplicates":
                lines.append(f"  Duplicates: {vr.get('issues_found', 0)}")
            elif vtype == "null_check":
                lines.append(f"  Nulls: {vr.get('null_count', 0)}")
            lines.append("")
        return "\n".join(lines)

    def _gather_governance_rules(self, profiles: dict) -> list:
        rules = []
        for col_name, profile in profiles.items():
            q = f"Profiling column {col_name} with sample values {profile.get('sample_values', [])[:3]}"
            rules.extend(
                r["text"] for r in self.semantic_memory.query_rules(q, limit=2)
            )
        return list(set(rules))

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
