"""
Core profiling functions: load_csv and profile_column.
These analyze CSV structure and column statistics.
"""

import pandas as pd


def load_csv(filepath: str) -> dict:
    """
    Load a CSV and return a high-level overview:
    shape, column names, pandas dtypes, null counts, and 5 sample rows.
    
    Args:
        filepath: Path to CSV file
        
    Returns:
        dict with keys: filepath, shape, columns, pandas_dtypes, null_counts, sample_rows
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


def profile_column(filepath: str, column_name: str) -> dict:
    """
    Return a detailed statistical profile for a single column.
    Analyzes all rows for complete accuracy.
    
    Args:
        filepath: Path to CSV file
        column_name: Name of column to profile
        
    Returns:
        dict with comprehensive column statistics
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
            for k, v in col.value_counts().items()
        }

    # Detect if column looks like a date (scan ALL non-null values)
    if col.dtype == object:
        sample = col.dropna()
        if len(sample) > 0:
            parsed = pd.to_datetime(sample, errors="coerce", infer_datetime_format=True)
            if parsed.notna().sum() >= len(sample) * 0.8:
                profile["looks_like_date"] = True

    return profile


def _safe_float(val) -> float | None:
    """Convert value to float safely, returns None on failure."""
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None