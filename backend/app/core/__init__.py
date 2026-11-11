"""Core application package containing shared LLM and Database configurations."""

from app.core.LLMConfig import (
    get_groq_client,
    get_selected_model,
    call_llm_json,
    call_llm_text,
    select_best_groq_model,
    AVAILABLE_MODELS,
)
from app.core.database import get_db_connection, init_db

__all__ = [
    "get_groq_client",
    "get_selected_model",
    "call_llm_json",
    "call_llm_text",
    "select_best_groq_model",
    "AVAILABLE_MODELS",
    "get_db_connection",
    "init_db",
]
