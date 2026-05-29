"""
main.py — Entry point for the CSV Catalog Agent.

Usage:
    python main.py                        # uses sample_data.csv in current dir
    python main.py path/to/your_file.csv  # any CSV you like
"""

import sys
import os
from dotenv import load_dotenv
from agent import run_catalog_agent

def main():
    load_dotenv()

    # Check API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌  ANTHROPIC_API_KEY not found.")
        print("    Create a .env file with:  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    # Resolve filepath
    if len(sys.argv) >= 2:
        filepath = sys.argv[1]
    else:
        filepath = "sample_data.csv"

    if not os.path.exists(filepath):
        print(f"❌  File not found: {filepath}")
        print(f"    Usage: python main.py <path_to_csv>")
        sys.exit(1)

    if not filepath.lower().endswith(".csv"):
        print("❌  Please provide a .csv file.")
        sys.exit(1)

    # Run the agent
    paths = run_catalog_agent(filepath)

    if paths:
        print(f"  📄  JSON  → {paths.get('json_path')}")
        print(f"  📝  Markdown → {paths.get('md_path')}")

if __name__ == "__main__":
    main()
