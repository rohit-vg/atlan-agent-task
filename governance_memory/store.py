import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


class SQLiteEpisodeStore:
    """Relational SQLite store for reliable episodic memory reconstruction."""

    def __init__(self, db_path: str = "governance_memory/memory.db"):
        self.db_path = db_path
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # IMPROVEMENT: WAL for concurrency
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """Initialize the database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Table for high-level episode information
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    filepath TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,          -- 'started', 'completed', 'failed'
                    duration REAL,
                    catalog_json TEXT,             -- Final synthesized metadata catalog JSON
                    feedback_rating INTEGER,       -- User rating (e.g., 1-5)
                    feedback_comments TEXT,        -- Optional comments
                    summary TEXT                   -- High-level textual summary of the run
                )
            """)

            # Table for detailed sequential steps (tool execution trace)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    inputs TEXT,                   -- JSON string of tool arguments
                    output TEXT,                   -- JSON string of tool response
                    FOREIGN KEY(episode_id) REFERENCES episodes(id) ON DELETE CASCADE
                )
            """)

            # IMPROVEMENT: Add indexes for performance
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_steps_episode ON steps(episode_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status)"
            )
            conn.commit()

    def create_episode(self, episode_id: str, filepath: str, summary: str = "") -> None:
        """Create a new episode in the started state."""
        timestamp = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO episodes
                (id, filepath, timestamp, status, duration, catalog_json, feedback_rating, feedback_comments, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    filepath,
                    timestamp,
                    "started",
                    None,
                    None,
                    None,
                    None,
                    summary,
                ),
            )
            conn.commit()

    def add_step(
        self,
        episode_id: str,
        step_index: int,
        tool_name: str,
        inputs: Dict[str, Any],
        output: Any,
    ) -> None:
        """Log a tool execution step under an episode."""
        inputs_json = json.dumps(inputs, default=str)
        output_json = json.dumps(output, default=str)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO steps (episode_id, step_index, tool_name, inputs, output)
                VALUES (?, ?, ?, ?, ?)
                """,
                (episode_id, step_index, tool_name, inputs_json, output_json),
            )
            conn.commit()

    def complete_episode(
        self,
        episode_id: str,
        status: str,
        duration: float,
        catalog_json: Optional[Dict[str, Any]] = None,
        summary: str = "",
    ) -> None:
        """Complete an episode, saving the final synthesized catalog."""
        catalog_str = json.dumps(catalog_json, default=str) if catalog_json else None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Retrieve current summary if none is provided to avoid overwrite
            if not summary:
                cursor.execute(
                    "SELECT summary FROM episodes WHERE id = ?", (episode_id,)
                )
                row = cursor.fetchone()
                summary = row["summary"] if row else ""

            cursor.execute(
                """
                UPDATE episodes
                SET status = ?, duration = ?, catalog_json = ?, summary = ?
                WHERE id = ?
                """,
                (status, duration, catalog_str, summary, episode_id),
            )
            conn.commit()

    def add_feedback(self, episode_id: str, rating: int, comments: str) -> None:
        """Attach user feedback to a past episode."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE episodes
                SET feedback_rating = ?, feedback_comments = ?
                WHERE id = ?
                """,
                (rating, comments, episode_id),
            )
            conn.commit()

    def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full details of an episode and its sequential steps."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
            episode_row = cursor.fetchone()
            if not episode_row:
                return None

            episode = dict(episode_row)
            if episode["catalog_json"]:
                episode["catalog_json"] = json.loads(episode["catalog_json"])

            cursor.execute(
                "SELECT * FROM steps WHERE episode_id = ? ORDER BY step_index ASC",
                (episode_id,),
            )
            steps = []
            for step_row in cursor.fetchall():
                step = dict(step_row)
                step["inputs"] = json.loads(step["inputs"]) if step["inputs"] else {}
                step["output"] = json.loads(step["output"]) if step["output"] else {}
                steps.append(step)

            episode["steps"] = steps
            return episode

    def list_episodes(self) -> List[Dict[str, Any]]:
        """List all stored episodes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM episodes ORDER BY timestamp DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_feedback_stats(self) -> Optional[Dict]:
        """Aggregate feedback for feedback-loop nudge in synthesis."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS count,
                       AVG(feedback_rating) AS avg_rating,
                       MIN(feedback_rating) AS min_rating,
                       MAX(feedback_rating) AS max_rating
                FROM episodes
                WHERE feedback_rating IS NOT NULL
            """).fetchone()
            if row and row["count"] > 0:
                return {
                    "count": row["count"],
                    "avg_rating": round(row["avg_rating"], 2),
                    "min_rating": row["min_rating"],
                    "max_rating": row["max_rating"],
                }
        return None

    def get_recent_episodes(self, limit: int = 10) -> List[Dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, filepath, timestamp, status, duration, feedback_rating, summary
                FROM episodes WHERE status='completed'
                ORDER BY timestamp DESC LIMIT ?
            """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
