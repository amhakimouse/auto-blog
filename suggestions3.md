# NOT DONE
# 🚀 Auto-Blog SaaS: Advanced Feature Blueprint (Phase 3)

This document serves as the technical specification and architectural blueprint for the Coder LLM to implement the next major features for the Auto-Blog platform. It is broken down into three high-impact tiers.

---

## 🥇 Tier 1: SEO & Publishing Power
These features elevate the platform by fully automating the publishing lifecycle and maximizing SEO value through contextual linking.

### 1. Scheduled Execution (Drip-Feed) [did with claude but not confirmed]
**Goal:** Allow users to queue up to 10 tasks (keywords or reference links) with assigned execution dates. The system waits until the date arrives, then generates and publishes the article.

```mermaid
graph TD
    UI[Frontend: Queue 10 Tasks + Dates] --> API[POST /jobs/batch]
    API --> DB[("Save Jobs with execute_at timestamp & Status='queued'")]
    
    Beat((Celery Beat: Hourly Cron)) --> Check{"Check execute_at <= NOW()"}
    Check -- Yes --> Worker[Celery Worker: Generate Article]
    Worker --> Publish[WP Publisher Task]
    Publish --> Live[Status='published']
    Check -- No --> Wait[Wait for next hour]
```
**Implementation Notes:**
- Add `execute_at` (DateTime) to the `Job` model (rather than `Article` model).
- Add validation to limit batch submissions to a maximum of 10 tasks.
- Setup Celery Beat in `celery_app.py` with an hourly `crontab`.
- Create a new Celery task `process_scheduled_jobs` that queries the DB for `queued` jobs where `execute_at <= NOW()` and dispatches the generation pipeline.

### 2. Smart Internal Linking Engine [did with claude but not confirmed]
**Goal:** The AI should automatically weave links to the user's older WordPress posts into the new article in matched keyword.

```mermaid
sequenceDiagram
    participant Worker as Celery Worker
    participant WP as WordPress REST API
    participant Gemini as Gemini 1.5

    Worker->>WP: GET /wp-json/wp/v2/posts?per_page=50
    WP-->>Worker: Return recent posts (query only: Titles + URLs)
    Worker->>Gemini: Inject Posts into System Prompt (with text content to include in final HTML)
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
    alt New URL found today
        Scraper->>DB: Log URL as processed
        Scraper->>Pipeline: Dispatch Reference_to_Article Job
        Pipeline->>Pipeline: Generate & Publish
    end
```
---

## 🥉 Tier 3: Quality & Customization [did with claude but not confirmed]
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