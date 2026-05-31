"""
Data Profiling Skill
====================
A reusable, self-contained module for comprehensive CSV data profiling and validation.

This skill provides:
  - CSV loading and overview (shape, dtypes, nulls, samples)
  - Deep column profiling (stats, distribution, type detection)
  - Comprehensive validation (email, phone, duplicates, nulls)
  - Catalog synthesis and reporting (JSON + Markdown)

Import patterns:
  from data_profiling_skill import load_csv, profile_column, validate_column, save_catalog
  from data_profiling_skill.core import ProfileAgent
"""

from .profiling import load_csv, profile_column
from .validation import validate_column
from .catalog import save_catalog
from .agent import ProfileAgent

__version__ = "1.0.0"
__all__ = [
    "load_csv",
    "profile_column",
    "validate_column",
    "save_catalog",
    "ProfileAgent",
]