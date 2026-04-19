# DONE
# 🚀 Advanced Performance & Scalability Roadmap (Phase 2)

This document specifies high-impact optimizations for the **Content Automation Engine**. These features focus on reducing perceived latency, optimizing token costs, and ensuring multi-user stability.

---

## 🏗️ Feature 1: The "Parallel Asset Pipeline"
Instead of generating images one by one, we generate and upload all requested images (up to 5) simultaneously. This ensures the "Structure HTML" stage has all final WordPress image URLs before it begins.

### Visual Workflow
```mermaid
graph TD
    Start[Unified Analysis Done] -->|Extract json parameters| Split{Parallel Split}
    
    subgraph "Parallel Asset Processing"
        Split --> Img1[Imagen: Image 1 + WP Upload + seo data]
        Split --> Img2[Imagen: Image 2 + WP Upload + seo data]
        Split --> ImgN[Imagen: Image N + WP Upload + seo data]
    end
    
    Img1 --> Join[Collect All WP Links]
    Img2 --> Join
    ImgN --> Join
    
    Join --> HTML[Gemini: Structure HTML with <img> tags]
    HTML --> Publish[WP: Create Post & RankMath]
```

**Agent Note:** Use a Celery `group` to execute the image generation and WordPress uploads in parallel. The pipeline must wait for **all** images to return their WordPress URLs before calling Gemini for the final "Structure HTML" step. This ensures no broken images or placeholders in the final draft.

---

## 🧠 Feature 2: Vertex AI Context Caching
Reduce token costs and latency by caching long system prompts and search results.

### Visual Logic
```mermaid
sequenceDiagram
    participant Worker as Celery Worker
    participant Cache as Vertex Cache (TTL 1h)
    participant Gemini as Gemini 1.5 API
    
    Worker->>Cache: Check for Cache ID (Prompt Prefix)
    alt Cache Hit
        Cache-->>Worker: Return cache_id
        Worker->>Gemini: Call with cache_id + User Prompt
    else Cache Miss
        Worker->>Gemini: Create New Context Cache
        Gemini-->>Cache: Store Prefix
        Gemini-->>Worker: Return Full Response
    end
```

**Agent Note:** Target the "Top 3 Scraped Articles" as the cache prefix. If multiple users generate content on similar topics, the input cost drops significantly.

---

## 🔍 Feature 3: Smart "Delta" Scraping Cache
Prevent redundant network requests and avoid IP bans by caching scraped content in Redis.

### Visual Logic
```mermaid
graph TD
    URL[Input URL] --> Hash[Generate MD5 Hash]
    Hash --> Redis{In Redis?}
    Redis -->|Yes| Return[Return Cached Markdown]
    Redis -->|No| Scrape[Execute Trafilatura/FlareSolverr]
    Scrape --> Save[Save to Redis - TTL 24h]
    Save --> Return
```

**Agent Note:** Use the existing Redis instance. Key format: `scrape:cache:<md5_hash>`.

---

## 🚥 Feature 4: Priority-Based Celery Queues
Ensure "fast" tasks (text) aren't stuck behind "heavy" tasks (images).

### Queue Architecture
```mermaid
graph TD
    API[FastAPI] -->|Task Type| Broker[Redis Broker]
    
    subgraph "Workers"
        Broker -->|queue:fast| W1[Text/Analysis Worker]
        Broker -->|queue:heavy| W2[Image/Video Worker]
        Broker -->|queue:io| W3[WP/API Sync Worker]
    end
```

**Agent Note:** Update `celery_app.py` to define `task_routes`. 
- `run_analysis` -> `fast`
- `generate_images` -> `heavy`
- `sync_to_wordpress` -> `io`

---

## 🎓 Mentor's Final Verdict for the Agent

1.  **Safety First:** When implementing the "Split-Join" (Feature 1), ensure that if the Image Branch fails, the Text Branch can still finish and notify the user with "Text ready, images failed."
2.  **Cost Monitoring:** For Vertex Caching (Feature 2), monitor the cache creation costs—only cache prefixes longer than 2048 tokens to ensure it's cost-effective.
