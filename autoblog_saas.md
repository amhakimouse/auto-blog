# 🏗️ System Architecture: Content Automation Engine

## High-Level Flow

```
[ React Frontend ] ↔️ (REST API) ↔️ [ FastAPI Backend ]
      (UI/UX)                            |
                                         | (Queues Task)
                                         ↓
                                  [ Redis Broker ]
                                         |
                                         ↓
[ PostgreSQL DB ] ↔️ (State/Logs) ↔️ [ Celery Worker ]
 (Users, Jobs, Articles)                 |
                                         |→ [ Trafilatura ] (Scrapes Content)
                                         |→ [ Google Cloud: Gemini ] (Analyzes/Writes)
                                         |→ [ Google Cloud: Imagen ] (Generates Art)
                                         |→ [ WordPress API ] (Publishes)
```

## 🗄️ Database Schema (PostgreSQL)

| Table    | Columns                                           | Purpose                                        |
|----------|---------------------------------------------------|------------------------------------------------|
| `users`    | `id` (UUID), `email` (Str), `credits` (Int)       | Manages authentication and API billing limits. |
| `jobs`     | `id` (UUID), user_id (FK), workflow_type (Str), input_data (Str), status (Enum), error_log (Text) | Tracks background worker status (queued, scraping, failed, completed). |
| `articles` | `id` (UUID), `job_id` (FK), `title` (Str), `content_markdown` (Text), `content_html` (Text) | Stores the finalized AI output before/after WordPress formatting. |

## 🚀 Core Workflows

### Workflow 1: Keyword → Article
1. User inputs keyword → FastAPI creates job in DB.
2. Worker scrapes DuckDuckGo HTML for top 5 URLs.
3. Worker extracts text via Trafilatura.
4. Gemini analyzes content, generates outline, writes sections.
5. Imagen generates contextual images.
6. Worker builds HTML, saves to DB, publishes to WordPress.

### Workflow 2: Reference Link → Article
1. User inputs URL → FastAPI creates job in DB.
2. Worker safely extracts clean markdown via Trafilatura.
3. Gemini rewrites, improves SEO, and structures output.
4. Worker builds final HTML and saves/publishes.