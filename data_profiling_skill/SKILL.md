# 📊 Data Profiling Skill

A **self-contained, reusable skill** for comprehensive CSV data profiling, validation, and metadata catalog generation.

## Overview

This skill packages all the logic needed to:
- ✅ Load and understand CSV structure
- ✅ Profile individual columns with deep statistics
- ✅ Validate data quality (emails, phones, duplicates, nulls)
- ✅ Synthesize professional metadata catalogs (JSON + Markdown)

Perfect for any agent that needs to understand or govern data.

---

## When to Use This Skill

| Scenario | Use This Skill |
|----------|---|
| Building a data catalog system | ✅ Yes |
| Understanding a new dataset | ✅ Yes |
| Validating data quality in a pipeline | ✅ Yes |
| Adding data governance to workflows | ✅ Yes |
| Automating PII/sensitivity detection | ✅ Yes |
| Generating data dictionaries | ✅ Yes |

---

## Installation & Imports

### As a module in your project:
```python 
from data_profiling_skill import load_csv, profile_column, validate_column, save_catalog
from data_profiling_skill import ProfileAgent
``` 

### Use the full agent:
```python
from data_profiling_skill import ProfileAgent
agent = ProfileAgent() output_paths = agent.run("path/to/your/data.csv")
# Returns: {"json_path": "...", "md_path": "..."}```
```

### Use individual functions:
```python 
from data_profiling_skill import load_csv, profile_column
# Step 1: Load the CSV
overview = load_csv("data.csv") print(f"Rows: {overview['shape']['rows']}, Columns: {overview['shape']['columns']}")
# Step 2: Profile a specific column
profile = profile_column("data.csv", "email_column") print(f"Null %: {profile['null_percentage']}") print(f"Unique values: {profile['unique_count']}")
``` 

---

## Core Functions

### `load_csv(filepath: str) → dict`

Load a CSV and return basic structure info.

**Parameters:**
- `filepath` (str): Path to the CSV file

**Returns:** Dictionary with:
- `filepath`: Input file path
- `shape`: Dict with `rows` and `columns` count
- `columns`: List of column names
- `pandas_dtypes`: Column data types
- `null_counts`: Null count per column
- `sample_rows`: First 5 rows as records

**Example:**
```python 
overview = load_csv("data.csv") print(f"Dataset: {overview['shape']['rows']} rows × {overview['shape']['columns']} columns")
``` 

---

### `profile_column(filepath: str, column_name: str) → dict`

Deep statistical analysis of a single column. Scans **all rows** for complete accuracy.

**Parameters:**
- `filepath` (str): Path to the CSV file
- `column_name` (str): Column to analyze

**Returns:** Dictionary with:
- `column_name`: Name of analyzed column
- `total_rows`: Row count
- `null_count` & `null_percentage`: Nulls in column
- `unique_count` & `uniqueness_percentage`: Distinct values
- `sample_values`: Up to 10 unique sample values
- `numeric_stats` (optional): min, max, mean, std, percentiles for numeric columns
- `value_distribution` (optional): Count of each value for low-cardinality columns (<50 unique)
- `looks_like_date` (optional): Boolean if column appears to be a date

**Example:**
```python 
profile = profile_column("data.csv", "customer_email") print(f"Nulls: {profile['null_percentage']}%") print(f"Uniqueness: {profile['uniqueness_percentage']}%") print(f"Samples: {profile['sample_values']}")
``` 

---

### `validate_column(filepath: str, column_name: str, validation_type: str) → dict`

Comprehensive validation of a column for data quality issues. Scans **all rows**.

**Parameters:**
- `filepath` (str): Path to the CSV file
- `column_name` (str): Column to validate
- `validation_type` (str): One of:
  - `"email"` — RFC 5322 email format validation
  - `"phone"` — Phone format validation (flexible)
  - `"duplicates"` — Find all duplicate values
  - `"null_check"` — Find all null/empty values

**Returns:** Dictionary with:
- `column_name`: Column name
- `validation_type`: Type of validation performed
- `total_rows`: Total rows scanned
- `issues_found`: Count of issues detected
- `issues`: Array of issue objects with row numbers and values
- Type-specific fields (e.g., `invalid_emails_count`, `valid_emails_count`, etc.)

**Example:**
```python
# Check for invalid emails
validation = validate_column("data.csv", "email", "email") if validation["invalid_emails_count"] > 0: print(f"⚠️ Found {validation['invalid_emails_count']} invalid emails") for issue in validation["issues"][:3]: print(f" Row {issue['row']}: {issue['value']}")
# Check for duplicates
dupes = validate_column("data.csv", "user_id", "duplicates") if dupes["issues_found"] > 0: print(f"Found {len(dupes['issues'])} duplicate groups")
``` 

---

### `save_catalog(catalog: dict, output_dir: str = "output") → dict`

Save a full metadata catalog as JSON and Markdown files.

**Parameters:**
- `catalog` (dict): Full catalog dictionary
- `output_dir` (str): Output directory (default: "output")

**Returns:** Dictionary with:
- `json_path`: Path to generated JSON file
- `md_path`: Path to generated Markdown report

**Example:**
```python
paths = save_catalog(my_catalog) print(f"JSON: {paths['json_path']}") print(f"Report: {paths['md_path']}")
``` 

---

### `ProfileAgent` Class

Two-phase orchestrator for end-to-end profiling and catalog synthesis.

**Methods:**

#### `__init__()`
Initialize the agent (sets up Anthropic client).

#### `run(filepath: str) → dict`
Execute full pipeline: profile → synthesize → save

- **Phase 1:** Profiles all columns via ReAct tool loop
- **Phase 2:** Synthesizes metadata catalog via Claude LLM

**Returns:** Dictionary with `json_path` and `md_path` of outputs

**Example:**
```python 
from data_profiling_skill import ProfileAgent
agent = ProfileAgent() output_paths = agent.run("sales_data.csv") print(f"✅ Catalog saved to {output_paths['md_path']}")
``` 

---

## Module Structure
```
data_profiling_skill/ 
├── init.py # Public API exports 
├── profiling.py # load_csv(), profile_column() 
├── validation.py # validate_column() with all validators 
├── catalog.py # save_catalog(), markdown generation 
├── agent.py # ProfileAgent orchestrator 
└── SKILL.md # This documentation``` 
```
---

## Architecture & Design

### Modular Design
Each function is **independent and reusable**:
- Use `load_csv()` alone for quick dataset discovery
- Chain `profile_column()` for each column
- Add `validate_column()` for specific data quality checks
- Or use `ProfileAgent` for the complete workflow

### Two-Phase Agent Architecture
The `ProfileAgent` follows a proven pattern:
1. **Phase 1 (ReAct Loop):** Calls `load_csv` → `profile_column` for each column → `validate_column` for identified columns
2. **Phase 2 (One-shot):** Hands all collected profiles to Claude to synthesize the final catalog JSON

This avoids token budget conflicts when dealing with large datasets.

### Data Flow
```
CSV File ↓ load_csv() → shape, columns, dtypes, nulls ↓ profile_column() → statistics, distribution, uniqueness [×N columns] ↓ validate_column() → quality issues with row numbers [×K validations] ↓ ProfileAgent (Phase 2) → Claude synthesizes metadata catalog ↓ save_catalog() → JSON + Markdown outputs
```
---

## Example Workflows

### Workflow 1: Quick Data Understanding

```python
from data_profiling_skill import load_csv, profile_column

# Overview
overview = load_csv("dataset.csv")
print(f"📊 {overview['shape']['rows']:,} rows × {overview['shape']['columns']} columns")
print(f"Columns: {', '.join(overview['columns'])}")

# Profile key columns
for col in ["email", "phone", "created_at"]:
    if col in overview['columns']:
        profile = profile_column("dataset.csv", col)
        print(f"\n{col}:")
        print(f"  Nulls: {profile['null_percentage']}%")
        print(f"  Unique: {profile['uniqueness_percentage']}%")
```

### Workflow 2: Data Quality Audit
```python
from data_profiling_skill import validate_column

checks = {
    "email": "email",
    "phone": "phone",
    "customer_id": "duplicates",
    "name": "null_check"
}

for col, check_type in checks.items():
    result = validate_column("data.csv", col, check_type)
    if result["issues_found"] > 0:
        print(f"⚠️ {col}: {result['issues_found']} issues found")
```

### Workflow 3: Full Catalog Generation
```python
from data_profiling_skill import ProfileAgent

agent = ProfileAgent()
paths = agent.run("company_data.csv")
print(f"✅ Catalog saved:\n  {paths['md_path']}\n  {paths['json_path']}")
```

### Integration with Claude Agents
This skill is designed to be imported by any Claude agent as a reusable tool library:
```python
# In your agent code
from data_profiling_skill import load_csv, profile_column, validate_column

# Register tools
TOOLS = [
    {
        "name": "load_csv",
        "description": "Load CSV and get shape, columns, dtypes, nulls, sample rows",
        "input_schema": {...}
    },
    {
        "name": "profile_column",
        "description": "Deep profile of one column: nulls, uniqueness, stats, distribution",
        "input_schema": {...}
    },
    # ... etc
]


# In your tool dispatcher
def dispatch(tool_name, inputs):
    if tool_name == "load_csv":
        return load_csv(inputs["filepath"])
    # ... etc
```

 
### Configuration & Extensibility
### Adding a New Validation Type
Edit `validation.py` to add a custom validator:
```python
def validate_column(filepath, column_name, validation_type):
    # ...
    elif validation_type == "my_format":
    return _validate_my_format(col, results)


def _validate_my_format(col, results):
    # Your validation logic
    for idx, value in col.items():
        # Check condition
        if not is_valid(value):
            results["issues"].append({
                "row": idx + 1,
                "value": str(value),
                "issue": "Description of problem"
            })
    results["issues_found"] = len(results["issues"])
    return results
```

### Customizing the Markdown Report
Edit `catalog.py`'s `_build_markdown()` function to:
- Change column layout and styling
- Add/remove sections
- Customize emoji and formatting
- Add organizational branding
 
### Performance Notes
- **CSV Size**: Functions load entire CSV into memory via pandas. For files >1GB, consider chunking or using dask.
- **Deep Profiling**: Analysis scans ALL rows for 100% accuracy (no sampling bias).
- **Validation Speed**: Email/phone regex applied to every value in every row.
- **Large Datasets**: The two-phase agent architecture handles 20+ columns efficiently.
 
### Error Handling
All functions return structured dictionaries. Errors are signaled via an `"error"` key:
```python
result = load_csv("nonexistent.csv")
if "error" in result:
    print(f"Error loading CSV: {result['error']}")
else:
    print(f"Loaded {result['shape']['rows']} rows")
```

 
### Dependencies
- **pandas** — CSV loading and analysis
- **anthropic** — For ProfileAgent only (optional if using individual functions)
- **Python 3.8+**
 
### What's Next?
After generating a catalog:
1. **Extend validations** — Add custom regex patterns for your domain
2. **PII detection** — Integrate ML models for sensitive field identification 
3. **Trend tracking** — Run profiles over time to monitor data quality drift 
4. **API wrapper** — Expose via FastAPI as a microservice 
5. **Custom outputs** — Generate reports in your org's preferred format 
6. **Integration** — Wire into data pipelines, dbt workflows, or data platforms
 
### Summary
The **Data Profiling Skill** is a production-ready, modular system for understanding and cataloging data. Use individual functions for flexibility, or the `ProfileAgent` for turnkey end-to-end profiling.\
**Key strengths**:
- ✅ Self-contained and portable
- ✅ Modular — use what you need
- ✅ Reusable across projects
- ✅ Extensible — add validators easily
- ✅ Integration-ready — designed for agents and pipelines
