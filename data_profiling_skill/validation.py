"""
Comprehensive column validation: email, phone, duplicates, nulls.
"""

import pandas as pd
import re


def validate_column(filepath: str, column_name: str, validation_type: str) -> dict:
    """
    Comprehensive validation of an entire column for specific data quality issues.

    Args:
        filepath: Path to CSV file
        column_name: Name of column to validate
        validation_type: One of "email", "phone", "duplicates", "null_check"

    Returns:
        dict with validation results and all violations listed with row numbers
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
        return _validate_email(col, results)
    elif validation_type == "phone":
        return _validate_phone(col, results)
    elif validation_type == "duplicates":
        return _validate_duplicates(col, results)
    elif validation_type == "null_check":
        return _validate_nulls(col, results)
    
    return {"error": f"Unknown validation_type: {validation_type}"}


def _validate_email(col: pd.Series, results: dict) -> dict:
    """Validate email format (RFC 5322 simplified)."""
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    invalid_emails = []
    duplicate_emails = {}
    valid_emails = []
    
    for idx, value in col.items():
        if pd.isna(value):
            continue
        
        value_str = str(value).strip()
        
        if not re.match(email_pattern, value_str):
            invalid_emails.append({
                "row": int(idx) + 1,
                "value": value_str,
                "issue": "Invalid email format (does not match RFC 5322 pattern)"
            })
        else:
            if value_str in duplicate_emails:
                duplicate_emails[value_str].append(int(idx) + 1)
            else:
                duplicate_emails[value_str] = [int(idx) + 1]
            valid_emails.append(value_str)
    
    results["invalid_emails_count"] = len(invalid_emails)
    results["issues"].extend(invalid_emails)
    
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
    
    return results


def _validate_phone(col: pd.Series, results: dict) -> dict:
    """Validate phone format (flexible)."""
    phone_pattern = r'^[\+]?[\d\s\-\(\)\.]{7,}$'
    
    invalid_phones = []
    valid_phones = []
    
    for idx, value in col.items():
        if pd.isna(value):
            continue
        
        value_str = str(value).strip()
        
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
    
    return results


def _validate_duplicates(col: pd.Series, results: dict) -> dict:
    """Find all duplicate values."""
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
    return results


def _validate_nulls(col: pd.Series, results: dict) -> dict:
    """Find all null/empty values."""
    for idx, value in col.items():
        if pd.isna(value) or str(value).strip() == "":
            results["issues"].append({
                "row": int(idx) + 1,
                "value": str(value) if not pd.isna(value) else "NULL"
            })
    results["issues_found"] = len(results["issues"])
    results["non_null_count"] = len(col.dropna())
    return results