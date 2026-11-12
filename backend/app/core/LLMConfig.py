import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from dotenv import load_dotenv
from groq import Groq
from langsmith import traceable

# Ensure backend root is in sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load environment variables
load_dotenv(ROOT_DIR / ".env")

# User's exact available models list in priority order
AVAILABLE_MODELS: List[str] = [
    "openai/gpt-oss-20b",
    "meta-llama/llama-prompt-guard-2-86m",
    "meta-llama/llama-prompt-guard-2-22m",
]

# Module-level cache for client and active model
_CACHED_CLIENT: Optional[Groq] = None
_CACHED_MODEL: Optional[str] = None


def select_best_groq_model(client: Groq) -> str:
    """Tests the available models list with a ping and returns the first working one."""
    global _CACHED_MODEL
    print("[Groq] Selecting best available model from allowed list...")
    
    for candidate in AVAILABLE_MODELS:
        try:
            client.chat.completions.create(
                model=candidate,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1
            )
            print(f"[Groq] Successfully connected to active model: {candidate}")
            _CACHED_MODEL = candidate
            return candidate
        except Exception:
            # If 404 or unsupported, continue to next candidate
            continue

    # Default to primary chat model
    _CACHED_MODEL = AVAILABLE_MODELS[0]
    return _CACHED_MODEL


def get_groq_client(force_refresh: bool = False) -> Tuple[Groq, str]:
    """Initializes and caches the Groq client with the verified model."""
    global _CACHED_CLIENT, _CACHED_MODEL

    if not force_refresh and _CACHED_CLIENT is not None and _CACHED_MODEL is not None:
        return _CACHED_CLIENT, _CACHED_MODEL

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        raise ValueError(
            "GROQ_API_KEY is not set! Please add your Groq API key in c:\\AI\\backend\\.env"
        )

    client = Groq(api_key=groq_api_key)
    env_model = os.getenv("GROQ_MODEL", "auto").strip()

    if env_model.lower() not in ["auto", "", "default"] and env_model in AVAILABLE_MODELS:
        selected_model = env_model
    else:
        try:
            selected_model = select_best_groq_model(client)
        except Exception:
            selected_model = AVAILABLE_MODELS[0]

    _CACHED_CLIENT = client
    _CACHED_MODEL = selected_model
    return _CACHED_CLIENT, _CACHED_MODEL


def get_selected_model() -> str:
    """Returns currently selected model name."""
    _, model = get_groq_client()
    return model


@traceable(name="Groq LLM (JSON)", run_type="llm")
def call_llm_json(prompt: str, user_content: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Shared helper: calls Groq LLM with automatic fallback switching across available models."""
    global _CACHED_MODEL
    client, default_model = get_groq_client()

    models_to_try = [model or default_model]
    for m in AVAILABLE_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error = None
    for attempt_model in models_to_try:
        try:
            try:
                # Try JSON response format
                completion = client.chat.completions.create(
                    model=attempt_model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_content}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3
                )
                raw_text = completion.choices[0].message.content or "{}"
                _CACHED_MODEL = attempt_model
                return json.loads(raw_text)
            except Exception:
                # Fallback to standard completion and manual JSON extraction
                completion = client.chat.completions.create(
                    model=attempt_model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.3
                )
                content = completion.choices[0].message.content or "{}"
                start = content.find("{")
                end = content.rfind("}") + 1
                if start != -1 and end != 0:
                    _CACHED_MODEL = attempt_model
                    return json.loads(content[start:end])
                return {}
        except Exception as e:
            last_error = e
            print(f"[Model Fallback] '{attempt_model}' failed ({e}). Switching to next available model...")
            continue

    if last_error:
        raise last_error
    return {}


@traceable(name="Groq LLM (Text)", run_type="llm")
def call_llm_text(prompt: str, user_content: str, model: Optional[str] = None, temperature: float = 0.5) -> str:
    """Shared helper: calls Groq LLM with automatic fallback switching across available models."""
    global _CACHED_MODEL
    client, default_model = get_groq_client()

    models_to_try = [model or default_model]
    for m in AVAILABLE_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error = None
    for attempt_model in models_to_try:
        try:
            completion = client.chat.completions.create(
                model=attempt_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=temperature
            )
            _CACHED_MODEL = attempt_model
            return completion.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            print(f"[Model Fallback] '{attempt_model}' failed ({e}). Switching to next available model...")
            continue

    if last_error:
        raise last_error
    return ""
