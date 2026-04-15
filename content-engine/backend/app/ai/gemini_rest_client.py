"""
app/ai/gemini_rest_client.py — Direct HTTP REST client for Vertex AI Gemini API.

This module provides an HTTP-based client for calling Vertex AI Gemini models
via the REST API endpoint:
https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{REGION}/publishers/google/models/{MODEL_ID}:streamGenerateContent

Advantages over Python SDK:
- Direct control over HTTP requests
- Easier debugging and logging
- No SDK bloat
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import structlog
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config import settings

log = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────
_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

_GENERATION_CONFIG = {
    "temperature": 0.7,
    "topP": 0.95,
    "topK": 40,
    "maxOutputTokens": 8192,
    "responseMimeType": "application/json",
}


# ── Custom exceptions ──────────────────────────────────────────────────────────


class GeminiRestError(Exception):
    """Base exception for REST API errors."""


class GeminiApiError(GeminiRestError):
    """Raised when the API returns an error response."""


class GeminiEmptyResponseError(GeminiRestError):
    """Raised when the model returns an empty or blocked response."""


class GeminiResponseParseError(GeminiRestError):
    """Raised when the response cannot be parsed."""


# ── Authentication ─────────────────────────────────────────────────────────────


class _VertexAIAuth:
    """Manages OAuth2 authentication for Vertex AI REST API."""

    _credentials: Any = None
    _token: str | None = None

    @classmethod
    def get_access_token(cls) -> str:
        """Get or refresh access token from service account."""
        if cls._credentials is None:
            cls._credentials = service_account.Credentials.from_service_account_file(
                settings.google_application_credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )

        if cls._credentials.expired or not cls._credentials.token:
            cls._credentials.refresh(Request())

        return cls._credentials.token


# ── REST API Client ────────────────────────────────────────────────────────────


def _build_api_url(model_id: str) -> str:
    """Build the Vertex AI REST API URL for the given model."""
    region = settings.gcp_region
    project_id = settings.gcp_project_id

    # Remove 'models/' prefix if present
    if model_id.startswith("models/"):
        model_id = model_id[7:]

    return (
        f"https://aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/{region}/"
        f"publishers/google/models/{model_id}:streamGenerateContent"
    )


def _build_request_payload(
    system_instruction: str | None,
    user_message: str,
    generation_config: dict | None = None,
) -> dict:
    """Build the JSON payload for the Gemini API request."""
    contents: list[dict] = []

    # System instruction as first content if provided
    if system_instruction:
        contents.append(
            {
                "role": "user",
                "parts": [{"text": system_instruction}],
            }
        )
        contents.append(
            {
                "role": "model",
                "parts": [{"text": "Understood. I will follow these instructions."}],
            }
        )

    # User message
    contents.append(
        {
            "role": "user",
            "parts": [{"text": user_message}],
        }
    )

    config = _GENERATION_CONFIG.copy()
    if generation_config:
        config.update(generation_config)

    return {
        "contents": contents,
        "generationConfig": config,
        "safetySettings": _SAFETY_SETTINGS,
    }


def _parse_streaming_response(response_text: str) -> str:
    """
    Parse the streaming JSON array response from Gemini.
    Returns the concatenated text from all chunks.
    """
    full_text = []

    try:
        # Vertex AI streamGenerateContent returns a JSON array of objects
        chunks = json.loads(response_text)
    except json.JSONDecodeError as exc:
        log.error("gemini_rest.json_decode_error", error=str(exc))
        return ""

    if isinstance(chunks, dict):
        chunks = [chunks]

    if not isinstance(chunks, list):
        return ""

    for chunk in chunks:
        # Extract text from candidates
        candidates = chunk.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if "text" in part:
                    full_text.append(part["text"])

            # Check for finish reason
            finish_reason = candidate.get("finishReason")
            if finish_reason and finish_reason not in ("STOP", "MAX_TOKENS"):
                log.warning("gemini_rest.finish_reason", reason=finish_reason)

    return "".join(full_text)


def generate_content_rest(
    system_prompt: str | None,
    user_message: str,
    model: str | None = None,
    generation_config: dict | None = None,
    logger: Any | None = None,
) -> str:
    """
    Call Gemini via REST API and return the generated text.

    Args:
        system_prompt: Optional system instructions
        user_message: The user message/prompt
        model: Model ID (defaults to settings.vertex_ai_model)
        generation_config: Optional generation parameters override
        logger: Optional logger instance

    Returns:
        Generated text string

    Raises:
        GeminiApiError: If API returns an error
        GeminiEmptyResponseError: If response is empty or blocked
    """
    logger = logger or log
    model_id = model or settings.vertex_ai_model

    logger.info("gemini_rest.request.start", model=model_id)

    # Build request
    url = _build_api_url(model_id)
    payload = _build_request_payload(system_prompt, user_message, generation_config)
    token = _VertexAIAuth.get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Make request
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            # Parse JSON array streaming response
            full_text = _parse_streaming_response(response.text)

    except httpx.HTTPStatusError as exc:
        error_text = exc.response.text if hasattr(exc, "response") else str(exc)
        logger.error(
            "gemini_rest.api_error",
            status=exc.response.status_code,
            error=error_text[:500],
        )
        raise GeminiApiError(
            f"API error {exc.response.status_code}: {error_text[:500]}"
        ) from exc
    except Exception as exc:
        logger.error("gemini_rest.request_error", error=str(exc))
        raise GeminiApiError(f"Request failed: {exc}") from exc

    if not full_text.strip():
        logger.error("gemini_rest.empty_response")
        raise GeminiEmptyResponseError("Gemini returned empty response")

    logger.info("gemini_rest.request.success", chars=len(full_text))
    return full_text


def generate_content_rest_json(
    system_prompt: str,
    user_message: str,
    model: str | None = None,
    temperature: float = 0.6,
    logger: Any | None = None,
) -> dict:
    """
    Call Gemini via REST API and parse the response as JSON.

    Args:
        system_prompt: System instructions
        user_message: The user message/prompt
        model: Model ID (defaults to settings.vertex_ai_model)
        temperature: Generation temperature
        logger: Optional logger instance

    Returns:
        Parsed JSON dict

    Raises:
        GeminiResponseParseError: If response cannot be parsed as JSON
    """
    logger = logger or log

    gen_config = {
        "temperature": temperature,
        "responseMimeType": "application/json",
    }

    try:
        response_text = generate_content_rest(
            system_prompt=system_prompt,
            user_message=user_message,
            model=model,
            generation_config=gen_config,
            logger=logger,
        )
    except Exception as exc:
        raise GeminiApiError(f"Generation failed: {exc}") from exc

    # Parse JSON response
    cleaned = re.sub(r"^```(?:json)?\s*", "", response_text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # LLMs often hallucinate unescaped newlines and sometimes abruptly truncate outputs.
        # We manually walk the string to escape newlines safely and close hanging structures.
        in_string = False
        escape = False
        fixed_chars = []
        
        for char in cleaned:
            if escape:
                fixed_chars.append(char)
                escape = False
                continue
            if char == '\\':
                escape = True
                fixed_chars.append(char)
                continue
            if char == '"':
                in_string = not in_string
                fixed_chars.append(char)
                continue
            if in_string and char == '\n':
                fixed_chars.extend(['\\', 'n'])
                continue
            if in_string and char == '\r':
                continue # drop carriage returns in strings
                
            fixed_chars.append(char)
            
        if in_string:
            fixed_chars.append('"')
            
        repaired = "".join(fixed_chars)
        
        parsed = None
        for suffix in ("", "}", "]}", "]", '"}'):
            try:
                parsed = json.loads(repaired + suffix)
                break
            except json.JSONDecodeError:
                continue
                
        if parsed is None:
            msg = f"Failed to parse JSON response: {exc}. Raw: {response_text[:500]!r}"
            logger.error("gemini_rest.json_parse_error", error=str(exc))
            raise GeminiResponseParseError(msg) from exc
            
        logger.warning("gemini_rest.json_repaired_successfully")

    return parsed


# ═════════════════════════════════════════════════════════════════════════════
# Compatibility layer for existing gemini_writer.py
# ═════════════════════════════════════════════════════════════════════════════


def generate_content_with_rest(
    system_instruction: str | None,
    contents: list,
    generation_config: Any | None = None,
    safety_settings: Any | None = None,
) -> Any:
    """
    Compatibility wrapper that mimics the Vertex AI SDK's generate_content.

    Usage: Replace model.generate_content() calls with this function.
    """
    # Extract text from contents
    user_message = ""
    for content in contents:
        if hasattr(content, "text"):
            user_message = content.text
        elif isinstance(content, dict) and "text" in content:
            user_message = content["text"]
        elif isinstance(content, str):
            user_message = content

    response_text = generate_content_rest(
        system_prompt=system_instruction,
        user_message=user_message,
        generation_config=generation_config
        if isinstance(generation_config, dict)
        else None,
    )

    # Return a mock response object with .text property
    class _MockResponse:
        def __init__(self, text: str):
            self._text = text
            self.candidates = [{"finish_reason": "STOP"}]

        @property
        def text(self) -> str:
            return self._text

    return _MockResponse(response_text)
