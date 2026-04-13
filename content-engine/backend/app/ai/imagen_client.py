"""
app/ai/imagen_client.py — Imagen image generation via Vertex AI.

Workflow 1 (Keyword → Article) image enrichment step.

Flow:
  1. Receive a plain-text image prompt derived from an article section heading.
  2. Call Imagen via the Vertex AI Python SDK.
  3. Return the generated image as base64-encoded bytes plus metadata.

Safety & reliability:
  - Safety-filter rejections raise ImagenSafetyError (non-retryable).
  - Quota / service errors raise ImagenQuotaError (retryable by Celery).
  - All other SDK failures raise ImagenError (retryable).

The function is synchronous because it is called from Celery workers
(no running event loop). The Vertex AI SDK is itself synchronous.
"""
from __future__ import annotations

import base64
import re
import time
from typing import Final

import structlog
import vertexai
from vertexai.preview.vision_models import ImageGenerationModel

from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)

# ── Vertex AI init (done once at import time, idempotent) ─────────────────────
vertexai.init(
    project=settings.gcp_project_id,
    location=settings.gcp_location,
)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class ImagenError(Exception):
    """Base exception for Imagen failures. Celery will retry on this."""


class ImagenSafetyError(ImagenError):
    """
    Raised when Imagen rejects a prompt via its safety filters.

    This is NON-retryable — the same prompt will always be blocked.
    Callers should catch this, log it, and continue without an image.
    """


class ImagenQuotaError(ImagenError):
    """
    Raised when GCP quota or rate limits are hit.

    Celery should retry with exponential back-off.
    """


# ── Constants ──────────────────────────────────────────────────────────────────

# Phrases in exception messages that indicate a safety filter rejection.
_SAFETY_SIGNALS: Final[tuple[str, ...]] = (
    "safety",
    "policy",
    "blocked",
    "prohibited",
    "inappropriate",
    "violation",
)

# Phrases that indicate a quota / rate-limit issue.
_QUOTA_SIGNALS: Final[tuple[str, ...]] = (
    "quota",
    "rate limit",
    "resource exhausted",
    "429",
    "rateLimitExceeded",
)

# Default Imagen generation parameters.
_DEFAULT_ASPECT_RATIO: Final[str] = "16:9"       # wide, blog-friendly
_DEFAULT_NUMBER_OF_IMAGES: Final[int] = 1         # one image per section
_PROMPT_MAX_CHARS: Final[int] = 480               # Imagen prompt character limit


# ── Public interface ───────────────────────────────────────────────────────────

def generate_section_image(
    prompt: str,
    *,
    aspect_ratio: str = _DEFAULT_ASPECT_RATIO,
    negative_prompt: str = "text, watermark, logo, blurry, low quality, nsfw",
    retry_delay_s: float = 2.0,
) -> dict:
    """
    Generate a single image for an article section using Imagen.

    Args:
        prompt: Plain-text description of the desired image. Should be specific
                and relevant to the article section (e.g. "A modern Python
                developer working at a standing desk with multiple monitors").
        aspect_ratio: Image aspect ratio. One of "1:1", "9:16", "16:9",
                      "4:3", "3:4". Defaults to "16:9" (horizontal blog image).
        negative_prompt: Concepts to exclude from the generated image.
        retry_delay_s: Seconds to wait before the call if rate-limit signals
                       are expected. Useful for polite spacing between calls.

    Returns:
        A dict with the following keys:
          - ``prompt``       (str)  — sanitised prompt used for generation
          - ``base64_image`` (str)  — base64-encoded PNG image data
          - ``mime_type``    (str)  — always ``"image/png"``
          - ``alt_text``     (str)  — prompt truncated to 120 chars, used as alt tag

    Raises:
        ImagenSafetyError: Prompt was rejected by safety filters (non-retryable).
        ImagenQuotaError: GCP quota exceeded (retryable by Celery).
        ImagenError: Any other Vertex AI / generation failure (retryable).
    """
    # Sanitise and truncate prompt to Imagen's limit
    clean_prompt = _sanitise_prompt(prompt)
    logger = log.bind(
        model=settings.imagen_model,
        prompt_chars=len(clean_prompt),
        aspect_ratio=aspect_ratio,
    )
    logger.info("imagen.generate.start")

    if retry_delay_s > 0:
        time.sleep(retry_delay_s)

    try:
        model = ImageGenerationModel.from_pretrained(settings.imagen_model)

        response = model.generate_images(
            prompt=clean_prompt,
            number_of_images=_DEFAULT_NUMBER_OF_IMAGES,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            # add_watermark=False ensures clean output for blog use
            add_watermark=False,
        )

    except Exception as exc:
        _classify_and_raise(exc, clean_prompt, logger)

    # ── Extract image bytes from response ─────────────────────────────────────
    images = getattr(response, "images", None) or []
    if not images:
        # Response with empty images list = implicit safety block
        msg = (
            f"Imagen returned an empty images list for prompt: {clean_prompt!r}. "
            "This usually indicates a safety filter rejection."
        )
        logger.warning("imagen.generate.empty_response")
        raise ImagenSafetyError(msg)

    generated_image = images[0]

    # The SDK exposes raw PNG bytes via ._image_bytes (private but stable across SDK
    # versions). We access it defensively with a fallback attempt.
    image_bytes: bytes = _extract_image_bytes(generated_image, logger)

    b64_data: str = base64.b64encode(image_bytes).decode("utf-8")
    alt_text: str = clean_prompt[:120].rstrip()

    result = {
        "prompt": clean_prompt,
        "base64_image": b64_data,
        "mime_type": "image/png",
        "alt_text": alt_text,
    }

    logger.info(
        "imagen.generate.success",
        image_bytes=len(image_bytes),
        b64_chars=len(b64_data),
    )
    return result


def build_section_prompt(article_title: str, section_heading: str) -> str:
    """
    Create an Imagen-optimised prompt from an article title and section heading.

    Produces a photorealistic, blog-appropriate image prompt. Keeps it under
    Imagen's character limit.

    Args:
        article_title: The article's H1 title.
        section_heading: The H2/H3 section heading the image will accompany.

    Returns:
        A ready-to-use prompt string for ``generate_section_image()``.
    """
    raw = (
        f"Professional, high-quality editorial photograph illustrating the concept of "
        f"'{section_heading}' in the context of '{article_title}'. "
        "Shot on a mirrorless camera, photorealistic, natural lighting, "
        "suitable for a professional blog article."
    )
    return _sanitise_prompt(raw)


# ── Private helpers ───────────────────────────────────────────────────────────

def _sanitise_prompt(prompt: str) -> str:
    """Strip control characters and truncate to Imagen's character limit."""
    # Remove newlines and collapse whitespace
    clean = re.sub(r"\s+", " ", prompt.strip())
    return clean[:_PROMPT_MAX_CHARS]


def _extract_image_bytes(generated_image: object, logger: structlog.BoundLogger) -> bytes:
    """
    Extract raw bytes from a Vertex AI GeneratedImage object.

    The SDK offers two ways to access bytes:
      1. ``._image_bytes``  — private attribute, available since SDK 1.38
      2. ``.save()``        — saves to disk (we don't want disk I/O here)

    We use approach 1 with a thorough defensive fallback.
    """
    # Primary: private attribute (fastest, no I/O)
    image_bytes: bytes | None = getattr(generated_image, "_image_bytes", None)

    if image_bytes:
        return image_bytes

    # Fallback: some SDK versions expose .image_bytes (without underscore)
    image_bytes = getattr(generated_image, "image_bytes", None)
    if image_bytes:
        logger.debug("imagen.bytes_from_public_attr")
        return image_bytes

    # Last resort: write to a temp buffer via the public .save() interface
    import io
    try:
        buf = io.BytesIO()
        # The SDK's save() takes a file-like object or path string
        generated_image._pil_image.save(buf, format="PNG")  # type: ignore[attr-defined]
        buf.seek(0)
        logger.debug("imagen.bytes_from_pil")
        return buf.read()
    except Exception as exc:
        raise ImagenError(
            "Could not extract image bytes from Imagen response. "
            f"SDK object: {type(generated_image).__name__}. Error: {exc}"
        ) from exc


def _classify_and_raise(
    exc: Exception,
    prompt: str,
    logger: structlog.BoundLogger,
) -> None:
    """
    Inspect the exception message and raise the appropriate typed exception.

    This categorises raw SDK errors into:
      - ImagenSafetyError  (non-retryable)
      - ImagenQuotaError   (retryable)
      - ImagenError        (retryable, generic)
    """
    exc_msg = str(exc).lower()

    for signal in _SAFETY_SIGNALS:
        if signal in exc_msg:
            msg = (
                f"Imagen safety filter rejected prompt: {prompt!r}. "
                f"SDK error: {exc}"
            )
            logger.warning("imagen.generate.safety_block", prompt=prompt)
            raise ImagenSafetyError(msg) from exc

    for signal in _QUOTA_SIGNALS:
        if signal in exc_msg:
            msg = (
                f"Imagen quota/rate-limit hit for prompt: {prompt!r}. "
                f"SDK error: {exc}"
            )
            logger.warning("imagen.generate.quota_exceeded", error=str(exc))
            raise ImagenQuotaError(msg) from exc

    # Generic fallback
    msg = f"Imagen generate_images() failed: {type(exc).__name__}: {exc}"
    logger.error("imagen.generate.error", error=str(exc), exc_type=type(exc).__name__)
    raise ImagenError(msg) from exc
