import os
import sys
from dotenv import load_dotenv
from data_profiling_skill import ProfileAgent

# Load environment variables from .env file
load_dotenv()

class DataGovernanceAgent:
    """Agent that uses the data profiling skill for comprehensive CSV analysis."""

    def __init__(self):
        self.profiler = ProfileAgent()

    def run_full_workflow(self, filepath: str):
        """
        Profile a CSV file and generate a complete metadata catalog.
        
        Returns:
            dict with paths to generated JSON and Markdown outputs
        """
        return self.profiler.run(filepath)


if __name__ == "__main__":
    # Verify API key is loaded
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not found.")
        print("   Create a .env file with: ANTHROPIC_API_KEY=sk-ant-...")
        exit(1)

    # Resolve filepath
    if len(sys.argv) >= 2:
        filepath = sys.argv[1]
    else:
        filepath = "input/sample_data.csv"

    agent = DataGovernanceAgent()
    result = agent.run_full_workflow(filepath=filepath)
    
    print("\n" + "="*70)
    print("✨ Profiling complete!")
    print(f"📄 JSON Catalog: {result['json_path']}")
    print(f"📝 Markdown Report: {result['md_path']}")
    print("="*70)
