"""
Core profiling functions: load_csv and profile_column.
These analyze CSV structure and column statistics.
"""

import pandas as pd

# Simple cache for the DataFrame
_DF_CACHE = {}


def _get_df(filepath: str) -> pd.DataFrame:
    """Helper to get or load the dataframe."""
    if filepath not in _DF_CACHE:
        _DF_CACHE[filepath] = pd.read_csv(filepath)
    return _DF_CACHE[filepath]


def load_csv(filepath: str) -> dict:
    """
    Load a CSV and return a high-level overview:
    shape, column names, pandas dtypes, null counts, and 5 sample rows.
    """
    try:
        df = _get_df(filepath)
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


def get_dataset_profile(filepath: str) -> dict:
    """
    Load a CSV and return a full profile for all columns at once.
    This avoids iterative file reads.
    """
    try:
        df = _get_df(filepath)
    except Exception as e:
        return {"error": str(e)}

    profiles = {}
    for col_name in df.columns:
        profiles[col_name] = profile_column_from_df(df, col_name)

    return {
        "filepath": filepath,
        "shape": {"rows": len(df), "columns": len(df.columns)},
        "column_profiles": profiles,
    }


def profile_column_from_df(df: pd.DataFrame, column_name: str) -> dict:
    """Helper that profiles a column from an existing dataframe."""
    col = df[column_name]
    total = len(col)
    null_count = int(col.isnull().sum())

    profile = {
        "column_name": column_name,
        "total_rows": total,
        "null_count": null_count,
        "null_percentage": round(null_count / total * 100, 2),
        "unique_count": int(col.nunique()),
        "uniqueness_percentage": round(col.nunique() / (total - null_count) * 100, 2)
        if (total - null_count) > 0
        else 0,
        "sample_values": [str(v) for v in col.dropna().head(20).tolist()],
    }

    if pd.api.types.is_numeric_dtype(col):
        desc = col.describe()
        profile["numeric_stats"] = {
            "min": _safe_float(desc.get("min")),
            "max": _safe_float(desc.get("max")),
            "mean": _safe_float(desc.get("mean")),
            "std": _safe_float(desc.get("std")),
        }

    return profile


def profile_column(filepath: str, column_name: str) -> dict:
    """Deprecated: Use get_dataset_profile for efficiency."""
    try:
        df = _get_df(filepath)
    except Exception as e:
        return {"error": str(e)}
    return profile_column_from_df(df, column_name)


def _safe_float(val) -> float | None:
    """Convert value to float safely, returns None on failure."""
    try:
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None
