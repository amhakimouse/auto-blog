# 🚀 Auto-Blog SaaS: Advanced Feature Blueprint (Phase 3)

This document serves as the technical specification and architectural blueprint for the Coder LLM to implement the next major features for the Auto-Blog platform. It is broken down into three high-impact tiers.

---

## 🥇 Tier 1: SEO & Publishing Power
These features elevate the platform by fully automating the publishing lifecycle and maximizing SEO value through contextual linking.

### 1. Drip-Feed Scheduling
**Goal:** Allow users to generate a batch of articles today, but publish them slowly over time (e.g., 1 per day).

```mermaid
graph TD
    UI[Frontend: Select 'Schedule Dates'] --> API[POST /jobs]
    API --> DB[("Save Job with publish_at timestamp")]
    DB --> Worker[Celery Worker: Generate Article]
    Worker --> Draft[("Save Article as Status='scheduled'")]
    
    Beat((Celery Beat: Hourly Cron)) --> Check{"Check publish_at <= NOW()"}
    Check -- Yes --> Publish[WP Publisher Task]
    Publish --> Live[Status='published']
    Check -- No --> Wait[Wait for next hour]
```
**Implementation Notes:**
- Add `publish_at` (DateTime) to `Article` model.
- Setup Celery Beat in `celery_app.py` with an hourly `crontab`.
- Create a new Celery task `publish_scheduled_articles` that queries the DB and runs `WordPressPublisher`.

### 2. Smart Internal Linking Engine
**Goal:** The AI should automatically weave links to the user's older WordPress posts into the new article.

```mermaid
sequenceDiagram
    participant Worker as Celery Worker
    participant WP as WordPress REST API
    participant Gemini as Gemini 1.5

    Worker->>WP: GET /wp-json/wp/v2/posts?per_page=50
    WP-->>Worker: Return recent posts (Titles + URLs)
    Worker->>Gemini: Inject Posts into System Prompt
    Note over Gemini: "Weave 2-3 contextual links<br/>to these URLs into the article."
    Gemini-->>Worker: HTML with <a href="..."> internal links
```

---

## 🥈 Tier 2: Workflow Automation
Features designed to make the platform "sticky" by putting content generation on autopilot.

### 3. Auto-Pilot Competitor Monitoring
**Goal:** Automatically rewrite competitor content the moment they publish a new blog post via RSS.

```mermaid
sequenceDiagram
    participant Beat as Celery Beat (Daily)
    participant Scraper as RSS Scraper Task
    participant DB as Auto-Blog DB
    participant Pipeline as Reference Pipeline

    Beat->>Scraper: Check monitored RSS feeds
    Scraper->>Scraper: Parse XML
    alt New URL found not in DB
        Scraper->>DB: Log URL as processed
        Scraper->>Pipeline: Dispatch Reference_to_Article Job
        Pipeline->>Pipeline: Generate & Publish
    end
```

### 4. Webhook / Zapier Integration
**Goal:** Allow external triggers (Airtable, Notion, Make.com) to start jobs.
**Implementation Notes:**
- Create a new endpoint `POST /api/v1/webhooks/trigger`.
- Secure it with a static API key tied to the User's account instead of JWT auth.
- The payload should mirror `JobCreate` schema.

---

## 🥉 Tier 3: Quality & Customization
Granular controls over the AI output to cater to high-end B2B users.

### 5. Granular Content Controls
**Goal:** Let users define the exact shape, structure, and tone of the output.

**The Customization Matrix (All fields fallback to `default`)**
| Field | Default Value | Available Options | Applies To |
| :--- | :--- | :--- | :--- |
| `article_type` | `informational` | `informational`, `educational`, `guide`, `course module`, `listicle` | Gemini (Depth) |
| `article_structure` | `standard` | `standard`, `content_cards`, `q_and_a`, `step_by_step` | Gemini (HTML) |
| `image_style` | `standard` | `standard`, `ultra_realistic`, `minimalist`, `infographic` | Imagen (Prompt) |
| `tone_of_voice` | `professional` | `professional`, `conversational`, `witty`, `academic` | Gemini (Copy) |

```mermaid
sequenceDiagram
    actor User
    participant UI as React Frontend
    participant DB as PostgreSQL
    participant Celery as Celery Worker
    participant Gemini as Gemini 1.5

    User->>UI: Selects: Listicle, Witty, Infographic
    UI->>DB: Save to `job.meta["customization"]`
    
    Celery->>DB: Extract Customization Options
    Celery->>Gemini: Alter System Prompt
    Note over Gemini: "Write a Witty Listicle...<br/>Format as content_cards."
    Gemini-->>Celery: Custom HTML Output
```

### 6. Human-in-the-Loop Editor
**Goal:** A WYSIWYG editor allowing users to tweak AI content before WordPress publication.
**Implementation Notes:**
- Pause pipeline at `AWAITING_PREVIEW` status.
- UI renders `TipTap` or `Quill.js` editor populated with `article.html_content`.
- User clicks "Publish", sending the updated HTML back to the backend.
- Backend resumes pipeline and pushes the final modified HTML to WordPress.
