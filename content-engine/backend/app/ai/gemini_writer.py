"""
app/ai/gemini_writer.py — SEO rewriting via Vertex AI (Gemini).

Workflow 1 (Keyword → Article) and Workflow 2 (Link → Article) AI steps.
"""

from __future__ import annotations

import json
import re
import hashlib
import unicodedata

import bleach
import structlog

from app.config import settings
from app.redis import get_redis_client

log = structlog.get_logger(__name__)

# ── Initialize based on configuration ──────────────────────────────────────────
if settings.gemini_use_rest_api:
    # Use direct REST API client
    from app.ai.gemini_rest_client import (
        GeminiApiError,
        GeminiEmptyResponseError,
        GeminiResponseParseError,
        generate_content_rest,
        generate_content_rest_json,
    )

    USE_REST_API = True
else:
    # Use Vertex AI SDK
    import vertexai
    from vertexai.generative_models import (
        GenerationConfig,
        GenerativeModel,
        HarmBlockThreshold,
        HarmCategory,
        Part,
    )

    vertexai.init(project=settings.gcp_project_id)
    USE_REST_API = False

# ── Custom exceptions ──────────────────────────────────────────────────────────


class GeminiWriterError(Exception):
    """Base exception for AI writing failures."""


class GeminiResponseParseError(GeminiWriterError):
    """Raised when the model response cannot be parsed as valid JSON."""


class GeminiEmptyResponseError(GeminiWriterError):
    """Raised when the model returns an empty or blocked response."""


# ── HTML sanitisation allow-list (bleach) ─────────────────────────────────────
_ALLOWED_TAGS: list[str] = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "a",
    "blockquote",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "figure",
    "figcaption",
    "img",  # allowed for IMAGE_PLACEHOLDER substitution
]
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "title", "rel", "target"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "blockquote": ["cite"],
    "code": ["class"],
    "img": ["src", "alt", "title", "width", "height", "loading", "class"],
}

# ── Safety settings (SDK-only) ────────────────────────────────────────────────
if not USE_REST_API:
    _SAFETY_SETTINGS = {
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    }
else:
    _SAFETY_SETTINGS = {}

# ── System prompt (Workflow 2 rewrite) ────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are an Expert SEO Copywriter with 15 years of experience writing high-ranking
web content. Your task is to transform a raw article into a polished,
search-engine-optimised Markdown article and provide comprehensive SEO metadata.

WRITING GUIDELINES:
- Improve the structure with clear, keyword-rich headings (H1, H2, H3).
- Expand on concepts with additional context, examples, and statistics where appropriate.
- Use short, scannable paragraphs (2–4 sentences each).
- Add bullet lists or numbered lists to break up dense text.
- Write in an authoritative yet conversational tone suitable for a general audience.
- Ensure the article follows the E-E-A-T principle (Experience, Expertise,
  Authoritativeness, Trustworthiness).

IMAGE PROMPT GUIDELINES:
- Generate up to 5 descriptive image prompts for AI generation.
- Prompts should be detailed, specifying style (e.g., "photorealistic", "digital art"),
  composition, and lighting.
- Each prompt should relate directly to a section of the article.
- **CRITICAL**: You MUST inject image tags into the "html_content" where they should appear.
- Use this format for injection: `<img src="IMAGE_PLACEHOLDER_1">` for the first image, `<img src="IMAGE_PLACEHOLDER_2">` for the second, and so on.
- The featured image (IMAGE_PLACEHOLDER_1) should ideally be at the very top.
- Spread the other placeholders (2-5) naturally throughout the article body.

OUTPUT FORMAT — YOU MUST REPLY WITH A SINGLE, VALID JSON OBJECT AND NOTHING ELSE:
{
  "title": "<Compelling, keyword-focused article title>",
  "html_content": "<Full article body as clean, semantic HTML>",
  "target_keyword": "<Primary SEO focus keyword>",
  "slug": "<URL-friendly slug generated from the title>",
  "seo_title": "<Optimised SEO meta title (max 60 chars)>",
  "seo_description": "<Compelling SEO meta description (max 160 chars)>",
  "image_1_prompt": "<Detailed prompt for featured image>",
  "image_2_prompt": "<Detailed prompt for content image 1>",
  "image_3_prompt": "<Detailed prompt for content image 2>",
  "image_4_prompt": "<Detailed prompt for content image 3>",
  "image_5_prompt": "<Detailed prompt for content image 4>"
}

CRITICAL RULES:
- The content in "html_content" must be VALID semantic HTML.
- The JSON must be parseable by Python's json.loads() — no trailing commas,
  no comments, properly escaped strings.
- Reply with the JSON object only — no preamble, no explanation.
"""


# ── JSON Schemas for Controlled Generation (OpenAPI 3.0 subset) ────────────────

_REWRITE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "html_content": {"type": "STRING"},
        "target_keyword": {"type": "STRING"},
        "slug": {"type": "STRING"},
        "seo_title": {"type": "STRING"},
        "seo_description": {"type": "STRING"},
        "image_1_prompt": {"type": "STRING"},
        "image_2_prompt": {"type": "STRING"},
        "image_3_prompt": {"type": "STRING"},
        "image_4_prompt": {"type": "STRING"},
        "image_5_prompt": {"type": "STRING"},
    },
    "required": [
        "title",
        "html_content",
        "target_keyword",
        "slug",
        "seo_title",
        "seo_description",
    ],
}

_GAP_ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "focus_keyword": {"type": "STRING"},
        "covered_topics": {"type": "ARRAY", "items": {"type": "STRING"}},
        "content_gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        "recommended_angle": {"type": "STRING"},
        "suggested_title": {"type": "STRING"},
        "target_word_count": {"type": "INTEGER"},
        "semantic_keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "focus_keyword",
        "covered_topics",
        "content_gaps",
        "recommended_angle",
        "suggested_title",
        "target_word_count",
    ],
}

_OUTLINE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "meta_description": {"type": "STRING"},
        "sections": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "heading": {"type": "STRING"},
                    "type": {"type": "STRING", "enum": ["introduction", "body", "faq", "conclusion"]},
                    "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "subsections": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "heading": {"type": "STRING"},
                                "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
                            },
                            "required": ["heading", "key_points"],
                        },
                    },
                },
                "required": ["heading", "type", "key_points"],
            },
        },
    },
    "required": ["title", "meta_description", "sections"],
}


# ── Core function (Workflow 2) ────────────────────────────────────────────────


def rewrite_article_for_seo(markdown_content: str) -> dict:
    """
    Rewrite *markdown_content* as an SEO-optimised Markdown article and metadata using Gemini.
    """
    logger = log.bind(model=settings.vertex_ai_model, input_chars=len(markdown_content))
    logger.info("gemini_writer.rewrite.start")

    user_message = (
        "Please transform the following article into a polished, SEO-optimised "
        "Markdown article and provide comprehensive metadata as JSON, "
        "following all the guidelines in your system prompt.\n\n"
        "--- ARTICLE START ---\n"
        f"{markdown_content}\n"
        "--- ARTICLE END ---"
    )

    try:
        if USE_REST_API:
            parsed = generate_content_rest_json(
                system_prompt=_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.7,
                response_schema=_REWRITE_SCHEMA,
                logger=logger,
            )
        else:
            model = GenerativeModel(
                model_name=settings.vertex_ai_model,
                system_instruction=_SYSTEM_PROMPT,
            )
            generation_config = GenerationConfig(
                temperature=0.7,
                top_p=0.95,
                top_k=40,
                max_output_tokens=8192,
                response_mime_type="application/json",
            )
            response = model.generate_content(
                contents=[Part.from_text(user_message)],
                generation_config=generation_config,
                safety_settings=_SAFETY_SETTINGS,
            )
            raw_text = _extract_response_text(response, logger)
            parsed = _parse_json_response(raw_text, logger)
    except Exception as exc:
        msg = f"Gemini API call failed: {type(exc).__name__}: {exc}"
        logger.error("gemini_writer.api_error", error=str(exc))
        raise GeminiWriterError(msg) from exc

    _validate_keys(parsed)

    # Note: We now return the raw markdown_content from Gemini as requested
    title: str = parsed["title"].strip()
    
    # Minimal validation/cleanup of the dictionary
    result = {
        "title": title,
        "markdown_content": parsed["markdown_content"],
        "target_keyword": parsed.get("target_keyword", ""),
        "slug": parsed.get("slug") or _slugify(title),
        "seo_title": parsed.get("seo_title", ""),
        "seo_description": parsed.get("seo_description", ""),
        "image_1_prompt": parsed.get("image_1_prompt", ""),
        "image_2_prompt": parsed.get("image_2_prompt", ""),
        "image_3_prompt": parsed.get("image_3_prompt", ""),
        "image_4_prompt": parsed.get("image_4_prompt", ""),
        "image_5_prompt": parsed.get("image_5_prompt", ""),
    }
    
    logger.info("gemini_writer.rewrite.success", title=title)
    return result


# ── Private helpers ───────────────────────────────────────────────────────────


def _extract_response_text(response, logger: structlog.BoundLogger) -> str:
    """Pull the text string out of the Vertex AI response object."""
    try:
        raw_text: str = response.text
    except (AttributeError, ValueError) as exc:
        finish_reason = getattr(
            getattr(response, "candidates", [{}])[0], "finish_reason", "UNKNOWN"
        )
        msg = (
            f"Gemini returned no usable text (finish_reason={finish_reason}). "
            "The content may have been blocked by safety filters."
        )
        logger.warning("gemini_writer.safety_block", finish_reason=str(finish_reason))
        raise GeminiEmptyResponseError(msg) from exc

    if not raw_text or not raw_text.strip():
        logger.warning("gemini_writer.empty_response")
        raise GeminiEmptyResponseError("Gemini returned an empty text response.")

    return raw_text.strip()


def _parse_json_response(raw_text: str, logger: structlog.BoundLogger) -> dict:
    """Parse the model's raw text as JSON."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        msg = (
            f"Failed to parse Gemini response as JSON. "
            f"JSONDecodeError at pos {exc.pos}: {exc.msg}. "
            f"Raw response (first 500 chars): {raw_text[:500]!r}"
        )
        logger.error("gemini_writer.json_parse_error", error=str(exc))
        raise GeminiResponseParseError(msg) from exc

    if not isinstance(parsed, dict):
        msg = f"Gemini response parsed as {type(parsed).__name__} instead of a dict."
        logger.error("gemini_writer.response_not_dict", type=type(parsed).__name__)
        raise GeminiResponseParseError(msg)

    return parsed


def _validate_keys(parsed: dict) -> None:
    """Ensure required keys are present."""
    required = ("title", "markdown_content")
    for key in required:
        if key not in parsed:
            raise GeminiResponseParseError(f"Missing key: '{key}'")


def _slugify(text: str) -> str:
    """URL-safe slug."""
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:580] or "article"


# ── Unified Analysis Pattern ──────────────────────────────────────────────────

_UNIFIED_ANALYSIS_SYSTEM_PROMPT = """\
You are an Expert Content Strategist and SEO Copywriter. Your task is to perform a deep analysis of the provided content and return a structured JSON plan for a new high-quality article.

JSON SCHEMA REQUIREMENTS:
Reply with a SINGLE valid JSON object containing:
- "title": A compelling, SEO-optimised H1 title.
- "markdown_content": A brief summary or high-level overview of the intended article (not the full article yet).
- "slug": A URL-friendly slug.
- "focus_keyword": The primary keyword to target.
- "seo_title": An optimised meta title (max 60 chars).
- "seo_description": A compelling meta description (max 160 chars).
- "image_prompts": An array of objects, each with:
    - "prompt": Detailed Imagen generation prompt (style: photorealistic, 8k, professional).
    - "title": Title for the image.
    - "alt_text": Descriptive alt text.
    - "caption": Engaging caption.
    - "description": Detailed description for the media library.

CRITICAL: Return ONLY valid JSON. No preamble, no markdown blocks.
"""

_UNIFIED_ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "markdown_content": {"type": "STRING"},
        "slug": {"type": "STRING"},
        "focus_keyword": {"type": "STRING"},
        "seo_title": {"type": "STRING"},
        "seo_description": {"type": "STRING"},
        "image_prompts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "prompt": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "alt_text": {"type": "STRING"},
                    "caption": {"type": "STRING"},
                    "description": {"type": "STRING"},
                },
                "required": ["prompt", "title", "alt_text"],
            },
        },
    },
    "required": ["title", "slug", "focus_keyword", "seo_title", "seo_description", "image_prompts"],
}

def run_unified_analysis(source_content: str, logger: structlog.BoundLogger | None = None) -> dict:
    """
    Perform a single-shot Gemini 2.5 Flash request to extract all SEO and content parameters.
    Implements a self-correction loop for JSONDecodeError.
    """
    if logger is None:
        logger = log.bind(model="gemini-2.5-flash")

    logger.info("gemini_writer.unified_analysis.start", input_chars=len(source_content))

    user_message = (
        "Analyze the following source material and provide the requested Unified Analysis JSON:\n\n"
        f"--- SOURCE MATERIAL START ---\n{source_content[:15000]}\n--- SOURCE MATERIAL END ---"
    )

    # Use _call_gemini_json which already has a retry loop for JSON errors
    try:
        result = _call_gemini_json(
            system_prompt=_UNIFIED_ANALYSIS_SYSTEM_PROMPT,
            user_message=user_message,
            logger=logger,
            temperature=0.2, # Lower temperature for better JSON consistency
            response_schema=_UNIFIED_ANALYSIS_SCHEMA,
        )
        logger.info("gemini_writer.unified_analysis.success", title=result.get("title"))
        return result
    except Exception as exc:
        logger.error("gemini_writer.unified_analysis.failed", error=str(exc))
        raise GeminiWriterError(f"Unified Analysis failed: {exc}")

# ═════════════════════════════════════════════════════════════════════════════
# Workflow 1 Functions
# ═════════════════════════════════════════════════════════════════════════════

_GAP_ANALYSIS_SYSTEM_PROMPT = """\
You are a senior SEO Content Strategist. Analyze competitor article outlines and return a CONTENT GAP ANALYSIS.
YOU MUST REPLY WITH A SINGLE VALID JSON OBJECT.
"""

_OUTLINE_SYSTEM_PROMPT = """\
You are an Expert SEO Content Architect. Produce a structured ARTICLE OUTLINE JSON object.
YOU MUST REPLY WITH A SINGLE VALID JSON OBJECT.
"""

_SECTION_WRITER_BASE_PROMPT = """\
You are an Expert SEO Copywriter. Write the FULL HTML CONTENT for the given section.
OUTPUT FORMAT: ONLY CLEAN HTML, NO MARKDOWN, NO JSON.
{internal_linking_block}
{image_block}
"""

def _build_section_writer_prompt(
    related_articles: list[dict] | None = None,
    image_requested: bool = False,
    image_placeholder_id: str = "",
) -> str:
    linking_block = ""
    if related_articles:
        articles_text = "\n".join(f'- "{a["title"]}": {a["url"]}' for a in related_articles[:3])
        linking_block = f"INTERNAL LINKING (mandatory):\n{articles_text}"

    image_block = ""
    if image_requested and image_placeholder_id:
        image_block = f'IMAGE PLACEMENT: <img src="IMAGE_PLACEHOLDER_{image_placeholder_id}" alt="...">'

    return _SECTION_WRITER_BASE_PROMPT.format(
        internal_linking_block=linking_block,
        image_block=image_block,
    )

def analyze_content_gaps(scraped_articles: list[dict]) -> dict:
    logger = log.bind(model=settings.vertex_ai_model, articles=len(scraped_articles))
    compact = [{"url": a.get("url", ""), "content": a.get("content", "")[:2000]} for a in scraped_articles]
    user_message = f"Competitor outlines:\n{json.dumps(compact, indent=2)}"

    return _call_gemini_json(
        system_prompt=_GAP_ANALYSIS_SYSTEM_PROMPT,
        user_message=user_message,
        logger=logger,
        temperature=0.4,
        response_schema=_GAP_ANALYSIS_SCHEMA,
    )

def generate_article_outline(gap_analysis: dict) -> list[dict]:
    logger = log.bind(model=settings.vertex_ai_model)
    user_message = f"Gap analysis:\n{json.dumps(gap_analysis, indent=2)}"

    response_dict = _call_gemini_json(
        system_prompt=_OUTLINE_SYSTEM_PROMPT,
        user_message=user_message,
        logger=logger,
        temperature=0.5,
        response_schema=_OUTLINE_SCHEMA,
    )

    sections = response_dict.get("sections", [])
    meta = {"_meta": {"title": response_dict.get("title", ""), "meta_description": response_dict.get("meta_description", "")}}
    return [meta, *sections]

def write_article_from_outline(
    outline: list[dict],
    gap_analysis: dict,
    *,
    section_image_indices: set[int] | None = None,
    related_articles: list[dict] | None = None,
    generated_images: list[dict] | None = None,
) -> dict:
    logger = log.bind(model=settings.vertex_ai_model, sections=len(outline))
    section_image_indices = section_image_indices or set()
    related_articles = related_articles or []
    generated_images = generated_images or []

    meta_entry = outline[0] if outline and "_meta" in outline[0] else {}
    meta = meta_entry.get("_meta", {})
    sections = [s for s in outline if "_meta" not in s]

    title = meta.get("title") or "Article"
    
    html_parts = []
    image_placeholder_map = {}
    placeholder_counter = 0

    for idx, section in enumerate(sections):
        wants_image = idx in section_image_indices
        placeholder_id = ""
        if wants_image:
            placeholder_id = str(placeholder_counter)
            if placeholder_counter < len(generated_images):
                image_placeholder_map[placeholder_id] = placeholder_counter
            placeholder_counter += 1

        system_prompt = _build_section_writer_prompt(related_articles, wants_image, placeholder_id)
        user_msg = f"SECTION: {json.dumps(section)}"

        try:
            if USE_REST_API:
                section_html = generate_content_rest(
                    system_prompt=system_prompt,
                    user_message=user_msg,
                    generation_config={"temperature": 0.75, "maxOutputTokens": 4096},
                    logger=logger,
                )
            else:
                model = GenerativeModel(model_name=settings.vertex_ai_model, system_instruction=system_prompt)
                response = model.generate_content(
                    contents=[Part.from_text(user_msg)],
                    generation_config=GenerationConfig(temperature=0.75, max_output_tokens=4096),
                    safety_settings=_SAFETY_SETTINGS,
                )
                section_html = _extract_response_text(response, logger)
            
            section_html = re.sub(r"^```(?:html)?\s*", "", section_html, flags=re.IGNORECASE)
            section_html = re.sub(r"\s*```$", "", section_html).strip()
            html_parts.append(section_html)
        except Exception as exc:
            logger.error("write_section.failed", idx=idx, error=str(exc))

    raw_html = "\n\n".join(html_parts)
    raw_html = _substitute_image_placeholders(raw_html, image_placeholder_map, generated_images, logger)
    clean_html = bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES, strip=True)

    return {
        "title": title,
        "html_content": clean_html,
        "word_count": len(re.findall(r"\w+", clean_html)),
        "slug": _slugify(title),
        "meta_description": meta.get("meta_description", ""),
    }

def _substitute_image_placeholders(html: str, placeholder_map: dict[str, int], images: list[dict], logger: structlog.BoundLogger) -> str:
    def _replace(match: re.Match) -> str:
        placeholder_id = match.group(1)
        idx = placeholder_map.get(placeholder_id)
        if idx is not None and idx < len(images):
            img = images[idx]
            if img.get("base64_image"):
                return f"data:{img.get('mime_type', 'image/png')};base64,{img['base64_image']}"
            return img.get("url", "")
        return ""
    return re.sub(r"IMAGE_PLACEHOLDER_(\w+)", _replace, html)

def _call_gemini_json(
    system_prompt: str,
    user_message: str,
    logger: structlog.BoundLogger,
    temperature: float = 0.6,
    response_schema: dict | None = None,
) -> dict:
    _MAX_RETRIES = 2
    
    def _get_raw():
        if USE_REST_API:
            return generate_content_rest(
                system_prompt=system_prompt,
                user_message=user_message,
                generation_config={"temperature": temperature, "response_schema": response_schema} if response_schema else {"temperature": temperature},
                logger=logger,
            )
        else:
            model = GenerativeModel(model_name=settings.vertex_ai_model, system_instruction=system_prompt)
            gen_config = GenerationConfig(
                temperature=temperature,
                response_mime_type="application/json",
            )
            response = model.generate_content(contents=[Part.from_text(user_message)], generation_config=gen_config, safety_settings=_SAFETY_SETTINGS)
            return _extract_response_text(response, logger)

    attempt = 0
    while attempt <= _MAX_RETRIES:
        try:
            raw = _get_raw()
            return _parse_json_response(raw, logger)
        except Exception as exc:
            if attempt >= _MAX_RETRIES: raise
            attempt += 1
            logger.warning("gemini.retry", attempt=attempt, error=str(exc))
    return {}
`(?:html)?\s*", "", section_html, flags=re.IGNORECASE)
            section_html = re.sub(r"\s*```$", "", section_html).strip()
            html_parts.append(section_html)
        except Exception as exc:
            logger.error("write_section.failed", idx=idx, error=str(exc))

    raw_html = "\n\n".join(html_parts)
    raw_html = _substitute_image_placeholders(raw_html, image_placeholder_map, generated_images, logger)
    clean_html = bleach.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES, strip=True)

    return {
        "title": title,
        "html_content": clean_html,
        "word_count": len(re.findall(r"\w+", clean_html)),
        "slug": _slugify(title),
        "meta_description": meta.get("meta_description", ""),
    }

def _substitute_image_placeholders(html: str, placeholder_map: dict[str, int], images: list[dict], logger: structlog.BoundLogger) -> str:
    def _replace(match: re.Match) -> str:
        placeholder_id = match.group(1)
        idx = placeholder_map.get(placeholder_id)
        if idx is not None and idx < len(images):
            img = images[idx]
            if img.get("base64_image"):
                return f"data:{img.get('mime_type', 'image/png')};base64,{img['base64_image']}"
            return img.get("url", "")
        return ""
    return re.sub(r"IMAGE_PLACEHOLDER_(\w+)", _replace, html)

def _call_gemini_json(
    system_prompt: str,
    user_message: str,
    logger: structlog.BoundLogger,
    temperature: float = 0.6,
    response_schema: dict | None = None,
) -> dict:
    _MAX_RETRIES = 2
    
    def _get_raw():
        if USE_REST_API:
            return generate_content_rest(
                system_prompt=system_prompt,
                user_message=user_message,
                generation_config={"temperature": temperature, "response_schema": response_schema} if response_schema else {"temperature": temperature},
                logger=logger,
            )
        else:
            model = GenerativeModel(model_name=settings.vertex_ai_model, system_instruction=system_prompt)
            gen_config = GenerationConfig(
                temperature=temperature,
                response_mime_type="application/json",
            )
            response = model.generate_content(contents=[Part.from_text(user_message)], generation_config=gen_config, safety_settings=_SAFETY_SETTINGS)
            return _extract_response_text(response, logger)

    attempt = 0
    while attempt <= _MAX_RETRIES:
        try:
            raw = _get_raw()
            return _parse_json_response(raw, logger)
        except Exception as exc:
            if attempt >= _MAX_RETRIES: raise
            attempt += 1
            logger.warning("gemini.retry", attempt=attempt, error=str(exc))
    return {}
