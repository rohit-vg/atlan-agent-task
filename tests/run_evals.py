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
    catalog_cols = {c["name"]: c for c in catalog["columns"]}
    email_col = catalog_cols.get("email")
    phone_col = catalog_cols.get("phone")

    # 1. "overall_quality_score" in catalog
    if "overall_quality_score" not in catalog:
        errors.append("overall_quality_score not found in catalog")

    if not email_col:
        errors.append("Email column not found in catalog")
    else:
        # 2. email_col["pii_risk"] in ("medium", "high")
        if email_col.get("pii_risk") not in ("medium", "high"):
            errors.append(
                f"Expected email pii_risk to be medium/high, got: {email_col.get('pii_risk')}"
            )

        # 3. any("EMAIL" in c.upper() for c in email_col.get("recommended_constraints", []))
        constraints = email_col.get("recommended_constraints", [])
        if not any("EMAIL" in c.upper() for c in constraints):
            errors.append(f"Email column missing EMAIL constraint. Got: {constraints}")

        # 4. email_col.get("quality_score", 100) < 80   (warning only)
        if email_col.get("quality_score", 100) >= 80:
            print("  ⚠️  WARN: Email quality_score >= 80")

    if not phone_col:
        errors.append("Phone column not found in catalog")
    else:
        # 5. phone_col.get("null_percentage", 0) > 0     (warning only)
        if phone_col.get("null_percentage", 0) == 0:
            print("  ⚠️  WARN: Phone null_percentage is 0")

    if errors:
        print("\n❌ Evaluation FAILED:")
        for e in errors:
            print(f" - {e}")
        sys.exit(1)
    else:
        print("\n✅ Evaluation PASSED!")


if __name__ == "__main__":
    run_evals()
