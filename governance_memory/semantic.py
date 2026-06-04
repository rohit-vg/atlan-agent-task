import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import chromadb
from google import genai

from .embeddings import GeminiEmbeddingFunction


class SemanticMemory:
    """Stores and retrieves abstract concepts, rules, standards, and guidelines in ChromaDB."""

    def __init__(
        self,
        chroma_client: Optional[chromadb.PersistentClient] = None,
        client: Optional[genai.Client] = None,
        db_path: str = "governance_memory/chroma_db",
    ):
        self.chroma_client = chroma_client or chromadb.PersistentClient(path=db_path)
        self.embedding_fn = GeminiEmbeddingFunction(client=client)
        self.collection = self.chroma_client.get_or_create_collection(
            name="semantic_memory",
            embedding_function=self.embedding_fn,
        )
        self._seed_default_rules()

    def _seed_default_rules(self):
        """Seed default governance guidelines if the collection is empty."""
        count = self.collection.count()
        if count > 0:
            return

        defaults = [
            (
                "When a column name contains 'email' or the data looks like email addresses, "
                "classify its semantic_type as 'EMAIL_ADDRESS' and set pii_risk to 'medium'. "
                "Verify standard formatting via validating functions and record typos in quality observations.",
                "semantic_type_guideline",
                ["email", "pii"],
            ),
            (
                "When a column contains names, phone numbers, or physical addresses, "
                "classify its pii_risk as 'medium' or 'high' and ensure nullable/uniqueness percentages "
                "are computed. Do not expose raw phone numbers in the description; keep descriptions abstract.",
                "pii_risk_guideline",
                ["phone", "name", "pii", "address"],
            ),
            (
                "When column names are 'id', 'uuid', 'key', or sequential integer primary keys, "
                "classify the semantic_type as 'unique_identifier' or 'primary_key'. "
                "Typically, primary keys should not be nullable and uniqueness percentage should be 100%.",
                "semantic_type_guideline",
                ["id", "key", "uniqueness"],
            ),
            (
                "When validating duplicate emails or phone numbers, distinct entries with identical text "
                "should be flagged as duplicate issues in the quality_observations. "
                "Null/empty fields are not considered duplicate violations unless they are primary keys.",
                "validation_guideline",
                ["duplicates", "validation"],
            ),
            (
                "If a column contains numeric codes that are non-measurable (such as zip codes, phone extensions, "
                "or employee numbers), classify its data_type as text/string and set semantic_type to "
                "specific code/identifier types rather than mathematical metrics.",
                "data_type_guideline",
                ["numeric", "codes", "semantic_type"],
            ),
        ]

        for text, category, tags in defaults:
            self.add_rule(
                text=text, category=category, tags=tags, source="system_default"
            )

    def add_rule(
        self,
        text: str,
        category: str,
        tags: Optional[List[str]] = None,
        source: str = "user_feedback",
    ) -> str:
        """Add a custom rule, guideline, or user feedback to Semantic Memory."""
        rule_id = f"rule_{uuid.uuid4()}"
        metadata = {
            "category": category,
            "source": source,
            "created_at": datetime.now().isoformat(),
            "tags": ",".join(tags) if tags else "",
        }
        self.collection.add(
            documents=[text],
            metadatas=[metadata],
            ids=[rule_id],
        )
        return rule_id

    def query_rules(self, query_text: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Retrieve relevant rules matching a semantic query (e.g., column descriptions or validation errors)."""
        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[query_text],
            n_results=limit,
        )

        rules = []
        if not results or "documents" not in results or not results["documents"][0]:
            return rules

        for i in range(len(results["documents"][0])):
            rules.append(
                {
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i]
                    if "distances" in results and results["distances"]
                    else None,
                }
            )
        return rules

    def list_all_rules(self) -> List[Dict[str, Any]]:
        """Retrieve all stored semantic guidelines and rules."""
        results = self.collection.get()
        rules = []
        if not results or "documents" not in results or not results["documents"]:
            return rules

        for i in range(len(results["documents"])):
            rules.append(
                {
                    "id": results["ids"][i],
                    "text": results["documents"][i],
                    "metadata": results["metadatas"][i],
                }
            )
        return rules

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a specific rule from the semantic store."""
        try:
            self.collection.delete(ids=[rule_id])
            return True
        except Exception:
            return False
