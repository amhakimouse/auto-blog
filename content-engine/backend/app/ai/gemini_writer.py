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

log = structlog.get_logger(__name__)

# ── Initialize based on configuration ──────────────────────────────────────────
if settings.gemini_use_rest_api:
    # Use direct REST API client
    from app.ai.gemini_rest_client import (
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
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "a",
    "blockquote", "pre", "code", "table",
    "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption", "img",
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

# ── Schemas for Controlled Generation ─────────────────────────────────────────

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
    "required": ["focus_keyword", "covered_topics", "content_gaps", "recommended_angle", "suggested_title", "target_word_count"],
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
                },
                "required": ["heading", "type", "key_points"],
            },
        },
    },
    "required": ["title", "meta_description", "sections"],
}

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

# ── Workflow 1 (Keyword) Functions ────────────────────────────────────────────

_REWRITE_SYSTEM_PROMPT = """You are an Expert SEO Copywriter. Rewrite the provided content into a polished, high-quality article.
- "title": Compelling H1 title.
- "html_content": FULL article body in clean semantic HTML.
- "slug": URL-friendly slug.
- "target_keyword": Primary focus keyword.
- "seo_title": Meta title (max 60 chars).
- "seo_description": Meta description (max 160 chars).
Return ONLY valid JSON."""

_REWRITE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "html_content": {"type": "STRING"},
        "slug": {"type": "STRING"},
        "target_keyword": {"type": "STRING"},
        "seo_title": {"type": "STRING"},
        "seo_description": {"type": "STRING"},
    },
    "required": ["title", "html_content", "slug", "target_keyword", "seo_title", "seo_description"],
}

def rewrite_article_for_seo(markdown_content: str) -> dict:
    logger = log.bind(model=settings.vertex_ai_model, input_chars=len(markdown_content))
    logger.info("gemini_writer.rewrite.start")
    
    user_message = f"Rewrite this content:\n{markdown_content}"
    
    try:
        parsed = _call_gemini_json(
            system_prompt=_REWRITE_SYSTEM_PROMPT,
            user_message=user_message,
            logger=logger,
            temperature=0.7,
            response_schema=_REWRITE_SCHEMA,
        )
        logger.info("gemini_writer.rewrite.success", title=parsed.get("title"))
        return parsed
    except Exception as exc:
        logger.error("gemini_writer.rewrite.failed", error=str(exc))
        raise GeminiWriterError(f"Rewrite failed: {exc}")

def analyze_content_gaps(scraped_articles: list[dict]) -> dict:
    logger = log.bind(model=settings.vertex_ai_model, articles=len(scraped_articles))
    compact = [{"url": a.get("url", ""), "content": a.get("content", "")[:2000]} for a in scraped_articles]
    user_message = f"Competitor outlines:\n{json.dumps(compact, indent=2)}"
    
    return _call_gemini_json(
        system_prompt="You are a senior SEO Content Strategist. Analyze competitor article outlines and return a CONTENT GAP ANALYSIS.",
        user_message=user_message,
        logger=logger,
        temperature=0.4,
        response_schema=_GAP_ANALYSIS_SCHEMA,
    )

def generate_article_outline(gap_analysis: dict) -> list[dict]:
    logger = log.bind(model=settings.vertex_ai_model)
    user_message = f"Gap analysis:\n{json.dumps(gap_analysis, indent=2)}"

    response_dict = _call_gemini_json(
        system_prompt="You are an Expert SEO Content Architect. Produce a structured ARTICLE OUTLINE JSON object.",
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

# ── Unified Analysis (Workflow 1 & 2) ─────────────────────────────────────────

def run_unified_analysis(
    source_content: str,
    num_images: int = 2,
    logger: structlog.BoundLogger | None = None,
    language: str = "english",
    customization=None,  # ContentCustomization | None
) -> dict:
    """
    Single-shot Gemini analysis: extract title, slug, SEO fields, markdown
    summary, and exactly *num_images* image prompts in one API call.

    Args:
        source_content: Combined scraped markdown text.
        num_images:     Number of image_prompts to generate (matches user setting).
        logger:         Optional bound structlog logger.
        language:       Target output language (e.g. 'french', 'arabic').
                        Injected as a mandatory directive into the system prompt.
        customization:  Granular content controls (article_type, tone, image_style)
                        to guide the analysis and image prompt generation.
    """
    if logger is None:
        logger = log.bind(model="gemini-2.5-flash")

    # Sanitise language — strip whitespace, lowercase, cap length to prevent prompt injection
    language = (language or "english").strip().lower()[:40] or "english"

    num_images = max(0, min(num_images, 6))  # hard cap at 6
    logger.info(
        "gemini_writer.unified_analysis.start",
        input_chars=len(source_content),
        num_images=num_images,
        language=language,
    )

    # Language directive is placed at the TOP of the system prompt so the model
    # commits to the language before any other instruction is processed.
    language_directive = (
        f"CRITICAL LANGUAGE REQUIREMENT: The ENTIRE article — including the title, "
        f"all body content, meta description, SEO fields, and focus keyword — MUST be "
        f"written in {language.upper()}. Do NOT use any other language anywhere in the output."
    )

    # ── Content Controls Injection ──
    from app.ai.prompt_builder import _IMAGE_STYLE_LABELS, _ARTICLE_TYPE_GUIDANCE, _TONE_GUIDANCE
    
    style_str = "photorealistic"
    type_str = ""
    tone_str = ""
    if customization:
        style_str = _IMAGE_STYLE_LABELS.get(customization.image_style, "photorealistic")
        type_str = f"ARTICLE TYPE: {_ARTICLE_TYPE_GUIDANCE.get(customization.article_type, '')}\n"
        tone_str = f"TONE: {_TONE_GUIDANCE.get(customization.tone_of_voice, '')}\n"

    system_prompt = (
        f"{language_directive}\n\n"
        "You are an Expert Content Strategist and SEO Copywriter. "
        "Perform a deep analysis of the provided content and return a structured JSON article plan.\n"
        f"{type_str}"
        f"{tone_str}"
        '- "title": Compelling H1 title.\n'
        '- "markdown_content": High-level content summary (markdown, 300-600 words).\n'
        '- "slug": URL-friendly slug (lowercase, hyphens only).\n'
        '- "focus_keyword": Primary SEO keyword.\n'
        '- "seo_title": Meta title (max 60 chars).\n'
        '- "seo_description": Meta description (max 160 chars).\n'
        f'- "image_prompts": Array of EXACTLY {num_images} objects, each with: '
        f'"prompt" (detailed Imagen generation prompt, {style_str}), '
        '"title" (short image title), "alt_text", "caption", "description".\n'
        "Return ONLY valid JSON matching the schema. No markdown fences."
    )

    # Repeat the language reminder in the user message to reinforce compliance
    user_message = (
        f"[LANGUAGE: {language.upper()}]\n\n"
        f"Analyze this content:\n\n{source_content[:15_000]}"
    )

    try:
        result = _call_gemini_json(
            system_prompt=system_prompt,
            user_message=user_message,
            logger=logger,
            temperature=0.2,
            response_schema=_UNIFIED_ANALYSIS_SCHEMA,
        )
        # Validate image_prompts count — pad or trim to requested count
        prompts = result.get("image_prompts", [])
        if len(prompts) > num_images:
            result["image_prompts"] = prompts[:num_images]
        elif len(prompts) < num_images and prompts:
            # Duplicate last prompt if Gemini returned fewer than requested
            while len(result["image_prompts"]) < num_images:
                result["image_prompts"].append(prompts[-1])

        logger.info(
            "gemini_writer.unified_analysis.success",
            title=result.get("title"),
            image_prompts=len(result.get("image_prompts", [])),
            language=language,
        )
        return result
    except Exception as exc:
        logger.error("gemini_writer.unified_analysis.failed", error=str(exc))
        raise GeminiWriterError(f"Unified Analysis failed: {exc}")

# ── Private Helpers ───────────────────────────────────────────────────────────

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
            gen_config = GenerationConfig(temperature=temperature, response_mime_type="application/json")
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

def _extract_response_text(response, logger: structlog.BoundLogger) -> str:
    try:
        raw_text: str = response.text
        return raw_text.strip()
    except Exception as exc:
        finish_reason = getattr(getattr(response, "candidates", [{}])[0], "finish_reason", "UNKNOWN")
        logger.warning("gemini_writer.no_text", finish_reason=str(finish_reason))
        raise GeminiEmptyResponseError(f"No text (reason={finish_reason})") from exc

def _parse_json_response(raw_text: str, logger: structlog.BoundLogger) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("gemini_writer.json_parse_error", error=str(exc), raw=raw_text[:200])
        raise GeminiResponseParseError(f"JSON error: {exc}")

def _build_section_writer_prompt(related_articles: list[dict] | None, image_requested: bool, image_placeholder_id: str) -> str:
    prompt = "You are an Expert SEO Copywriter. Write ONLY CLEAN HTML for this section."
    if related_articles:
        links = "\n".join(f'- "{a["title"]}": {a["url"]}' for a in related_articles[:3])
        prompt += f"\nINTERNAL LINKING (mandatory):\n{links}"
    if image_requested and image_placeholder_id:
        prompt += f'\nIMAGE: <img src="IMAGE_PLACEHOLDER_{image_placeholder_id}" alt="...">'
    return prompt

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

def _slugify(text: str) -> str:
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:580] or "article"
