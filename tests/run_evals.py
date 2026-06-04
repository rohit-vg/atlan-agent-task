import json
import os
import sys

# Add the parent directory to path so we can import the agent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from governance_agent import ProfileAgent


def run_evals():
    print("🚀 Running Agent Evaluations...")
    agent = ProfileAgent()

    # 1. Run agent on gold standard
    results = agent.run("tests/gold_standard.csv")

    # 2. Load the generated JSON catalog
    with open(results["json_path"], "r") as f:
        catalog = json.load(f)

    # 3. Evaluate results
    # Expected:
    # - 'email' column should have 1 invalid format, 1 duplicate
    # - 'phone' column should have 1 null

    errors = []
    email_col = next((c for c in catalog["columns"] if c["name"] == "email"), None)

    if not email_col:
        errors.append("Email column not found in catalog")
    else:
        # Check if the agent reported any issues for the email column
        issues = email_col.get("quality_observations", [])

        # Verify that the agent identified the issue
        has_issue = any("invalid" in str(i).lower() for i in issues)
        if not has_issue:
            errors.append(
                f"Failed to detect invalid email format. Issues reported: {issues}"
            )

    if errors:
        print("\n❌ Evaluation FAILED:")
        for e in errors:
            print(f" - {e}")
        sys.exit(1)
    else:
        print("\n✅ Evaluation PASSED!")


if __name__ == "__main__":
    run_evals()
