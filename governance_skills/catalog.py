"""
Catalog synthesis, formatting, and file output (JSON + Markdown).
"""

import json
import os
from datetime import datetime


def save_catalog(catalog: dict, output_dir: str = "output") -> dict:
    """
    Save the final catalog as both JSON and human-readable Markdown.

    Args:
        catalog: Full catalog dict to save
        output_dir: Directory for output files

    Returns:
        dict with paths to generated JSON and Markdown files
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    source = os.path.basename(catalog.get("source_file", "dataset"))

    json_path = f"{output_dir}/catalog_{source}_{timestamp}.json"
    md_path = f"{output_dir}/catalog_{source}_{timestamp}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, default=str, ensure_ascii=False)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_build_markdown(catalog))

    return {"json_path": json_path, "md_path": md_path}


def _build_markdown(catalog: dict) -> str:
    """Generate human-readable Markdown report from catalog."""
    source = catalog.get("source_file", "Unknown")
    rows = catalog.get("total_rows", "?")
    cols = catalog.get("columns", [])
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    PII_EMOJI = {"none": "✅", "low": "🟡", "medium": "🟠", "high": "🔴"}

    lines = [
        f"# 📊 Data Catalog — `{source}`",
        f"",
        f"| | |",
        f"|---|---|",
        f"| **Generated** | {generated} |",
        f"| **Rows** | {rows} |",
        f"| **Columns** | {len(cols)} |",
        f"",
        "---",
        "",
        "## Column Catalog",
        "",
    ]

    for col in cols:
        name = col.get("name", "?")
        pii = col.get("pii_risk", "none")

        lines += [
            f"### `{name}`  {PII_EMOJI.get(pii, '❓')}",
            f"",
            f"> {col.get('description', 'No description.')}",
            f"",
        ]

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
            lines.append(
                f"| **Stats** | min={s.get('min')}  max={s.get('max')}  mean={s.get('mean')} |"
            )

        lines.append("")

        tags = col.get("tags", [])
        if tags:
            tag_str = " ".join(f"`{t}`" for t in tags)
            lines.append(f"**Tags:** {tag_str}")
            lines.append("")

        samples = col.get("sample_values", [])
        if samples:
            lines.append(
                f"**Sample values:** `{'`, `'.join(str(v) for v in samples[:6])}`"
            )
            lines.append("")

        constraints = col.get("recommended_constraints", [])
        if constraints:
            lines.append(
                f"**Recommended constraints:** {', '.join(f'`{c}`' for c in constraints)}"
            )
            lines.append("")

        if col.get("quality_observations"):
            lines += [
                f"⚠️ **Quality note:** {col['quality_observations']}",
                "",
            ]

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
