# 🧠 Project Memory — Content Automation Engine

This folder is a **living knowledge base** for the project.  
Its sole purpose is to let an AI assistant (or a new developer) **load project context
in seconds** — without reading every source file from scratch.

**Update these files whenever you make significant structural changes.**

---

## Files in this folder

| File | Purpose |
|---|---|
| `architecture.md` | High-level system design, data flow, and technology choices |
| `models.md` | Every SQLAlchemy model, column purposes, and relationships |
| `api_endpoints.md` | All API routes, methods, payloads, and auth requirements |
| `pipelines.md` | Workflow 1 & 2 pipeline stages, step-by-step |
| `workers_and_tasks.md` | All Celery tasks, queues, beat schedule |
| `conventions.md` | Code patterns, naming rules, error handling, logging style |
| `agent_skills_index.md` | Catalogue of all skills in `agent_skills/` |
| `known_issues.md` | Active bugs, gotchas, and workarounds |

---

## How to use this as an AI context loader

At the start of any coding session, paste this into your prompt:

```
Read content-engine/memory/*.md before making any changes.
```

Or reference specific files for targeted tasks:
```
Read content-engine/memory/pipelines.md and memory/models.md — then add a new pipeline stage.
```
