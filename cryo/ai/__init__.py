"""AI-assisted analysis package."""

from cryo.ai.anomaly import Anomaly, detect_anomalies
from cryo.ai.copilot import answer_question, answer_verified_question, list_verified_questions
from cryo.ai.embeddings import cosine_similarity, embed_text
from cryo.ai.explain import explain_health_change
from cryo.ai.pii import classify_column, classify_table_columns
from cryo.ai.similarity import find_similar_tables

__all__ = [
    "Anomaly",
    "answer_question",
    "answer_verified_question",
    "classify_column",
    "classify_table_columns",
    "cosine_similarity",
    "detect_anomalies",
    "embed_text",
    "explain_health_change",
    "find_similar_tables",
    "list_verified_questions",
]
