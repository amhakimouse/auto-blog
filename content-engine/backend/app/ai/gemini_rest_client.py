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


def _build_api_url(model_id: str, cached_content: str | None = None) -> str:
    """Build the Vertex AI REST API URL for the given model."""
    project_id = settings.gcp_project_id
    
    # If cached_content is provided, it's a full resource name
    # e.g. projects/{project_id}/locations/{region}/cachedContents/{id}
    
    # Remove 'models/' prefix if present
    if model_id.startswith("models/"):
        model_id = model_id[7:]
    
    if not model_id:
        model_id = "gemini-2.5-flash"

    return (
        f"https://aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/global/"
        f"publishers/google/models/{model_id}:generateContent"
    )


def _build_request_payload(
    system_instruction: str | None,
    user_message: str,
    generation_config: dict | None = None,
    cached_content: str | None = None,
) -> dict:
    """Build the JSON payload for the Gemini API request."""
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_message}],
            }
        ],
        "generationConfig": {
            "candidateCount": 1,
            "responseModalities": ["TEXT"],
        }
    }

    if system_instruction and not cached_content:
        # If no cache, we inject system instructions into the prompt or content
        # Better approach for Vertex AI: add systemInstruction object
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }
    
    if cached_content:
        # Use existing context cache
        payload["cachedContent"] = cached_content

    # Merge additional config if provided
    if generation_config:
        for k, v in generation_config.items():
            api_key = k
            if k == "response_schema":
                api_key = "responseSchema"
                payload["generationConfig"]["responseMimeType"] = "application/json"
            
            if api_key not in payload["generationConfig"]:
                payload["generationConfig"][api_key] = v
            elif api_key == "responseMimeType":
                payload["generationConfig"][api_key] = v

    return payload


# ── Context Caching Management ────────────────────────────────────────────────


class ContextCacheManager:
    """Manages Vertex AI Context Caching via REST API."""

    @classmethod
    def get_cache_url(cls) -> str:
        return (
            f"https://aiplatform.googleapis.com/v1/"
            f"projects/{settings.gcp_project_id}/locations/global/cachedContents"
        )

    @classmethod
    def create_cache(
        cls, 
        system_instruction: str, 
        contents: list[dict], 
        model: str = "gemini-2.5-flash",
        ttl_seconds: int = 3600
    ) -> str:
        """
        Create a new cached content resource.
        Returns the resource name (id).
        """
        log.info("gemini_cache.create.start", model=model, ttl=ttl_seconds)
        
        # Vertex AI REST expects 'models/...' prefix for the model field in cachedContent
        if not model.startswith("models/"):
            model = f"publishers/google/models/{model}"

        payload = {
            "model": model,
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": contents,
            "ttl": f"{ttl_seconds}s"
        }

        token = _VertexAIAuth.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(cls.get_cache_url(), json=payload, headers=headers)
                if response.status_code != 200:
                    log.error("gemini_cache.create.failed", status=response.status_code, body=response.text)
                    response.raise_for_status()
                
                data = response.json()
                cache_name = data["name"]
                log.info("gemini_cache.create.success", name=cache_name)
                return cache_name
        except Exception as exc:
            log.error("gemini_cache.create.error", error=str(exc))
            raise GeminiApiError(f"Failed to create context cache: {exc}") from exc


def _parse_response(raw_text: str) -> str:
    """
    Parse the response text from Gemini.
    Handles both unary responses and NDJSON streaming chunks.
    """
    if not raw_text:
        return ""

    full_text_parts = []
    
    # 1. Try parsing as a single JSON object (unary)
    try:
        data = json.loads(raw_text)
        # If it's a list, it might be NDJSON already parsed as a list by some middleware
        # or it's a streaming response body formatted as a list
        if isinstance(data, list):
            for chunk in data:
                for candidate in chunk.get("candidates", []):
                    for part in candidate.get("content", {}).get("parts", []):
                        if "text" in part:
                            full_text_parts.append(part["text"])
        else:
            # Single object
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        full_text_parts.append(part["text"])
        
        if full_text_parts:
            return "".join(full_text_parts)
    except json.JSONDecodeError:
        # Not a single JSON object, try NDJSON line-by-line
        pass

    # 2. Parse as NDJSON (one JSON object per line)
    # Vertex AI streamGenerateContent returns a JSON array-like structure but 
    # the REST response can be a JSON array of objects or just concatenated objects.
    
    # Clean up common wrapper characters if present [ ... ]
    cleaned = raw_text.strip()
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1].strip()
    
    # Split by JSON boundaries
    # Heuristic: split before each {"candidates"
    chunks = re.split(r',?\s*(?={"candidates")', cleaned)
    
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            data = json.loads(chunk)
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        full_text_parts.append(part["text"])
        except json.JSONDecodeError:
            continue

    return "".join(full_text_parts)


def generate_content_rest(
    system_prompt: str | None,
    user_message: str,
    model: str | None = None,
    generation_config: dict | None = None,
    cached_content: str | None = None,
    logger: Any | None = None,
) -> str:
    """
    Call Gemini via REST API and return the generated text.
    Supports optional cached_content ID.
    """
    logger = logger or log
    model_id = model or settings.vertex_ai_model

    logger.info("gemini_rest.request.start", model=model_id, cached=bool(cached_content))

    # Build request
    url = _build_api_url(model_id)
    payload = _build_request_payload(system_prompt, user_message, generation_config, cached_content)
    token = _VertexAIAuth.get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Make request
    try:
        with httpx.Client(timeout=180.0) as client:
            response = client.post(url, json=payload, headers=headers)
            
            if response.status_code != 200:
                logger.error("gemini_rest.api_error", status=response.status_code, body=response.text[:1000])
                response.raise_for_status()

            # Parse response (unary or streaming)
            full_text = _parse_response(response.text)

    except httpx.HTTPStatusError as exc:
        error_text = exc.response.text if hasattr(exc, "response") else str(exc)
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
    response_schema: dict | None = None,
    logger: Any | None = None,
) -> dict:
    """
    Call Gemini via REST API and parse the response as JSON.
    """
    logger = logger or log

    gen_config = {
        "temperature": temperature,
        "responseMimeType": "application/json",
    }
    if response_schema:
        gen_config["response_schema"] = response_schema

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
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("gemini_rest.json_parse_error.attempting_repair", error=str(exc))
        
        # ── Advanced JSON Repair ─────────────────────────────────────────────
        # 1. Basic cleaning
        repaired = cleaned.strip()
        
        # 2. Handle truncation (if it doesn't end with })
        if not repaired.endswith('}'):
            # Find the last valid key-value completion if possible, or just close it
            last_quote = repaired.rfind('"')
            if last_quote != -1:
                # If we're inside a string, close it
                # Check if the quote is escaped
                if last_quote > 0 and repaired[last_quote-1] != '\\':
                    # Might be the end of a string or start of one. 
                    # Heuristic: if there's a colon before it, it's likely a value.
                    pass
            
            # Simple approach: append closing structure based on counts
            open_braces = repaired.count('{') - repaired.count('}')
            if open_braces > 0:
                # If it ends mid-string
                if repaired.count('"') % 2 != 0:
                    repaired += '"'
                repaired += '}' * open_braces

        # 3. Robust quote escaping for large HTML blobs
        # This regex looks for "key": "value" pairs and escapes internal quotes in value
        def _escape_internal_quotes(json_str: str) -> str:
            # Find all top-level keys
            parts = []
            last_pos = 0
            # Matches "key": "
            for m in re.finditer(r'"(\w+)":\s*"', json_str):
                # Append everything from last_pos to start of this match
                parts.append(json_str[last_pos:m.start()])
                
                # We found a key and the start of a value
                key_start = m.start()
                val_start = m.end()
                
                # Now we need to find the end of this value. 
                # The end of a value is a quote followed by , or }
                # But there might be escaped quotes \" inside.
                # We search for the next " that is NOT preceded by \ AND is followed by \s*[,}]
                # OR the end of the string.
                end_m = re.search(r'(?<!\\)"\s*(?=[,}])', json_str[val_start:])
                if end_m:
                    val_end_relative = end_m.start()
                    val_end = val_start + val_end_relative
                    content = json_str[val_start:val_end]
                    # Escape any unescaped quotes in content
                    fixed_content = re.sub(r'(?<!\\)"', '\\"', content)
                    # Replace unescaped newlines with \n
                    fixed_content = fixed_content.replace('\n', '\\n').replace('\r', '')
                    
                    parts.append(f'"{m.group(1)}": "{fixed_content}"')
                    last_pos = val_end + 1 # +1 to skip the closing quote
                else:
                    # No end found, take the rest of the string
                    content = json_str[val_start:]
                    fixed_content = re.sub(r'(?<!\\)"', '\\"', content)
                    fixed_content = fixed_content.replace('\n', '\\n').replace('\r', '')
                    parts.append(f'"{m.group(1)}": "{fixed_content}"')
                    last_pos = len(json_str)
                    break
            
            if last_pos < len(json_str):
                parts.append(json_str[last_pos:])
            
            return "".join(parts)

        try:
            repaired = _escape_internal_quotes(repaired)
            parsed = json.loads(repaired)
            logger.info("gemini_rest.json_repaired_successfully")
            return parsed
        except Exception as repair_exc:
            logger.warning("gemini_rest.json_repair_failed", error=str(repair_exc))

        # 4. Last ditch: Truncate at last valid }
        last_brace = cleaned.rfind('}')
        if last_brace != -1:
            try:
                return json.loads(cleaned[:last_brace+1])
            except json.JSONDecodeError:
                pass

        msg = f"Failed to parse JSON response: {exc}. Raw: {response_text[:500]!r}"
        raise GeminiResponseParseError(msg) from exc


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
