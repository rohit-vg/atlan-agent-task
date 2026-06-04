USE_LOCAL = True

import anthropic

if USE_LOCAL:
    client = anthropic.Anthropic(
        base_url="http://localhost:11434",
        api_key="ollama",
    )
    MODEL = "qwen3.5:9b"
else:
    MODEL = "claude-haiku-4-5"
