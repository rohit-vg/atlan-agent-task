"""
tools.py — CSV profiling tools used by the catalog agent.
Each function maps 1:1 to a tool the Claude agent can call.
"""

import pandas as pd
import json
import os
from datetime import datetime


# ─────────────────────────────────────────────
# Tool 1: Load CSV overview
# ─────────────────────────────────────────────
def load_csv(filepath: str) -> dict:
    """
    Load a CSV and return a high-level overview:
    shape, column names, pandas dtypes, null counts, and 5 sample rows.
    """
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        return {"error": str(e)}

    return {
        "filepath": filepath,
        "shape": {"rows": len(df), "columns": len(df.columns)},
        "columns": list(df.columns),
        "pandas_dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
        "sample_rows": df.head(5).to_dict(orient="records"),
    }


# ─────────────────────────────────────────────
# Tool 2: Profile a single column
# ─────────────────────────────────────────────
def profile_column(filepath: str, column_name: str) -> dict:
    """
    Return a detailed statistical profile for a single column:
    nulls, uniqueness, sample values, numeric stats, value distribution.
    NOW ANALYZES ALL ROWS for complete accuracy.
    """
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        return {"error": str(e)}

    if column_name not in df.columns:
        return {"error": f"Column '{column_name}' not found in file."}

    col = df[column_name]
    total = len(col)
    null_count = int(col.isnull().sum())

    profile = {
        "column_name": column_name,
        "total_rows": total,
        "null_count": null_count,
        "null_percentage": round(null_count / total * 100, 2),
        "unique_count": int(col.nunique()),
        "uniqueness_percentage": round(col.nunique() / (total - null_count) * 100, 2) if (total - null_count) > 0 else 0,
        "sample_values": [str(v) for v in col.dropna().unique()[:10].tolist()],
    }

    # Numeric columns: add descriptive stats (ALL ROWS)
    if pd.api.types.is_numeric_dtype(col):
        desc = col.describe()
        profile["numeric_stats"] = {
            "min": _safe_float(desc.get("min")),
            "max": _safe_float(desc.get("max")),
            "mean": _safe_float(desc.get("mean")),
            "std": _safe_float(desc.get("std")),
            "25th_percentile": _safe_float(desc.get("25%")),
            "75th_percentile": _safe_float(desc.get("75%")),
        }

    # Low-cardinality columns: add value distribution (ALL ROWS)
    if col.nunique() <= 50:
        profile["value_distribution"] = {
            str(k): int(v)
            for k, v in col.value_counts().items()  # Changed: removed .head(15) to get ALL values
        }

    # Detect if column looks like a date (scan ALL non-null values)
    if col.dtype == object:
        sample = col.dropna()  # Changed: use ALL non-null values instead of .head(20)
        if len(sample) > 0:
            parsed = pd.to_datetime(sample, errors="coerce", infer_datetime_format=True)
            if parsed.notna().sum() >= len(sample) * 0.8:
                profile["looks_like_date"] = True

    return profile


# ─────────────────────────────────────────────
# Tool 3: Validate column values (NEW)
# ─────────────────────────────────────────────
def validate_column(filepath: str, column_name: str, validation_type: str) -> dict:
    """
    Comprehensive validation of an entire column for specific data quality issues.

    validation_type options:
    - "email"           → Check all values match RFC 5322 email pattern, separate invalid from duplicates
    - "phone"           → Check all values match phone format patterns (flexible)
    - "null_check"      → Report all null/empty values with row numbers
    - "duplicates"      → Find all duplicate values with occurrence count

    Returns comprehensive results including exact row numbers of violations.
    Works with ANY column from any CSV - fully generic.
    """
    try:
        df = pd.read_csv(filepath)
    except Exception as e:
        return {"error": str(e)}

    if column_name not in df.columns:
        return {"error": f"Column '{column_name}' not found in file."}

    col = df[column_name]
    results = {
        "column_name": column_name,
        "validation_type": validation_type,
        "total_rows": len(col),
        "issues_found": 0,
        "issues": [],
    }

    if validation_type == "email":
        import re
        # RFC 5322 simplified pattern (more lenient but catches basic issues)
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        
        invalid_emails = []
        duplicate_emails = {}
        valid_emails = []
        
        for idx, value in col.items():
            if pd.isna(value):
                continue
            
            value_str = str(value).strip()
            
            # Check if email matches RFC pattern
            if not re.match(email_pattern, value_str):
                invalid_emails.append({
                    "row": int(idx) + 1,
                    "value": value_str,
                    "issue": "Invalid email format (does not match RFC 5322 pattern)"
                })
            else:
                # Valid email - track for duplicates
                if value_str in duplicate_emails:
                    duplicate_emails[value_str].append(int(idx) + 1)
                else:
                    duplicate_emails[value_str] = [int(idx) + 1]
                valid_emails.append(value_str)
        
        # Add invalid email issues first
        results["invalid_emails_count"] = len(invalid_emails)
        results["issues"].extend(invalid_emails)
        
        # Add duplicate valid emails (exclude those with only one occurrence)
        duplicate_valid_emails = {email: rows for email, rows in duplicate_emails.items() if len(rows) > 1}
        results["duplicate_valid_emails_count"] = len(duplicate_valid_emails)
        for email, row_numbers in duplicate_valid_emails.items():
            results["issues"].append({
                "value": email,
                "issue_type": "duplicate_valid_email",
                "occurrence_count": len(row_numbers),
                "row_numbers": row_numbers
            })
        
        results["issues_found"] = len(invalid_emails) + len(duplicate_valid_emails)
        results["valid_emails_count"] = len(set(valid_emails))
        results["invalid_emails"] = [item["value"] for item in invalid_emails]

    elif validation_type == "phone":
        import re
        # Generic phone pattern: allows various formats including +X-XXX-XXX-XXXX, (XXX) XXX-XXXX, XXX-XXX-XXXX
        phone_pattern = r'^[\+]?[\d\s\-\(\)\.]{7,}$'
        
        invalid_phones = []
        valid_phones = []
        
        for idx, value in col.items():
            if pd.isna(value):
                continue
            
            value_str = str(value).strip()
            
            # Check if phone matches generic pattern
            if not re.match(phone_pattern, value_str):
                invalid_phones.append({
                    "row": int(idx) + 1,
                    "value": value_str,
                    "issue": "Invalid phone format"
                })
            else:
                valid_phones.append(value_str)
        
        results["invalid_phones_count"] = len(invalid_phones)
        results["issues"].extend(invalid_phones)
        results["valid_phones_count"] = len(set(valid_phones))
        results["issues_found"] = len(invalid_phones)

    elif validation_type == "duplicates":
        duplicates = col[col.duplicated(keep=False)].value_counts()
        for value, count in duplicates.items():
            if count > 1:
                row_numbers = col[col == value].index.tolist()
                results["issues"].append({
                    "value": str(value),
                    "occurrence_count": int(count),
                    "row_numbers": [int(r) + 1 for r in row_numbers]
                })
        results["issues_found"] = len(results["issues"])

    elif validation_type == "null_check":
        for idx, value in col.items():
            if pd.isna(value) or str(value).strip() == "":
                results["issues"].append({
                    "row": int(idx) + 1,
                    "value": str(value) if not pd.isna(value) else "NULL"
                })
        results["issues_found"] = len(results["issues"])
        results["non_null_count"] = len(col.dropna())

    return results


# ─────────────────────────────────────────────
# Tool 3: Save the final catalog
# ─────────────────────────────────────────────
def save_catalog(catalog: dict, output_dir: str = "output") -> dict:
    """
    Save the final catalog as both a JSON file and a human-readable
    Markdown report. Returns the paths of both output files.
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    source = os.path.basename(catalog.get("source_file", "dataset"))

    json_path = f"{output_dir}/catalog_{source}_{timestamp}.json"
    md_path   = f"{output_dir}/catalog_{source}_{timestamp}.md"

    # ── JSON ──────────────────────────────────
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, default=str, ensure_ascii=False)

    # ── Markdown ──────────────────────────────
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_build_markdown(catalog))

    return {"json_path": json_path, "md_path": md_path}


# ─────────────────────────────────────────────
# Markdown report builder
# ─────────────────────────────────────────────
def _build_markdown(catalog: dict) -> str:
    source   = catalog.get("source_file", "Unknown")
    rows     = catalog.get("total_rows", "?")
    cols     = catalog.get("columns", [])
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    PII_EMOJI = {"none": "✅", "low": "🟡", "medium": "🟠", "high": "🔴"}

    lines = [
        f"# 📊 Data Catalog — `{source}`",
        f"",
        f"| | |",
        f"|---|---|",
        f"| **Generated** | {generated} |",
        f"| **Rows** | {rows:,} |",
        f"| **Columns** | {len(cols)} |",
        f"",
        "---",
        "",
        "## Column Catalog",
        "",
    ]

    for col in cols:
        name = col.get("name", "?")
        pii  = col.get("pii_risk", "none")

        lines += [
            f"### `{name}`  {PII_EMOJI.get(pii, '❓')}",
            f"",
            f"> {col.get('description', 'No description.')}",
            f"",
        ]

        # Core metadata table
        lines += [
            f"| Property | Value |",
            f"|---|---|",
            f"| **Semantic Type** | `{col.get('semantic_type', '—')}` |",
            f"| **Data Type** | `{col.get('data_type', '—')}` |",
            f"| **PII Risk** | {PII_EMOJI.get(pii, '❓')} {pii.capitalize()} |",
            f"| **Nullable** | {'Yes' if col.get('nullable') else 'No'} |",
            f"| **Null %** | {col.get('null_percentage', 0)}% |",
            f"| **Unique %** | {col.get('uniqueness_percentage', 0)}% |",
        ]

        if col.get("business_glossary_term"):
            lines.append(f"| **Glossary Term** | {col['business_glossary_term']} |")

        if col.get("stats"):
            s = col["stats"]
            lines.append(f"| **Stats** | min={s.get('min')}  max={s.get('max')}  mean={s.get('mean')} |")

        lines.append("")

        # Tags
        tags = col.get("tags", [])
        if tags:
            tag_str = " ".join(f"`{t}`" for t in tags)
            lines.append(f"**Tags:** {tag_str}")
            lines.append("")

        # Sample values
        samples = col.get("sample_values", [])
        if samples:
            lines.append(f"**Sample values:** `{'`, `'.join(str(v) for v in samples[:6])}`")
            lines.append("")

        # Constraints
        constraints = col.get("recommended_constraints", [])
        if constraints:
            lines.append(f"**Recommended constraints:** {', '.join(f'`{c}`' for c in constraints)}")
            lines.append("")

        # Quality observations
        if col.get("quality_observations"):
            lines += [
                f"⚠️ **Quality note:** {col['quality_observations']}",
                "",
            ]

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _safe_float(val) -> float | None:
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None
