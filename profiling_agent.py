import os
from dotenv import load_dotenv
import anthropic
from data_profiling_skill import ProfileAgent

# Load environment variables from .env file
load_dotenv()

class MyDataGovernanceAgent:
    """Custom agent that uses the profiling skill for comprehensive analysis."""

    def __init__(self):
        self.client = anthropic.Anthropic()
        self.profiler = ProfileAgent()

    def run_full_workflow(self, filepath: str):
        """
        Complete workflow:
        1. Use ProfileAgent skill to profile ALL columns
        2. Summarize findings
        3. Identify quality issues
        """

        print(f"\n{'='*70}")
        print(f"  📊  Data Governance Agent with Profiling Skill")
        print(f"  📄  File: {filepath}")
        print(f"{'='*70}\n")

        # Step 1: Use the profiling skill to profile ALL columns
        # The ProfileAgent handles:
        # - Phase 1: Profile every column via ReAct tool loop
        # - Phase 2: Synthesize metadata catalog
        # - Saves JSON + Markdown outputs
        print("Step 1️⃣  Running comprehensive profiling skill...\n")
        output_paths = self.profiler.run(filepath)

        # Step 2: Quick summary
        print("\nStep 2️⃣  Analysis complete!\n")
        print("📋 Summary:")
        print(f"  ✅ JSON Catalog: {output_paths['json_path']}")
        print(f"  ✅ Markdown Report: {output_paths['md_path']}")
        
        return output_paths


# Usage
if __name__ == "__main__":
    # Verify API key is loaded
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not found.")
        print("   Create a .env file with: ANTHROPIC_API_KEY=sk-ant-...")
        exit(1)
    
    agent = MyDataGovernanceAgent()
    
    # Profile ALL columns in the dataset
    result = agent.run_full_workflow(filepath="input/hr_test.csv")
    
    print("\n" + "="*70)
    print("✨ Profiling complete! Check the output files for full details.")
    print("="*70)
