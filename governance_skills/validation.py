"""
Comprehensive column validation: email, phone, duplicates, nulls.
"""

import re

import pandas as pd

from governance_skills.profiling import _DF_CACHE

# Simple cache for the DataFrame
# _DF_CACHE = {}  <- Removed to use the one from profiling.py


def _get_df(filepath: str) -> pd.DataFrame:
    """Helper to get or load the dataframe."""
    if filepath not in _DF_CACHE:
        _DF_CACHE[filepath] = pd.read_csv(filepath)
    return _DF_CACHE[filepath]


def validate_column(
    filepath: str,
    column_name: str,
    validation_type: str,
    df: pd.DataFrame | None = None,  # IMPROVEMENT: skip file re-read
) -> dict:
    """
    Comprehensive validation of an entire column for specific data quality issues.
    """
    try:
        frame = df if df is not None else _get_df(filepath)
    except Exception as e:
        return {"error": str(e)}

    if column_name not in frame.columns:
        return {"error": f"Column '{column_name}' not found in file."}

    col = frame[column_name]
    results = {
        "column_name": column_name,
        "validation_type": validation_type,
        "total_rows": len(col),
        "issues_found": 0,
        "issues": [],
    }

    if validation_type == "email":
        return _validate_email(col, results)
    elif validation_type == "phone":
        return _validate_phone(col, results)
    elif validation_type == "duplicates":
        return _validate_duplicates(col, results)
    elif validation_type == "null_check":
        return _validate_nulls(col, results)

    return {"error": f"Unknown validation_type: {validation_type}"}


def validate_column_from_df(df, column_name, validation_type):
    return validate_column("__in_memory__", column_name, validation_type, df=df)


def _validate_email(col: pd.Series, results: dict) -> dict:
    """Validate email format with stricter checks."""
    # Stricter regex to ensure domain has at least one character before the dot, and a valid TLD
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}$"

    # Custom check: ensure domain part doesn't start/end with dot
    def is_valid_format(email):
        if not re.match(email_pattern, email):
            return False
        # Specific check for "user@.com" or "user@domain..com"
        domain_part = email.split("@")[1]
        if domain_part.startswith(".") or ".." in domain_part:
            return False
        return True

    invalid_emails = []
    duplicate_emails = {}
    valid_emails = []

    for idx, value in col.items():
        if pd.isna(value):
            continue

        value_str = str(value).strip()

        if not is_valid_format(value_str):
            invalid_emails.append(
                {
                    "row": int(idx) + 1,
                    "value": value_str,
                    "issue": "Invalid email format",
                }
            )
        else:
            if value_str in duplicate_emails:
                duplicate_emails[value_str].append(int(idx) + 1)
            else:
                duplicate_emails[value_str] = [int(idx) + 1]
            valid_emails.append(value_str)

    results["invalid_emails_count"] = len(invalid_emails)
    results["issues"].extend(invalid_emails)

    duplicate_valid_emails = {
        email: rows for email, rows in duplicate_emails.items() if len(rows) > 1
    }
    results["duplicate_valid_emails_count"] = len(duplicate_valid_emails)
    for email, row_numbers in duplicate_valid_emails.items():
        results["issues"].append(
            {
                "value": email,
                "issue_type": "duplicate_valid_email",
                "occurrence_count": len(row_numbers),
                "row_numbers": row_numbers,
            }
        )

    results["issues_found"] = len(invalid_emails) + len(duplicate_valid_emails)
    results["valid_emails_count"] = len(set(valid_emails))
    results["invalid_emails"] = [item["value"] for item in invalid_emails]

    return results


def _validate_phone(col: pd.Series, results: dict) -> dict:
    """Validate phone format (flexible)."""
    PHONE_RE = re.compile(
        r"^(\+?\d{1,3}[\s\-.]?)?"
        r"(\(?\d{2,4}\)?[\s\-.]?)"
        r"\d{3,4}[\s\-.]?\d{4}$"
    )

    invalid_phones = []
    valid_phones = []

    for idx, value in col.items():
        if pd.isna(value):
            continue

        value_str = str(value).strip()

        # Digit-only fast-path
        cleaned = re.sub(r"[\s\-().+]", "", value_str)
        if 7 <= len(cleaned) <= 15 and cleaned.isdigit():
            valid_phones.append(value_str)
            continue

        if not PHONE_RE.match(value_str):
            invalid_phones.append(
                {
                    "row": int(idx) + 1,
                    "value": value_str,
                    "issue": "Invalid phone format",
                }
            )
        else:
            valid_phones.append(value_str)

    results["invalid_phones_count"] = len(invalid_phones)
    results["issues"].extend(invalid_phones)
    results["valid_phones_count"] = len(set(valid_phones))
    results["issues_found"] = len(invalid_phones)

    return results


def _validate_duplicates(col: pd.Series, results: dict) -> dict:
    """Find all duplicate values."""
    duplicates = col[col.duplicated(keep=False)].value_counts()
    for value, count in duplicates.items():
        if count > 1:
            row_numbers = col[col == value].index.tolist()
            results["issues"].append(
                {
                    "value": str(value),
                    "occurrence_count": int(count),
                    "row_numbers": [int(r) + 1 for r in row_numbers],
                }
            )
    results["issues_found"] = len(results["issues"])
    return results


def _validate_nulls(col: pd.Series, results: dict) -> dict:
    """Find all null/empty values."""
    for idx, value in col.items():
        if pd.isna(value) or str(value).strip() == "":
            results["issues"].append(
                {
                    "row": int(idx) + 1,
                    "value": str(value) if not pd.isna(value) else "NULL",
                }
            )
    results["issues_found"] = len(results["issues"])
    results["non_null_count"] = len(col.dropna())
    return results
