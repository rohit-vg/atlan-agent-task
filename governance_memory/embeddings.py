import os
from typing import List

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from google import genai


class GeminiEmbeddingFunction(EmbeddingFunction):
    """Custom embedding function using Google's GenAI text-embedding-004 model."""

    def __init__(self, client: genai.Client = None):
        if client is None:
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY environment variable not set.")
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = client
        self.model = "gemini-embedding-2"

    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []

        embeddings: List[List[float]] = []
        try:
            # Attempt to embed as a batch
            res = self.client.models.embed_content(
                model=self.model, contents=list(input)
            )
            if hasattr(res, "embeddings") and res.embeddings:
                for e in res.embeddings:
                    embeddings.append([float(val) for val in e.values])
            elif hasattr(res, "embedding") and res.embedding:
                # Single result fallback
                embeddings.append([float(val) for val in res.embedding.values])
            else:
                raise ValueError("No embeddings returned by API.")
        except Exception as e:
            # Fallback to sequential embeddings if batching fails or returns unexpected format
            embeddings = []
            for text in input:
                res = self.client.models.embed_content(model=self.model, contents=text)
                embeddings.append([float(val) for val in res.embeddings[0].values])

        return embeddings
