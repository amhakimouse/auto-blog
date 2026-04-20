"""
app/ai/prompt_builder.py — Dynamic Prompt Engineering for Gemini.

This module is the single source of truth for constructing the system prompt
that is sent to Gemini for the final HTML article-writing step.

It injects two feature layers:
  1. Smart Internal Linking  — a curated block of WP post URLs for Gemini to
     weave naturally into the prose (strictly optional, not forced).
  2. Granular Content Controls — article_type, article_structure, tone_of_voice,
     and image_style constraints derived from the job's ContentCustomization
     settings.

Design:
  - build_article_system_prompt() is the ONLY public symbol.
  - Each sub-section of the prompt is assembled in its own private helper so
    tests can assert on each section in isolation.
  - Link instructions intentionally forbid Gemini from force-fitting links where
    they don't make contextual sense.
"""

from __future__ import annotations

from app.schemas.job import ContentCustomization

# ── Article type guidance map ──────────────────────────────────────────────────
_ARTICLE_TYPE_GUIDANCE: dict[str, str] = {
    "informational": (
        "Write an in-depth, fact-rich informational article. "
        "Include relevant statistics, expert-backed claims, and detailed explanations "
        "that satisfy the searcher's informational intent."
    ),
    "educational": (
        "Write a comprehensive educational piece designed to teach a concept from the "
        "ground up. Include clear definitions, step-by-step breakdowns, examples, and "
        "beginner-friendly explanations."
    ),
    "guide": (
        "Write a practical, actionable how-to guide. Structure content as a logical "
        "sequence of steps with clear instructions, tips, and warnings. Use numbered "
        "lists for steps and bullet lists for tips."
    ),
    "course_module": (
        "Write a structured course module with a learning objective stated upfront, "
        "key concepts explained progressively, exercises or reflection questions embedded, "
        "and a module summary at the end."
    ),
    "listicle": (
        "Write a well-researched listicle. Each item must have a bold title, 2-4 "
        "explanatory sentences, and optionally a tip or sub-list. Number the items and "
        "keep pacing energetic and scannable."
    ),
}

# ── Article structure HTML guidance map ───────────────────────────────────────
_STRUCTURE_GUIDANCE: dict[str, str] = {
    "standard": (
        "Use a standard SEO article structure: an introductory paragraph, body sections "
        "with H2 headings, supporting paragraphs and bullet lists, and a conclusion. "
        "Output clean semantic HTML."
    ),
    "content_cards": (
        "Structure each major section as a visually distinct card using a "
        "<div class=\"content-card\"> wrapper with an H2 heading, a lead paragraph, "
        "and a bullet list of key takeaways. Separate cards with <hr> tags."
    ),
    "q_and_a": (
        "Format the article body as a Q&A: use H2 tags for questions (phrased as "
        "the reader would ask them) and <p> tags for the answers. Include an FAQ "
        "<section> at the end with 5-7 rapid-fire questions and one-sentence answers."
    ),
    "step_by_step": (
        "Organise the article as a numbered step-by-step guide. Wrap each step in "
        "<div class=\"step\"> containing an H2 numbered heading (e.g. 'Step 1: ...'), "
        "a description paragraph, and optionally a code block or tip callout."
    ),
}

# ── Tone of voice guidance map ────────────────────────────────────────────────
_TONE_GUIDANCE: dict[str, str] = {
    "professional": (
        "Adopt a confident, authoritative, and professional tone. Avoid slang or "
        "casual language. Write as an industry expert speaking to informed peers."
    ),
    "conversational": (
        "Use a warm, conversational tone — write as if explaining to a smart friend. "
        "Use contractions, relatable analogies, and second-person ('you') address. "
        "Keep sentences short and punchy."
    ),
    "witty": (
        "Adopt an engaging, witty tone with light humour and clever wordplay. "
        "Include the occasional pun, cultural reference, or playful aside — but ensure "
        "the content remains genuinely informative and never sacrifices clarity for a joke."
    ),
    "academic": (
        "Write in a formal, scholarly tone. Use third-person where appropriate, cite "
        "concepts by name, avoid contractions, and structure arguments logically with "
        "clear transitions. Use passive voice sparingly."
    ),
}

# ── Image style guidance map (used to modify Imagen prompts, not the HTML) ────
_IMAGE_STYLE_LABELS: dict[str, str] = {
    "standard":        "high-quality photorealistic",
    "ultra_realistic": "ultra-photorealistic, studio-quality, 8K resolution",
    "minimalist":      "clean minimalist flat design, white background, pastel tones",
    "infographic":     "clean infographic style, data-rich, bold typography, vector illustration",
}


# ── Public: Main Prompt Builder ───────────────────────────────────────────────

def build_article_system_prompt(
    *,
    link_candidates: list[dict] | None = None,
    customization: ContentCustomization | None = None,
    total_images: int = 0,
    image_placeholder_instructions: str = "",
    language: str = "english",
) -> str:
    """
    Assemble the full Gemini system prompt for the final HTML article-writing step.

    Args:
        link_candidates:              List of {"title": str, "url": str} dicts from
                                      the Smart Internal Linking Engine. Pass [] or None
                                      to skip internal linking instructions entirely.
        customization:                ContentCustomization Pydantic model with article_type,
                                      article_structure, tone_of_voice, image_style fields.
                                      Pass None to use all defaults.
        total_images:                 Number of images to embed (0 = no images).
        image_placeholder_instructions: Pre-rendered instruction string for image
                                        placeholders (e.g. built by the pipeline helper).
        language:                     Target output language (e.g. 'french', 'arabic').
                                      Injected as a mandatory top-level directive.

    Returns:
        A complete system prompt string ready to send to Gemini.
    """
    if customization is None:
        customization = ContentCustomization()

    # Sanitise language — same defensive normalisation as gemini_writer.py
    language = (language or "english").strip().lower()[:40] or "english"

    sections: list[str] = [
        # Language directive MUST be first so the model commits to it immediately
        _language_section(language),
        _base_role_section(),
        _tone_section(customization.tone_of_voice),
        _article_type_section(customization.article_type),
        _structure_section(customization.article_structure),
    ]

    if link_candidates:
        sections.append(_internal_linking_section(link_candidates))

    if total_images > 0 and image_placeholder_instructions:
        sections.append(image_placeholder_instructions)

    sections.append(_output_rules_section())

    return "\n\n".join(s.strip() for s in sections if s.strip())


# ── Private section builders ──────────────────────────────────────────────────

def _language_section(language: str) -> str:
    """
    Top-priority directive that commits the model to the target language before
    it reads any other instruction. Repeated twice (header + reminder) to combat
    instruction-following drift in long system prompts.
    """
    lang_upper = language.upper()
    return (
        f"CRITICAL LANGUAGE REQUIREMENT:\n"
        f"The ENTIRE article — every sentence, heading, bullet point, meta description, "
        f"SEO title, focus keyword, and alt text — MUST be written exclusively in "
        f"{lang_upper}. Do NOT mix languages. Do NOT include any text in any other "
        f"language. If the source content is in a different language, translate everything "
        f"into {lang_upper} as part of the writing process."
    )


def _base_role_section() -> str:
    return (
        "You are an Expert SEO Copywriter and Content Strategist.\n"
        "Write a full, publication-ready SEO article as clean HTML "
        "(body content only — no <html>, <head>, or <body> tags).\n"
        "Use semantic HTML5 elements: H1 for the page title (once), H2 for major "
        "sections, H3 for sub-sections. Include bullet lists and numbered lists "
        "where they improve readability."
    )


def _tone_section(tone: str) -> str:
    guidance = _TONE_GUIDANCE.get(tone, _TONE_GUIDANCE["professional"])
    return f"TONE OF VOICE:\n{guidance}"


def _article_type_section(article_type: str) -> str:
    guidance = _ARTICLE_TYPE_GUIDANCE.get(
        article_type, _ARTICLE_TYPE_GUIDANCE["informational"]
    )
    return f"ARTICLE TYPE — {article_type.upper().replace('_', ' ')}:\n{guidance}"


def _structure_section(structure: str) -> str:
    guidance = _STRUCTURE_GUIDANCE.get(structure, _STRUCTURE_GUIDANCE["standard"])
    return f"HTML STRUCTURE — {structure.upper().replace('_', ' ')}:\n{guidance}"


def _internal_linking_section(candidates: list[dict]) -> str:
    """
    Build the internal linking instruction block.

    Strict rules injected to prevent Gemini from forcing links awkwardly:
      - Weave 2-3 links only where they are genuinely contextually relevant.
      - Never fabricate or alter URLs.
      - Never include a link if the context doesn't naturally support it.
      - Render links as <a href="URL" rel="noopener">Anchor Text</a>.
    """
    # Deduplicate by URL and cap at 15 candidates for prompt economy
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for c in candidates:
        url = c.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(c)
        if len(deduped) >= 15:
            break

    link_block = "\n".join(
        f'  - "{c["title"]}": {c["url"]}' for c in deduped
    )

    return (
        "SMART INTERNAL LINKING:\n"
        "The following are existing published posts on this WordPress site. "
        "Weave 2-3 of these links naturally into the article body ONLY where "
        "they are genuinely contextually relevant and improve the reader's experience.\n\n"
        "STRICT RULES — you MUST obey all of these:\n"
        "  1. NEVER force a link if it does not flow naturally in the sentence.\n"
        "  2. NEVER modify, shorten, or paraphrase the provided URLs.\n"
        "  3. NEVER invent or hallucinate URLs not in this list.\n"
        "  4. Use descriptive anchor text (NOT 'click here' or the raw URL).\n"
        "  5. Render as: <a href=\"URL\" rel=\"noopener\">Anchor Text</a>\n"
        "  6. If none of the posts are relevant to this article, include ZERO links.\n\n"
        f"Available posts:\n{link_block}"
    )


def _output_rules_section() -> str:
    return (
        "OUTPUT RULES:\n"
        "  - Return raw HTML only — do NOT wrap in markdown code fences (```html).\n"
        "  - Do NOT include <html>, <head>, or <body> wrapper tags.\n"
        "  - Ensure all HTML tags are properly closed and nested.\n"
        "  - The article must be substantive and well-developed — aim for 800+ words.\n"
        "  - Avoid placeholder text, lorem ipsum, or unfinished sentences."
    )


# ── Public: Image-style prompt enhancer ──────────────────────────────────────

def enhance_imagen_prompt(base_prompt: str, image_style: str) -> str:
    """
    Append the image_style modifier to an Imagen generation prompt.

    Args:
        base_prompt:  The original prompt generated by Gemini unified analysis.
        image_style:  One of the ContentCustomization.image_style literal values.

    Returns:
        An enhanced prompt string with the style modifier appended.
    """
    style_label = _IMAGE_STYLE_LABELS.get(image_style, _IMAGE_STYLE_LABELS["standard"])
    if style_label.lower() in base_prompt.lower():
        # Style already referenced in original prompt — don't double-add
        return base_prompt
    return f"{base_prompt.rstrip('.')}. Style: {style_label}."
