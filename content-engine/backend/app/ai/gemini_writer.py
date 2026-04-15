"""
app/ai/gemini_writer.py — SEO rewriting via Vertex AI (Gemini).

Workflow 1 (Keyword → Article) and Workflow 2 (Link → Article) AI steps.

Changes:
  - _call_gemini_json: JSON self-correction loop (up to 2 retries on JSONDecodeError).
  - _SECTION_WRITER_SYSTEM_PROMPT: humanisation rules, internal linking injection,
    configurable IMAGE_PLACEHOLDER tags for image distribution.
  - write_article_from_outline: accepts related_articles and section_image_indices;
    substitutes IMAGE_PLACEHOLDER_{id} with real base64 <img> or URL tags.
  - analyze_content_gaps: receives pre-summarised outlines (not raw HTML bodies).
"""

from __future__ import annotations

import json
import re
import unicodedata

import bleach
import structlog

from app.config import settings

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
web content. Your task is to transform a raw Markdown article into a polished,
search-engine-optimised HTML article.

WRITING GUIDELINES:
- Improve the structure with clear, keyword-rich headings (H1, H2, H3).
- Expand on concepts with additional context, examples, and statistics where appropriate.
- Use short, scannable paragraphs (2–4 sentences each).
- Add bullet lists or numbered lists to break up dense text.
- Write in an authoritative yet conversational tone suitable for a general audience.
- Ensure the article follows the E-E-A-T principle (Experience, Expertise,
  Authoritativeness, Trustworthiness).

OUTPUT FORMAT — YOU MUST REPLY WITH A SINGLE, VALID JSON OBJECT AND NOTHING ELSE:
{
  "title": "<Compelling, keyword-focused article title as plain text — no HTML>",
  "html_content": "<Full article body as clean, semantic HTML using only: \
h1, h2, h3, p, ul, ol, li, strong, em, blockquote, table, thead, tbody, tr, th, td>"
}

CRITICAL RULES:
- Do NOT include markdown in the output — only clean HTML inside html_content.
- Do NOT include <html>, <head>, <body>, or <style> wrapper tags.
- Do NOT include any JavaScript or inline style attributes.
- The JSON must be parseable by Python's json.loads() — no trailing commas,
  no comments, properly escaped strings.
- Reply with the JSON object only — no preamble, no explanation.
"""


# ── Core function (Workflow 2) ────────────────────────────────────────────────


def rewrite_article_for_seo(markdown_content: str) -> dict:
    """
    Rewrite *markdown_content* as an SEO-optimised HTML article using Gemini.

    Returns:
        A dict with: title, html_content, word_count, slug.
    """
    logger = log.bind(model=settings.vertex_ai_model, input_chars=len(markdown_content))
    logger.info("gemini_writer.rewrite.start")

    user_message = (
        "Please rewrite the following Markdown article as SEO-optimised HTML, "
        "following all the guidelines in your system prompt.\n\n"
        "--- ARTICLE MARKDOWN START ---\n"
        f"{markdown_content}\n"
        "--- ARTICLE MARKDOWN END ---"
    )

    try:
        if USE_REST_API:
            # Use direct REST API
            parsed = generate_content_rest_json(
                system_prompt=_SYSTEM_PROMPT,
                user_message=user_message,
                temperature=0.7,
                logger=logger,
            )
        else:
            # Use Vertex AI SDK
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

    dirty_html: str = parsed["html_content"]
    clean_html: str = bleach.clean(
        dirty_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        strip=True,
        strip_comments=True,
    )

    title: str = parsed["title"].strip()
    word_count: int = len(re.findall(r"\w+", clean_html))
    slug: str = _slugify(title)

    result = {
        "title": title,
        "html_content": clean_html,
        "word_count": word_count,
        "slug": slug,
    }
    logger.info("gemini_writer.rewrite.success", title=title, word_count=word_count)
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
    """
    Parse the model's raw text as JSON.

    Strips optional ```json fences before parsing.
    Does NOT retry here — retries belong in _call_gemini_json.
    """
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
        logger.error(
            "gemini_writer.json_parse_error",
            error=str(exc),
            raw_preview=raw_text[:200],
        )
        raise GeminiResponseParseError(msg) from exc

    if not isinstance(parsed, dict):
        msg = (
            f"Gemini response parsed as {type(parsed).__name__} instead of a dict. "
            f"Raw: {raw_text[:200]!r}"
        )
        logger.error("gemini_writer.response_not_dict", type=type(parsed).__name__)
        raise GeminiResponseParseError(msg)

    return parsed


def _validate_keys(parsed: dict) -> None:
    """Ensure both required keys are present and non-empty strings."""
    required = ("title", "html_content")
    for key in required:
        if key not in parsed:
            raise GeminiResponseParseError(
                f"Gemini JSON response missing required key: '{key}'. "
                f"Keys present: {list(parsed.keys())}"
            )
        if not isinstance(parsed[key], str) or not parsed[key].strip():
            raise GeminiResponseParseError(
                f"Gemini JSON key '{key}' must be a non-empty string. "
                f"Got: {type(parsed[key]).__name__!r} = {str(parsed[key])[:100]!r}"
            )


def _slugify(text: str) -> str:
    """Convert a title to a URL-safe ASCII slug, e.g. 'Hello World' → 'hello-world'."""
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    lower = ascii_text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lower)
    slug = slug.strip("-")
    return slug[:580] or "article"


# ═════════════════════════════════════════════════════════════════════════════
# Workflow 1 (Keyword → Article) — multi-step Gemini functions
# ═════════════════════════════════════════════════════════════════════════════

# ── System prompts ────────────────────────────────────────────────────────────

_GAP_ANALYSIS_SYSTEM_PROMPT = """\
You are a senior SEO Content Strategist. You will receive a JSON array of
competitor article STRUCTURAL OUTLINES (headings + first paragraph under each heading).
This is a dense, token-efficient summary — not the full article body.

Your task is to perform a thorough CONTENT GAP ANALYSIS:
1. Identify the main topics covered across all competitor articles.
2. Identify topics, subtopics, or angles that are MISSING or underrepresented.
3. Identify the keywords and phrases used most frequently.
4. Recommend the optimal content strategy to outrank these competitors.

YOU MUST REPLY WITH A SINGLE VALID JSON OBJECT:
{
  "focus_keyword": "<primary keyword to target>",
  "covered_topics": ["<topic 1>", "<topic 2>", ...],
  "content_gaps": ["<missing topic 1>", "<missing topic 2>", ...],
  "recommended_angle": "<1–2 sentence description of the best strategy>",
  "suggested_title": "<compelling article title that targets the focus keyword>",
  "target_word_count": <integer, recommended article length>,
  "semantic_keywords": ["<related keyword 1>", "<related keyword 2>", ...]
}

RULES:
- Reply with JSON only — no preamble, no explanation.
- All values must be properly escaped strings or numbers.
"""

_OUTLINE_SYSTEM_PROMPT = """\
You are an Expert SEO Content Architect. You will receive a content gap analysis
JSON object (the output of a previous analysis step).

Your task is to produce a STRUCTURED ARTICLE OUTLINE with sections that:
1. Address every content gap identified.
2. Follow the recommended angle exactly.
3. Are ordered for maximum reader engagement and SEO crawlability.
4. Include a compelling H1 title and descriptive H2 / H3 section names.

YOU MUST REPLY WITH A SINGLE VALID JSON OBJECT:
{
  "title": "<H1 article title — plain text, no HTML>",
  "meta_description": "<150-160 char SEO meta description>",
  "sections": [
    {
      "heading": "<H2 section heading>",
      "type": "introduction|body|faq|conclusion",
      "key_points": ["<point to cover>", ...],
      "subsections": [
        {"heading": "<H3 subheading>", "key_points": ["<point>", ...]}
      ]
    },
    ...
  ]
}

RULES:
- Reply with JSON only.
- Produce between 5 and 10 top-level sections.
- The first section type must be "introduction" and the last "conclusion".
- subsections may be an empty list [] if not needed.
"""

# NOTE: This prompt is built dynamically by _build_section_writer_prompt() so
# that related_articles and image_requested can be injected per-call.
_SECTION_WRITER_BASE_PROMPT = """\
You are an Expert SEO Copywriter with 15 years of experience. You will receive
a single article section definition (heading + key points to cover) and the
overall article context (title, focus keyword, recommended angle).

Your task is to write the FULL HTML CONTENT for that section only.

OUTPUT FORMAT — ONLY CLEAN HTML, NO MARKDOWN, NO JSON WRAPPER:
- Start directly with the section heading tag (e.g. <h2>).
- Use <h2> for the section heading, <h3> for any subsections.
- Write in short, scannable paragraphs (2–4 sentences each).
- Naturally weave the focus keyword and semantic keywords throughout.
- No <html>, <head>, <body>, <style>, or <script> tags.
- No inline styles. No JavaScript.
- Each section must be 150–400 words (calibrated to key_points count).
- Return ONLY the HTML — no preamble, no explanation.

HUMANISATION RULES (mandatory — your text will be tested against AI detectors):
- Use BURSTINESS: intentionally mix very short, punchy sentences (3-6 words)
  with longer, more detailed ones. Vary your rhythm constantly.
- NEVER use these robotic transition words: "Furthermore", "Moreover",
  "In conclusion", "Additionally", "It is worth noting", "It should be noted",
  "Lastly", "In summary", "To summarise".
- NEVER use these cliché AI phrases: "delve into", "unleash", "transformative",
  "testament to", "game-changer", "groundbreaking", "revolutionise",
  "dive deep", "cutting-edge", "robust", "landscape", "leverage" (as a verb).
- Write as a knowledgeable human would: use contractions (don't, you'll, it's),
  rhetorical questions, and occasional first-person ("In my experience...").
{internal_linking_block}
{image_block}
"""


def _build_section_writer_prompt(
    related_articles: list[dict] | None = None,
    image_requested: bool = False,
    image_placeholder_id: str = "",
) -> str:
    """
    Construct the section writer system prompt dynamically, injecting:
      - Internal linking instructions (if related_articles provided).
      - Image placeholder instruction (if image_requested is True).
    """
    # ── Internal linking block ────────────────────────────────────────────────
    if related_articles:
        articles_text = "\n".join(
            f'  - "{a["title"]}": {a["url"]}' for a in related_articles[:3]
        )
        linking_block = (
            "INTERNAL LINKING (mandatory):\n"
            "You MUST weave 1 or 2 contextual internal links into this section "
            "using the related articles listed below. Use natural anchor text that "
            "fits the surrounding sentence — never bare URLs.\n"
            f"Related articles:\n{articles_text}"
        )
    else:
        linking_block = ""

    # ── Image placeholder block ───────────────────────────────────────────────
    if image_requested and image_placeholder_id:
        image_block = (
            "IMAGE PLACEMENT (mandatory for this section):\n"
            f'Place exactly one <img src="IMAGE_PLACEHOLDER_{image_placeholder_id}" '
            f'alt="..."> tag at the single most contextually appropriate position '
            "within your HTML output. Choose the alt text to describe the ideal image "
            "for this section. Do NOT place the tag inside a <p> — use a standalone "
            "<figure><img ...><figcaption>...</figcaption></figure> block."
        )
    else:
        image_block = ""

    return _SECTION_WRITER_BASE_PROMPT.format(
        internal_linking_block=linking_block,
        image_block=image_block,
    )


# ── Public functions ───────────────────────────────────────────────────────────


def analyze_content_gaps(scraped_articles: list[dict]) -> dict:
    """
    Analyse a list of competitor article structural outlines and return a gap analysis.

    Args:
        scraped_articles: List of dicts, each with ``"url"`` (str) and
                          ``"content"`` (str) keys. The ``content`` field is
                          expected to be a pre-summarised outline (headings +
                          first paragraphs), NOT raw HTML or full bodies.
                          Produced by keyword_pipeline._extract_outline_summary().

    Returns:
        A dict with keys: focus_keyword, covered_topics, content_gaps,
        recommended_angle, suggested_title, target_word_count, semantic_keywords.
    """
    logger = log.bind(
        model=settings.vertex_ai_model,
        articles=len(scraped_articles),
        fn="analyze_content_gaps",
    )
    logger.info("gemini_writer.gap_analysis.start")

    # The content is already a token-efficient outline summary — no truncation needed.
    # We still cap at 2000 chars per article as a safety net.
    compact = [
        {"url": a.get("url", ""), "content": a.get("content", "")[:2000]}
        for a in scraped_articles
    ]
    user_message = (
        "Perform a content gap analysis on the following competitor article outlines:\n\n"
        f"{json.dumps(compact, ensure_ascii=False, indent=2)}"
    )

    response_dict = _call_gemini_json(
        system_prompt=_GAP_ANALYSIS_SYSTEM_PROMPT,
        user_message=user_message,
        logger=logger,
        temperature=0.4,
    )

    required = (
        "focus_keyword",
        "covered_topics",
        "content_gaps",
        "recommended_angle",
        "suggested_title",
        "target_word_count",
    )
    for key in required:
        if key not in response_dict:
            raise GeminiResponseParseError(
                f"Gap analysis response missing key: '{key}'. "
                f"Keys present: {list(response_dict.keys())}"
            )

    logger.info(
        "gemini_writer.gap_analysis.done",
        focus_keyword=response_dict.get("focus_keyword"),
        gaps=len(response_dict.get("content_gaps", [])),
    )
    return response_dict


def generate_article_outline(gap_analysis: dict) -> list[dict]:
    """
    Generate a structured article outline from the gap analysis.

    Returns:
        A list of section dicts. The first element is a synthetic ``_meta``
        sentinel for downstream consumers (title + meta_description).
    """
    logger = log.bind(
        model=settings.vertex_ai_model,
        focus_keyword=gap_analysis.get("focus_keyword"),
        fn="generate_article_outline",
    )
    logger.info("gemini_writer.outline.start")

    user_message = (
        "Generate a structured article outline based on the following "
        "content gap analysis:\n\n"
        f"{json.dumps(gap_analysis, ensure_ascii=False, indent=2)}"
    )

    response_dict = _call_gemini_json(
        system_prompt=_OUTLINE_SYSTEM_PROMPT,
        user_message=user_message,
        logger=logger,
        temperature=0.5,
    )

    sections: list[dict] = response_dict.get("sections", [])
    if not sections:
        raise GeminiResponseParseError(
            "Outline response contained no sections. "
            f"Raw keys: {list(response_dict.keys())}"
        )

    meta = {
        "_meta": {
            "title": response_dict.get(
                "title", gap_analysis.get("suggested_title", "")
            ),
            "meta_description": response_dict.get("meta_description", ""),
        }
    }
    sections_with_meta: list[dict] = [meta, *sections]

    logger.info(
        "gemini_writer.outline.done",
        title=meta["_meta"]["title"],
        section_count=len(sections),
    )
    return sections_with_meta


def write_article_from_outline(
    outline: list[dict],
    gap_analysis: dict,
    *,
    section_image_indices: set[int] | None = None,
    related_articles: list[dict] | None = None,
    generated_images: list[dict] | None = None,
) -> dict:
    """
    Write the full article by calling Gemini once per section and assembling HTML.

    Args:
        outline: List from ``generate_article_outline()``, including ``_meta``.
        gap_analysis: Original gap analysis dict (provides context per prompt).
        section_image_indices: Set of 0-based section indices that should receive
            an IMAGE_PLACEHOLDER tag. Provided by the pipeline after distributing
            content_image_count evenly across sections.
        related_articles: Optional list of dicts with ``title`` and ``url`` keys
            for internal linking (2–3 articles recommended).
        generated_images: List of image dicts from imagen_client (base64_image,
            alt_text, mime_type). Substituted into IMAGE_PLACEHOLDER_{id} tags
            after assembly. If None, placeholders are left as-is.

    Returns:
        A dict with: title, html_content, meta_description, word_count, slug,
        focus_keyword, semantic_keywords.
    """
    logger = log.bind(
        model=settings.vertex_ai_model,
        sections=len(outline),
        fn="write_article_from_outline",
    )
    logger.info("gemini_writer.write_article.start")

    section_image_indices = section_image_indices or set()
    related_articles = related_articles or []
    generated_images = generated_images or []

    # ── Extract _meta sentinel ────────────────────────────────────────────────
    meta_entry = outline[0] if outline and "_meta" in outline[0] else {}
    meta = meta_entry.get("_meta", {})
    sections = [s for s in outline if "_meta" not in s]

    title: str = meta.get("title") or gap_analysis.get("suggested_title", "Article")
    meta_description: str = meta.get("meta_description", "")
    focus_keyword: str = gap_analysis.get("focus_keyword", "")
    semantic_keywords: list[str] = gap_analysis.get("semantic_keywords", [])

    article_context = (
        f"Article Title: {title}\n"
        f"Focus Keyword: {focus_keyword}\n"
        f"Recommended Angle: {gap_analysis.get('recommended_angle', '')}\n"
        f"Semantic Keywords: {', '.join(semantic_keywords[:10])}"
    )

    # ── Write each section ────────────────────────────────────────────────────
    html_parts: list[str] = []
    # Track which IMAGE_PLACEHOLDER IDs we've committed so we can substitute later
    image_placeholder_map: dict[
        str, int
    ] = {}  # placeholder_id → generated_images index
    placeholder_counter = 0

    gen_config = None
    if not USE_REST_API:
        gen_config = GenerationConfig(
            temperature=0.75,
            top_p=0.95,
            top_k=40,
            max_output_tokens=2048,
        )

    for idx, section in enumerate(sections):
        wants_image = idx in section_image_indices
        placeholder_id = ""
        if wants_image:
            placeholder_id = str(placeholder_counter)
            # Map this placeholder to the next available generated image
            if placeholder_counter < len(generated_images):
                image_placeholder_map[placeholder_id] = placeholder_counter
            placeholder_counter += 1

        # Build a fresh system prompt per section (linking + image instructions)
        system_prompt = _build_section_writer_prompt(
            related_articles=related_articles if related_articles else None,
            image_requested=wants_image,
            image_placeholder_id=placeholder_id,
        )

        if USE_REST_API:
            # For REST API, we'll call generate_content_rest directly
            pass
        else:
            model = GenerativeModel(
                model_name=settings.vertex_ai_model,
                system_instruction=system_prompt,
            )

        section_logger = logger.bind(
            section_idx=idx,
            heading=section.get("heading"),
            wants_image=wants_image,
        )
        section_logger.info("gemini_writer.write_section.start")

        user_msg = (
            f"ARTICLE CONTEXT:\n{article_context}\n\n"
            f"SECTION TO WRITE:\n"
            f"{json.dumps(section, ensure_ascii=False, indent=2)}"
        )

        try:
            if USE_REST_API:
                section_html = generate_content_rest(
                    system_prompt=system_prompt,
                    user_message=user_msg,
                    generation_config={"temperature": 0.75, "maxOutputTokens": 2048},
                    logger=section_logger,
                )
            else:
                response = model.generate_content(
                    contents=[Part.from_text(user_msg)],
                    generation_config=gen_config,
                    safety_settings=_SAFETY_SETTINGS,
                )
                section_html = _extract_response_text(response, section_logger)
        except GeminiEmptyResponseError:
            section_logger.warning("gemini_writer.write_section.skipped_safety_block")
            continue
        except Exception as exc:
            msg = f"Section write failed (idx={idx}): {type(exc).__name__}: {exc}"
            section_logger.error("gemini_writer.write_section.error", error=str(exc))
            raise GeminiWriterError(msg) from exc

        # Strip accidental code fences
        section_html = re.sub(
            r"^```(?:html)?\s*", "", section_html, flags=re.IGNORECASE
        )
        section_html = re.sub(r"\s*```$", "", section_html).strip()

        html_parts.append(section_html)
        section_logger.info("gemini_writer.write_section.done", chars=len(section_html))

    if not html_parts:
        raise GeminiWriterError(
            "All article sections were skipped or failed — no HTML generated."
        )

    # ── Assemble raw HTML ─────────────────────────────────────────────────────
    raw_html = "\n\n".join(html_parts)

    # ── Substitute IMAGE_PLACEHOLDER_{id} with real <figure><img> tags ──────
    raw_html = _substitute_image_placeholders(
        raw_html, image_placeholder_map, generated_images, logger
    )

    # ── Sanitise (bleach) ─────────────────────────────────────────────────────
    clean_html = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        strip=True,
        strip_comments=True,
    )
    word_count = len(re.findall(r"\w+", clean_html))

    result = {
        "title": title,
        "html_content": clean_html,
        "meta_description": meta_description,
        "word_count": word_count,
        "slug": _slugify(title),
        "focus_keyword": focus_keyword,
        "semantic_keywords": semantic_keywords,
    }

    logger.info(
        "gemini_writer.write_article.done",
        title=title,
        word_count=word_count,
        sections_written=len(html_parts),
        images_substituted=len(image_placeholder_map),
    )
    return result


# ── Image placeholder substitution ───────────────────────────────────────────


def _substitute_image_placeholders(
    html: str,
    placeholder_map: dict[str, int],
    generated_images: list[dict],
    logger: structlog.BoundLogger,
) -> str:
    """
    Replace IMAGE_PLACEHOLDER_{id} src attributes with real image data.

    If the image is base64-encoded, uses a data URI (data:image/png;base64,...).
    If the image has a ``url`` key instead, uses the URL directly.
    Unmatched placeholders (no generated image available) are replaced with an
    empty string so they don't appear as broken images.
    """

    def _replace(match: re.Match) -> str:
        placeholder_id = match.group(1)
        img_idx = placeholder_map.get(placeholder_id)
        if img_idx is None or img_idx >= len(generated_images):
            logger.warning(
                "gemini_writer.placeholder_no_image",
                placeholder_id=placeholder_id,
            )
            return ""  # remove the whole src value

        img = generated_images[img_idx]
        if img.get("base64_image"):
            mime = img.get("mime_type", "image/png")
            return f"data:{mime};base64,{img['base64_image']}"
        if img.get("url"):
            return img["url"]
        return ""

    # Match the src attribute value IMAGE_PLACEHOLDER_{id} inside img tags
    substituted = re.sub(
        r"IMAGE_PLACEHOLDER_(\w+)",
        _replace,
        html,
    )
    return substituted


# ── Shared private helper ─────────────────────────────────────────────────────


def _call_gemini_json(
    system_prompt: str,
    user_message: str,
    logger: structlog.BoundLogger,
    temperature: float = 0.6,
    *,
    _retry_count: int = 0,
) -> dict:
    """
    Call Gemini with *system_prompt* + *user_message*, expect a JSON response.

    Self-correction loop: on JSONDecodeError, appends the error to the
    conversation and retries up to 2 times before raising permanently.

    Returns:
        Parsed dict from the model response.

    Raises:
        GeminiWriterError, GeminiEmptyResponseError, GeminiResponseParseError
    """
    _MAX_SELF_CORRECTION_RETRIES = 2

    # ── Call based on configured mode ──────────────────────────────────────────
    if USE_REST_API:
        try:
            raw_text = generate_content_rest(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                logger=logger,
            )
        except Exception as exc:
            msg = f"Gemini REST API call failed: {type(exc).__name__}: {exc}"
            logger.error("gemini_writer.api_error", error=str(exc))
            raise GeminiWriterError(msg) from exc
    else:
        # Use Vertex AI SDK
        model = GenerativeModel(
            model_name=settings.vertex_ai_model,
            system_instruction=system_prompt,
        )
        gen_config = GenerationConfig(
            temperature=temperature,
            top_p=0.95,
            top_k=40,
            max_output_tokens=4096,
            response_mime_type="application/json",
        )

        # Build conversation contents. On retries, the previous bad response and
        # the correction request are appended so the model sees its own mistake.
        contents: list = [Part.from_text(user_message)]

        try:
            response = model.generate_content(
                contents=contents,
                generation_config=gen_config,
                safety_settings=_SAFETY_SETTINGS,
            )
            raw_text = _extract_response_text(response, logger)
        except Exception as exc:
            msg = f"Vertex AI call failed: {type(exc).__name__}: {exc}"
            logger.error("gemini_writer.api_error", error=str(exc))
            raise GeminiWriterError(msg) from exc

    # ── Self-correction loop ───────────────────────────────────────────────────
    attempt = 0
    current_raw = raw_text
    while attempt <= _MAX_SELF_CORRECTION_RETRIES:
        try:
            return _parse_json_response(current_raw, logger)
        except GeminiResponseParseError as parse_exc:
            if attempt >= _MAX_SELF_CORRECTION_RETRIES:
                logger.error(
                    "gemini_writer.self_correction.exhausted",
                    attempts=attempt + 1,
                )
                raise  # bubble original parse error after max retries

            attempt += 1
            logger.warning(
                "gemini_writer.self_correction.retry",
                attempt=attempt,
                error=str(parse_exc),
            )

            # Build a correction message and call the model again
            correction_message = (
                f"{user_message}\n\n"
                f"--- YOUR PREVIOUS RESPONSE (attempt {attempt}) ---\n"
                f"{current_raw}\n"
                f"--- END PREVIOUS RESPONSE ---\n\n"
                f"Your previous response failed to parse with JSONDecodeError: "
                f"{parse_exc}. "
                "Please rewrite your exact response as valid JSON. "
                "Return ONLY the JSON object — no preamble, no explanation, "
                "no markdown fences."
            )

            if USE_REST_API:
                try:
                    current_raw = generate_content_rest(
                        system_prompt=system_prompt,
                        user_message=correction_message,
                        temperature=temperature,
                        logger=logger,
                    )
                except Exception as exc:
                    msg = f"Gemini REST API self-correction call failed: {type(exc).__name__}: {exc}"
                    logger.error("gemini_writer.api_error", error=str(exc))
                    raise GeminiWriterError(msg) from exc
            else:
                correction_contents: list = [Part.from_text(correction_message)]
                try:
                    correction_response = model.generate_content(
                        contents=correction_contents,
                        generation_config=gen_config,
                        safety_settings=_SAFETY_SETTINGS,
                    )
                    current_raw = _extract_response_text(correction_response, logger)
                except Exception as exc:
                    msg = f"Vertex AI self-correction call failed: {type(exc).__name__}: {exc}"
                    logger.error("gemini_writer.api_error", error=str(exc))
                    raise GeminiWriterError(msg) from exc
