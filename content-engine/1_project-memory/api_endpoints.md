# API Endpoints

**Base prefix:** `/api/v1`  
**Auth:** All endpoints except `/auth/*` require `Authorization: Bearer <JWT>`.  
**Rate limiting:** `POST /jobs` and `POST /jobs/bulk` — 10 requests/minute per user.

---

## Auth  `/auth`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/register` | ❌ | Create user account |
| POST | `/auth/login` | ❌ | Get JWT access token |
| GET | `/auth/me` | ✅ | Get current user profile |

**Register payload:**
```json
{ "email": "user@example.com", "password": "secret" }
```
**Login response:**
```json
{ "access_token": "...", "token_type": "bearer" }
```

---

## Jobs  `/jobs`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/jobs` | ✅ | Create a single job (returns 202) |
| POST | `/jobs/bulk` | ✅ | Create up to 50 jobs at once |
| GET | `/jobs` | ✅ | List jobs (paginated: `?limit=20&offset=0`) |
| GET | `/jobs/{job_id}` | ✅ | Poll job status |
| DELETE | `/jobs/{job_id}` | ✅ | Cancel a pending/processing job |

**Create job payload:**
```json
{
  "workflow_type": "keyword_to_article",
  "keyword": "best running shoes 2024",
  "integration_id": "uuid-optional",
  "content_image_count": 2,
  "include_featured_image": true,
  "related_articles": [
    {"title": "Best Trail Shoes", "url": "https://..."}
  ]
}
```
**Workflow 2** replaces `keyword` with `reference_url`.

**Credits check:** `POST /jobs` returns 402 if `user.credits <= 0`.  
**SSRF check:** `reference_url` is validated; private IPs / localhost are rejected (422).

**Job status fields returned:**
```json
{
  "id": "uuid",
  "status": "searching|scraping|analyzing|outlining|writing|completed|failed",
  "progress_pct": 0-100,
  "workflow_type": "keyword_to_article",
  "error_message": null,
  "meta": {}
}
```

---

## Articles  `/articles`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/articles` | ✅ | List articles (paginated, filter by status) |
| GET | `/articles/{id}` | ✅ | Get single article + HTML content |
| PATCH | `/articles/{id}` | ✅ | Edit title, html_content, meta_description |
| POST | `/articles/{id}/publish` | ✅ | Push/schedule to WordPress |
| DELETE | `/articles/{id}` | ✅ | Delete article (and parent job) |

**Publish payload:**
```json
{
  "integration_id": "uuid",
  "scheduled_publish_at": "2024-06-01T09:00:00Z"  // null = publish now
}
```

---

## Integrations  `/integrations`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/integrations` | ✅ | Create new WP site profile |
| GET | `/integrations` | ✅ | List all integrations for user |
| GET | `/integrations/{id}` | ✅ | Get single integration |
| PATCH | `/integrations/{id}` | ✅ | Update WP credentials |
| DELETE | `/integrations/{id}` | ✅ | Delete integration |
| POST | `/integrations/{id}/sync` | ✅ | Trigger immediate `sync_wp_links` task |

**WP password** is Fernet-encrypted at rest. Never returned in API responses.

---

## Events (SSE)  `/events`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/events/jobs/{job_id}` | ✅ | Server-Sent Events stream for job progress |

Client receives `data: {"status": "...", "progress_pct": 45}` until `completed`/`failed`.

---

## Error Format

All errors follow FastAPI default:
```json
{ "detail": "Descriptive error message" }
```

Common codes: `400`, `401`, `402` (credits), `403`, `404`, `409` (conflict), `422` (validation/SSRF).
