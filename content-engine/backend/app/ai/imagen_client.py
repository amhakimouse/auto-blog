"""
app/ai/imagen_client.py — Imagen image generation via Vertex AI REST API.

Workflow 1 (Keyword → Article) image enrichment step.
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Final

import httpx
import structlog
from app.config import settings
from app.ai.gemini_rest_client import _VertexAIAuth

log = structlog.get_logger(__name__)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class ImagenError(Exception):
    """Base exception for Imagen failures."""

class ImagenSafetyError(ImagenError):
    """Raised when Imagen rejects a prompt via its safety filters."""

class ImagenQuotaError(ImagenError):
    """Raised when GCP quota or rate limits are hit."""

# ── Constants ──────────────────────────────────────────────────────────────────

_DEFAULT_ASPECT_RATIO: Final[str] = "4:3"
_PROMPT_MAX_CHARS: Final[int] = 480

# ── REST API Client ────────────────────────────────────────────────────────────

def _build_imagen_url() -> str:
    """
    Build the Imagen REST API URL using the structure requested by the user.
    URL: https://aiplatform.googleapis.com/v1/projects/{{project_id}}/locations/global/publishers/google/models/imagen-3.0-generate-002:predict
    """
    project_id = settings.gcp_project_id
    # Using 'global' as requested by the user
    return (
        f"https://aiplatform.googleapis.com/v1/"
        f"projects/{project_id}/locations/global/"
        f"publishers/google/models/imagen-3.0-generate-002:predict"
    )

def _build_imagen_payload(prompt: str, aspect_ratio: str) -> dict:
    """
    Build the JSON payload for Imagen REST API.
    Note: The :predict endpoint requires 'instances' and 'parameters'.
    """
    return {
        "instances": [
            {
                "prompt": prompt
            }
        ],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": aspect_ratio,
            "outputMimeType": "image/png"
        }
    }

def generate_section_image(
    prompt: str,
    *,
    aspect_ratio: str = _DEFAULT_ASPECT_RATIO,
    retry_delay_s: float = 2.0,
) -> dict:
    """
    Generate a single image for an article section using Imagen REST API.
    """
    clean_prompt = _sanitise_prompt(prompt)
    logger = log.bind(
        model="imagen-3.0-generate-002",
        prompt_chars=len(clean_prompt),
        aspect_ratio=aspect_ratio,
    )
    logger.info("imagen.generate.start")

    if retry_delay_s > 0:
        time.sleep(retry_delay_s)

    url = _build_imagen_url()
    payload = _build_imagen_payload(clean_prompt, aspect_ratio)
    
    try:
        token = _VertexAIAuth.get_access_token()
    except Exception as exc:
        logger.error("imagen.auth_error", error=str(exc))
        raise ImagenError(f"Auth failed: {exc}")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=payload, headers=headers)
            
            if response.status_code != 200:
                logger.error("imagen.api_error", status=response.status_code, body=response.text[:1000])
                _handle_api_error(response, logger)
            
            data = response.json()
            
            # Parse Vertex AI Prediction response for Imagen
            predictions = data.get("predictions", [])
            if not predictions:
                # Check for safety filter information in the response
                if "errors" in data or "error" in data:
                    _handle_api_error(response, logger)
                raise ImagenSafetyError("Imagen returned no predictions (possible safety block)")
            
            # Imagen 3.0 returns base64 in 'bytesBase64Encoded'
            image_data = predictions[0].get("bytesBase64Encoded")

            if not image_data:
                logger.warning("imagen.no_image_in_response", response=data)
                raise ImagenError("No image data found in response predictions")

            alt_text = clean_prompt[:120].rstrip()
            
            logger.info("imagen.generate.success", b64_chars=len(image_data))
            
            return {
                "prompt": clean_prompt,
                "base64_image": image_data,
                "mime_type": "image/png",
                "alt_text": alt_text,
            }

    except httpx.HTTPError as exc:
        raise ImagenError(f"HTTP error: {exc}")
    except Exception as exc:
        if isinstance(exc, (ImagenSafetyError, ImagenQuotaError)):
            raise
        logger.error("imagen.unexpected_error", error=str(exc))
        raise ImagenError(f"Unexpected error: {exc}")

def build_section_prompt(article_title: str, section_heading: str) -> str:
    """Create an Imagen-optimised prompt."""
    raw = (
        f"Professional editorial photograph illustrating "
        f"'{section_heading}' for article '{article_title}'. "
        "Photorealistic, natural lighting, high quality."
    )
    return _sanitise_prompt(raw)

def _sanitise_prompt(prompt: str) -> str:
    """Strip control characters and truncate."""
    clean = re.sub(r"\s+", " ", prompt.strip())
    return clean[:_PROMPT_MAX_CHARS]

def _handle_api_error(response: httpx.Response, logger: Any) -> None:
    """Classify and raise REST API errors."""
    status = response.status_code
    body = response.text.lower()
    
    if status == 429 or "quota" in body or "rate limit" in body:
        raise ImagenQuotaError(f"Imagen quota exceeded: {response.text}")
    
    if "safety" in body or "blocked" in body or "prohibited" in body or "filtered" in body:
        raise ImagenSafetyError(f"Imagen safety block: {response.text}")
    
    raise ImagenError(f"Imagen API error {status}: {response.text}")
