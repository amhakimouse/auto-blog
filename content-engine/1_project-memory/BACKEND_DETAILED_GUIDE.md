# Content-Engine Backend - Detailed File Guide

> A beginner-friendly explanation of every file in the `content-engine/backend` folder, organized by purpose and explained with code-role breakdown.

## Table of Contents

1. [Application Entry Points](#1-application-entry-points)
2. [Configuration & Settings](#2-configuration--settings)
3. [Database Layer](#3-database-layer)
4. [Security](#4-security)
5. [ORM Models (Database Tables)](#5-orm-models-database-tables)
6. [API Endpoints](#6-api-endpoints)
7. [Pipelines (Business Logic)](#7-pipelines-business-logic)
8. [AI Services](#8-ai-services)
9. [Web Scrapers](#9-web-scrapers)
10. [WordPress Integration](#10-wordpress-integration)
11. [Worker & Tasks (Async Jobs)](#11-worker--tasks-async-jobs)
12. [Schemas (Data Validation)](#12-schemas-data-validation)

---

## 1. Application Entry Points

### `app/main.py` — The FastAPI Application Factory

**What it does:**  
This file creates and configures the FastAPI application. Think of it as the "front door" of your entire backend service.

**Key responsibilities:**
- Creates the FastAPI app with proper settings
- Configures CORS (Cross-Origin Resource Sharing) — controls which websites can access your API
- Adds rate limiting to prevent abuse
- Sets up the API router that connects all endpoints
- Creates `/health` and `/readiness` endpoints for monitoring (tells external services "I'm alive!")

**Code breakdown:**
```python
# Creates the FastAPI app
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Async content generation engine...",
    lifespan=lifespan,  # Startup/shutdown logic
)

# Routes all API endpoints under /api/v1
app.include_router(api_router, prefix=settings.api_v1_prefix)

# Health check endpoint
@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy"})
```

**Why it matters:**  
If you want to add a new feature like logging, caching, or authentication middleware, you would add it here.

---

## 2. Configuration & Settings

### `app/config.py` — Central Settings (Environment Variables)

**What it does:**  
Loads all configuration from environment variables (or `.env` file) at startup. This is the single source of truth for all settings.

**Key settings:**
- `app_env` — "development" or "production"
- `database_url` — PostgreSQL database connection
- `redis_url` — Redis connection for Celery
- `secret_key` — JWT signing key (security!)
- GCP settings (`gcp_project_id`, `vertex_ai_model`, etc.)
- WordPress credentials
- Rate limiting configuration

**Code structure:**
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", ...)
    
    # App settings
    app_env: Literal["development", "production"] = "development"
    secret_key: str = Field(..., min_length=32)
    debug: bool = False
    
    # Database
    database_url: str  # e.g. postgresql+asyncpg://user:pass@host:5432/db
    
    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"
    
    # Google Cloud
    gcp_project_id: str = ""
    vertex_ai_model: str = "gemini-2.5-flash"
```

**Usage:** All other modules import settings from here:
```python
from app.config import settings
print(settings.database_url)
```

**Important validator:**
```python
@field_validator("database_url")
def must_be_async_driver(cls, v):
    if not v.startswith("postgresql+asyncpg"):
        raise ValueError("DATABASE_URL must use asyncpg driver")
    return v
```

---

### `app/logger.py` — Structured JSON Logging

**What it does:**  
Sets up structured logging using `structlog`. In development, it prints pretty colored logs to the console. In production, it outputs JSON for log aggregators (Datadog, GCP Logging, etc.).

**Usage:**
```python
from app.logger import get_logger
log = get_logger(__name__)
log.info("something_happened", user_id="abc123", action="login")
# Output: {"event": "something_happened", "user_id": "abc123", "action": "login", ...}
```

---

### `app/database.py` — SQLAlchemy Engine & Session

**What it does:**  
Creates the database connection pool and provides a function to get database sessions.

**Key components:**

1. **Engine** — Manages connections to PostgreSQL
```python
engine = create_async_engine(
    settings.database_url,
    pool_size=10,        # Max connections
    max_overflow=20,     # Extra connections when pool is full
)
```

2. **Session Factory** — Creates database sessions
```python
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
)
```

3. **Base** — All ORM models inherit from this
```python
class Base(DeclarativeBase):
    pass
```

4. **Dependency** — FastAPI injects this into route handlers
```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

**Usage in routes:**
```python
@app.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    return result.scalars().all()
```

---

## 3. Security

### `app/security/__init__.py` — Security Utilities

**What it does:**  
Provides two critical security functions:

#### 1. SSRF Protection (Server-Side Request Forgery)
Prevents attackers from making your server fetch internal/private URLs (like `http://localhost`, `http://192.168.x.x`).

```python
def validate_url_for_ssrf(url: str) -> str:
    """
    Validates that URL is safe to fetch:
    1. Must use http or https
    2. Must NOT resolve to a private network IP
    """
    # Resolves hostname to IP
    addr_infos = socket.getaddrinfo(hostname, None)
    # Checks if IP is in blocked ranges
    for network in _BLOCKED_NETWORKS:
        if ip in network:
            raise SSRFViolationError(...)
    return url
```

#### 2. HTML Sanitization (XSS Prevention)
Cleans AI-generated HTML to prevent malicious code injection.

```python
def sanitize_html(raw_html: str) -> str:
    """
    Strips dangerous tags like <script>, <iframe>
    Allows safe tags: h1-h6, p, ul, ol, li, strong, em, a, img, etc.
    """
    clean = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
    )
    return clean
```

**Allowed tags:** `h1`, `h2`, `h3`, `h4`, `h5`, `h6`, `p`, `br`, `hr`, `ul`, `ol`, `li`, `strong`, `em`, `b`, `i`, `u`, `s`, `del`, `ins`, `blockquote`, `pre`, `code`, `a`, `img`, `table`, `thead`, `tbody`, `tr`, `th`, `td`, `div`, `span`, `figure`, `figcaption`.

---

### `app/security/auth.py` — Authentication Utilities

**What it does:**  
Handles password hashing and JWT token creation.

```python
# Password hashing (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

# JWT token creation
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt
```

---

## 4. API Endpoints

### `app/api/router.py` — API Router Aggregator

**What it does:**  
Collects all API endpoints under the `/api/v1` prefix.

```python
api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(articles.router, prefix="/articles", tags=["articles"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
```

---

### `app/api/dependencies.py` — Authentication Dependency

**What it does:**  
Provides `get_current_user` function that FastAPI uses to verify JWT tokens.

```python
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    # Decode JWT token
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    user_id_str = payload.get("sub")
    user_id = uuid.UUID(user_id_str)
    
    # Fetch user from database
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return user
```

---

### `app/api/v1/auth.py` — Authentication Endpoints

**Endpoints:**
- `POST /auth/signup` — Register new user
- `POST /auth/login` — Login and get JWT token
- `GET /auth/me` — Get current user profile

```python
@router.post("/signup")
async def signup(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user with hashed password
    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
    )
    db.add(user)
    await db.commit()
    return {"id": str(user.id), "email": user.email, ...}

@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # Verify password
    user = await db.execute(select(User).where(User.email == form_data.username))
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect credentials")
    
    # Create JWT token
    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}
```

---

### `app/api/v1/jobs.py` — Job Management Endpoints

**What it does:**  
Creates, lists, and manages "Jobs" (content generation tasks).

**Workflows:**
- **Workflow 1** (`keyword_to_article`): User provides a keyword → system generates SEO article
- **Workflow 2** (`reference_to_article`): User provides competitor URL → system rewrites

**Endpoints:**
- `POST /jobs` — Create single job
- `POST /jobs/bulk` — Create up to 50 jobs at once
- `GET /jobs` — List user's jobs
- `GET /jobs/{job_id}` — Get job status
- `DELETE /jobs/{job_id}` — Cancel job

**Key logic:**
```python
@router.post("")
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_active_user)):
    # Check credits
    if user.credits <= 0:
        raise HTTPException(status_code=402, detail="Insufficient credits")
    
    # Validate URL for SSRF (Workflow 2)
    if payload.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE:
        validate_url_for_ssrf(str(payload.reference_url))
    
    # Create Job record
    job = Job(
        user_id=user.id,
        workflow_type=payload.workflow_type.value,
        input_keyword=payload.keyword,
    )
    db.add(job)
    await db.flush()
    
    # Send to Celery worker
    celery_result = celery_app.send_task("app.worker.tasks.run_keyword_to_article", kwargs={"job_id": str(job.id)})
    job.celery_task_id = celery_result.id
    await db.commit()
    
    return job
```

---

### `app/api/v1/articles.py` — Article Management Endpoints

**Endpoints:**
- `GET /articles` — List articles
- `GET /articles/{article_id}` — Get article by ID
- `GET /articles/by-job/{job_id}` — Get article for a job
- `POST /articles/{article_id}/publish` — Publish to WordPress

**Publishing flow:**
```python
@router.post("/{article_id}/publish")
async def publish_article(article_id: uuid.UUID, payload: ArticlePublishRequest, db: AsyncSession = Depends(get_db)):
    # Load article
    article = await db.execute(select(Article).where(Article.id == article_id))
    
    # Sanitize HTML again
    clean_html = bleach.clean(payload.html_content, tags=_ALLOWED_TAGS, ...)
    article.html_content = clean_html
    
    # If scheduled, save for later
    if payload.scheduled_publish_at:
        article.scheduled_publish_at = payload.scheduled_publish_at
        article.article_status = ArticleStatus.SCHEDULED
    else:
        # Publish immediately to WordPress
        publisher = WordPressPublisher(wp_site_url=integration.wp_url, ...)
        result = await publisher.publish(title=article.title, html_content=clean_html)
        article.wp_post_id = result["wp_post_id"]
        article.article_status = ArticleStatus.PUBLISHED
    
    await db.commit()
    return article
```

---

### `app/api/v1/integrations.py` — WordPress Integration CRUD

**What it does:**  
Manages "Brand Profiles" — WordPress sites connected to user accounts.

**Endpoints:**
- `POST /integrations` — Create brand profile
- `GET /integrations` — List all brand profiles
- `GET /integrations/{id}` — Get single profile
- `PUT /integrations/{id}` — Full update
- `PATCH /integrations/{id}` — Partial update
- `DELETE /integrations/{id}` — Delete
- `POST /integrations/{id}/test` — Test connection

**Key feature:** Encrypts WordPress password
```python
integration = Integration(
    site_name=payload.site_name,
    wp_url=payload.wp_url,
    wp_username=payload.wp_username,
)
# Uses setter that encrypts via Fernet AES-128
integration.wp_password = payload.wp_password
```

---

### `app/api/v1/dashboard.py` — User Statistics

**Endpoints:**
- `GET /dashboard/stats` — Get user statistics
- `GET /dashboard/recent-jobs` — Get recent jobs

```python
@router.get("/stats")
async def get_stats(user: User = Depends(get_current_active_user)):
    # Count jobs
    total_jobs = await db.execute(select(func.count(Job.id)).where(Job.user_id == user.id))
    # Count articles
    total_articles = await db.execute(select(func.count(Article.id)).join(Job)...)
    return {
        "total_jobs": total_jobs,
        "published_articles": published_articles,
        "credits": user.credits,
    }
```

---

### `app/api/v1/events.py` — Real-time Job Updates (SSE)

**What it does:**  
Provides Server-Sent Events (SSE) for real-time job status updates without WebSockets.

**Endpoint:**
- `GET /events/jobs/{job_id}` — Stream job status

**How it works:**
```python
async def job_event_stream(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    async def generate():
        while True:
            # Poll database for job status
            job = await db.execute(select(Job).where(Job.id == job_id))
            
            # If status changed, send update
            if job.status != last_status:
                yield f"event: job_update\ndata: {json.dumps(job)}\n\n"
                
                if job.status in ("completed", "failed"):
                    yield f"event: done\ndata: {{}}\n\n"
                    return
            
            await asyncio.sleep(2)  # Poll every 2 seconds
    
    return StreamingResponse(generate(), media_type="text/event-stream")
```

---

## 5. ORM Models (Database Tables)

### `app/models/user.py` — User Model

**Table:** `users`

**Fields:**
- `id` — UUID primary key
- `email` — Unique email
- `hashed_password` — Bcrypt-hashed password
- `full_name` — Optional name
- `is_active` — Account status
- `is_superuser` — Admin flag
- `is_verified` — Email verified
- `plan` — "free", "pro", "enterprise"
- `credits` — Remaining credits

---

### `app/models/job.py` — Job Model

**Table:** `jobs`

**Fields:**
- `id` — UUID primary key
- `user_id` — Foreign key to User
- `integration_id` — Foreign key to Integration (optional)
- `workflow_type` — "keyword_to_article" or "reference_to_article"
- `input_keyword` — Keyword (Workflow 1)
- `input_url` — URL (Workflow 2)
- `status` — "pending" → "processing" → "completed"/"failed"
- `progress_pct` — 0-100 progress
- `error_message` — Error details on failure
- `celery_task_id` — Celery task ID for tracking
- `meta` — JSONB for flexible metadata
- `created_at`, `updated_at`, `completed_at` — Timestamps

**Relationships:**
- `owner` → User (one-to-many)
- `article` → Article (one-to-one)

---

### `app/models/article.py` — Article Model

**Table:** `articles`

**Fields:**
- `id` — UUID primary key
- `job_id` — Foreign key to Job (unique = 1:1)
- `user_id` — Foreign key to User
- `integration_id` — Foreign key to Integration (optional)
- `article_status` — "draft_ready", "scheduled", "published", "publish_failed"
- `title` — Article title
- `slug` — URL-friendly slug (auto-generated)
- `html_content` — Sanitized HTML body
- `plain_text` — Plain text for word count
- `word_count` — Number of words
- `meta_description` — SEO meta description
- `focus_keyword` — Target keyword
- `tags` — Array of tags
- `images` — JSONB array of image metadata
- `featured_image_url` — Featured image URL
- `wp_post_id` — WordPress post ID (after publish)
- `wp_post_url` — WordPress post URL (after publish)
- `scheduled_publish_at` — Scheduled time
- `published_at` — Published time
- `source_urls` — URLs used as sources

---

### `app/models/integration.py` — Integration Model

**Table:** `integrations`

**Fields:**
- `id` — UUID primary key
- `user_id` — Foreign key to User
- `site_name` — Human-readable name
- `wp_url` — WordPress site URL
- `wp_username` — WordPress username
- `wp_password_encrypted` — Encrypted Application Password
- `brand_voice_prompt` — AI instructions for brand tone
- `existing_post_links` — JSONB cache of WP posts for internal linking
- `created_at`, `updated_at` — Timestamps

**Password encryption:**
```python
def wp_password(self) -> str:
    """Decrypt password when accessed"""
    return _get_fernet().decrypt(self.wp_password_encrypted).decode()

@wp_password.setter
def wp_password(self, plaintext: str):
    """Encrypt password when set"""
    self.wp_password_encrypted = _get_fernet().encrypt(plaintext.encode())
```

---

## 6. Schemas (Data Validation)

### `app/schemas/job.py` — Job Request/Response Schemas

**JobCreate:**
```python
class JobCreate(BaseModel):
    workflow_type: WorkflowType
    keyword: str | None = Field(None, min_length=2, max_length=200)
    reference_url: HttpUrl | None
    integration_id: uuid.UUID | None
    content_image_count: int = Field(default=1, ge=0, le=5)
    include_featured_image: bool = Field(default=True)
    related_articles: list[RelatedArticle] = Field(default_factory=list, max_length=3)
    
    @model_validator(mode="after")
    def check_workflow_input(self):
        if self.workflow_type == WorkflowType.KEYWORD_TO_ARTICLE:
            if not self.keyword:
                raise ValueError("keyword required for keyword_to_article")
        elif self.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE:
            if not self.reference_url:
                raise ValueError("reference_url required for reference_to_article")
        return self
```

---

### `app/schemas/article.py` — Article Schemas

**ArticleRead:**
```python
class ArticleRead(BaseModel):
    model_config = {"from_attributes": True}
    
    id: uuid.UUID
    job_id: uuid.UUID
    article_status: str
    title: str
    html_content: str
    word_count: int
    ...
```

**ArticlePublishRequest:**
```python
class ArticlePublishRequest(BaseModel):
    html_content: str = Field(..., min_length=1)
    scheduled_publish_at: datetime | None = Field(default=None)
```

---

## 7. Pipelines (Business Logic)

### `app/pipelines/base.py` — Base Pipeline Class

**What it does:**  
Abstract base class that all pipelines inherit from. Defines the interface.

```python
class BasePipeline(ABC):
    def __init__(self, job_id: str, db: Session, job: Job):
        self.job_id = job_id
        self.db = db
        self.job = job
    
    @abstractmethod
    def run(self) -> dict:
        """Execute the full pipeline. Return metadata dict."""
        ...
```

---

### `app/pipelines/keyword_pipeline.py` — Workflow 1 Pipeline

**What it does:**  
Executes the full "Keyword → Article" workflow.

**Stages:**
1. **Search** (5%) — DuckDuckGo search for competitor URLs
2. **Scrape & Extract** (22%) — Fetch HTML, extract structural outlines
3. **Gap Analysis** (37%) — Gemini analyzes content gaps
4. **Outline Generation** (52%) — Gemini creates article outline
5. **Article Writing** (67%) — Gemini writes section by section
6. **Image Generation** (90%) — Imagen generates featured + content images
7. **Finalize** (92%) — Sanitize HTML, upsert Article

**Key methods:**
```python
def run(self) -> dict:
    self._logger.info("keyword_pipeline.run.start")
    self._set_job_status("searching", progress=5)
    
    # Stage 1: Search
    urls = self._search()  # DuckDuckGo
    self._update_progress(20)
    
    # Stage 2: Scrape
    outline_articles = self._scrape_outline(urls)
    self._update_progress(35)
    
    # Stage 3: Gap Analysis
    gap_analysis = self._analyze_gaps(outline_articles)
    self._update_progress(50)
    
    # Stage 4: Outline
    outline = self._generate_outline(gap_analysis)
    self._update_progress(65)
    
    # Stage 5: Write
    article_payload = self._write_article(outline, gap_analysis, section_image_indices)
    self._update_progress(85)
    
    # Stage 6: Images
    featured_image, content_images = self._generate_images(outline, title, section_image_indices)
    self._update_progress(90)
    
    # Stage 7: Persist
    self._finalize(article_payload, featured_image, content_images, source_urls=urls)
    self._update_progress(92)
    
    return {...}
```

---

### `app/pipelines/reference_pipeline.py` — Workflow 2 Pipeline

**What it does:**  
Executes "URL → Article" workflow (simpler than keyword pipeline).

**Stages:**
1. **Fetch** (10%) — Fetch competitor article
2. **Rewrite** (60%) — Gemini rewrites for SEO
3. **Finalize** (90%) — Sanitize, persist

```python
def run(self) -> dict:
    self._set_job_status("scraping", progress=10)
    
    # Stage 1: Fetch URL
    markdown_content = self._fetch()  # Uses article_scraper
    self._update_progress(30)
    
    # Stage 2: AI Rewrite
    ai_payload = self._rewrite(markdown_content)
    self._update_progress(60)
    
    # Stage 3: Persist
    self._finalize(ai_payload)
    self._update_progress(90)
    
    return {...}
```

---

## 8. AI Services

### `app/ai/gemini_rest_client.py` — Vertex AI REST Client

**What it does:**  
Provides a direct HTTP-based client for Vertex AI Gemini, bypassing the official SDK for better control, performance, and reduced dependency bloat.

**Key features:**
- OAuth2 authentication with service account tokens.
- Supports streaming and non-streaming responses.
- Handles API errors and blocked/empty responses.

---

### `app/ai/gemini_writer.py` — Gemini AI Writer

**What it does:**  
All Gemini/Vertex AI interactions for writing articles. Can use either the official SDK or the custom REST client based on `settings.gemini_use_rest_api`.

1. **rewrite_article_for_seo** — Workflow 2 rewriting
```python
def rewrite_article_for_seo(markdown_content: str) -> dict:
    """Take markdown, return SEO-optimized HTML"""
    model = GenerativeModel(model_name=settings.vertex_ai_model, system_instruction=_SYSTEM_PROMPT)
    response = model.generate_content(contents=[user_message], generation_config=gen_config)
    parsed = _parse_json_response(response.text)
    return {
        "title": parsed["title"],
        "html_content": clean_html,
        "word_count": word_count,
        "slug": slugify(title),
    }
```

2. **analyze_content_gaps** — Workflow 1 gap analysis
```python
def analyze_content_gaps(scraped_articles: list[dict]) -> dict:
    """Analyze competitor outlines, find content gaps"""
    # Returns: focus_keyword, covered_topics, content_gaps, recommended_angle, etc.
```

3. **generate_article_outline** — Create article structure
```python
def generate_article_outline(gap_analysis: dict) -> list[dict]:
    """Generate structured outline from gap analysis"""
    # Returns: [{"_meta": {"title": ..., "meta_description": ...}}, {"heading": ..., "type": ...}]
```

4. **write_article_from_outline** — Section-by-section writing
```python
def write_article_from_outline(outline, gap_analysis, section_image_indices, related_articles) -> dict:
    """Write each section, inject image placeholders"""
    for idx, section in enumerate(sections):
        # Build prompt with internal linking + image instructions
        system_prompt = _build_section_writer_prompt(related_articles, image_requested)
        # Call Gemini
        section_html = model.generate_content(...)
        html_parts.append(section_html)
    
    return {"title": ..., "html_content": ..., "word_count": ...}
```

---

### `app/ai/imagen_client.py` — Image Generation

**What it does:**  
Generates images using Google Imagen (via Vertex AI).

```python
def generate_section_image(prompt: str, aspect_ratio: str = "16:9") -> dict:
    """Generate image, return base64"""
    model = ImageGenerationModel.from_pretrained(settings.imagen_model)
    response = model.generate_images(
        prompt=prompt,
        number_of_images=1,
        aspect_ratio=aspect_ratio,
    )
    return {
        "prompt": clean_prompt,
        "base64_image": base64.b64encode(image_bytes).decode(),
        "mime_type": "image/png",
        "alt_text": alt_text,
    }
```

**Image placeholders:**
- Images are generated AFTER article text (because they cost money and time)
- Pipeline inserts `IMAGE_PLACEHOLDER_{id}` tags in HTML
- After generation, placeholders are replaced with base64 data URIs

---

## 9. Web Scrapers

### `app/scrapers/duckduckgo.py` — DuckDuckGo Search Scraper

**What it does:**  
Searches DuckDuckGo for competitor URLs given a keyword.

```python
class DuckDuckGoScraper:
    def search(self, keyword: str, n: int = 5) -> list[str]:
        # POST to html.duckduckgo.com
        response = client.post(_DDG_ENDPOINT, data={"q": keyword})
        
        # Parse results with BeautifulSoup
        soup = BeautifulSoup(response.text, "lxml")
        for anchor in soup.select("a.result__url"):
            url = self._resolve_ddg_redirect(anchor["href"])
            urls.append(url)
        
        # Filter blocklist (YouTube, Facebook, etc.)
        return [u for u in urls if not blocked(u)]
```

---

### `app/scrapers/article_scraper.py` — Article Content Extractor

**What it does:**  
Fetches URLs and extracts article content using `trafilatura`.

```python
async def extract_article_content(url: str) -> str:
    """Fetch URL, return article as Markdown"""
    # Step 1: Fetch raw HTML
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        raw_html = response.text
    
    # Step 2: Extract with trafilatura
    markdown = trafilatura.extract(
        raw_html,
        url=url,
        output_format="markdown",
        include_tables=True,
    )
    
    if not markdown:
        raise ArticleExtractionError("No content found")
    
    return markdown
```

---

## 10. WordPress Integration

### `app/integrations/wordpress.py` — WordPress Publisher

**What it does:**  
Publishes articles to WordPress via REST API.

```python
class WordPressPublisher:
    async def publish(
        self,
        title: str,
        html_content: str,
        image_bytes: bytes | None = None,
        slug: str | None = None,
        ...
    ) -> dict:
        async with httpx.AsyncClient(base_url=self._base_url, headers={"Authorization": self._auth_header}) as client:
            # Step 1: Upload featured image (if any)
            if image_bytes:
                media_result = await client.post(
                    _MEDIA_ENDPOINT,
                    content=image_bytes,
                    headers={"Content-Type": "image/png"},
                )
                featured_media_id = media_result.json()["id"]
            
            # Step 2: Create post
            post_result = await client.post(
                _POSTS_ENDPOINT,
                json={
                    "title": title,
                    "content": html_content,
                    "status": "publish",
                    "featured_media": featured_media_id,
                },
            )
        
        return {
            "wp_post_id": post_result["id"],
            "wp_post_url": post_result.get("link"),
        }
```

---

## 11. Worker & Tasks (Async Jobs)

### `app/worker/celery_app.py` — Celery Configuration

**What it does:**  
Configures Celery for async task processing.

```python
celery_app = Celery("content_engine")

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    task_acks_late=True,        # ACK after completion (reliability)
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    beat_schedule={
        "publish-scheduled-articles": {
            "task": "app.worker.tasks.publish_scheduled_articles",
            "schedule": 300.0,  # Every 5 minutes
        },
    },
)
```

---

### `app/worker/tasks.py` — Celery Task Definitions

**What it does:**  
Defines async tasks that run in the background.

**Key tasks:**

1. **run_keyword_to_article** — Workflow 1 task
```python
class _KeywordToArticleTask(BaseJobTask):
    def execute(self, job_id: str, db: Session, job: Job) -> dict:
        from app.pipelines.keyword_pipeline import KeywordPipeline
        return KeywordPipeline(job_id=job_id, db=db, job=job).run()

run_keyword_to_article = celery_app.register_task(_KeywordToArticleTask())
```

2. **run_reference_to_article** — Workflow 2 task
```python
@celery_app.task(bind=True, name="app.worker.tasks.process_link_workflow")
def process_link_workflow(self, job_id: str, url: str) -> dict:
    from app.pipelines.reference_pipeline import ReferencePipeline
    return ReferencePipeline(job_id=job_id, db=db, job=job).run()
```

3. **sync_wp_links** — Sync WordPress posts for internal linking
```python
@celery_app.task(name="app.worker.tasks.sync_wp_links")
def sync_wp_links(self, integration_id: str) -> dict:
    """Fetch WordPress posts, cache slugs for internal linking"""
    # GET /wp-json/wp/v2/posts
    # Cache in Integration.existing_post_links
```

4. **publish_scheduled_articles** — Publish due articles
```python
@celery_app.task(name="app.worker.tasks.publish_scheduled_articles")
def publish_scheduled_articles(self) -> dict:
    """Find and publish all scheduled articles"""
    due = db.query(Article).filter(
        Article.article_status == ArticleStatus.SCHEDULED,
        Article.scheduled_publish_at <= now,
    )
    for article in due:
        publisher = WordPressPublisher(...)
        await publisher.publish(...)
```

---

## Summary: Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                         USER REQUEST                                    │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  API ENDPOINTS (FastAPI)                                           │
│  - POST /jobs (create job)                                        │
│  - POST /articles/{id}/publish                                    │
│  - CRUD /integrations                                             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  VALIDATION                                                     │
│  - Schemas validate request data                                      │
│  - Security validates URLs (SSRF)                                    │
│  - Dependencies verify JWT                                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
���  DATABASE (PostgreSQL)                                            │
│  - Create Job record                                             │
│  - Store results (Article)                                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CELERY WORKER (Async)                                           │
│  - run_keyword_to_article                                        │
│  - run_reference_to_article                                      │
└───────┬──────────────────────────────────────────┬───────────────┘
        │                                          │
        ▼                                          ▼
┌─────────────────────┐               ┌─────────────────────────────┐
│ KEYWORD PIPELINE    │               │ REFERENCE PIPELINE          │
│ 1. Search (DDG)    │               │ 1. Fetch URL               │
│ 2. Scrape Outline  │               │ 2. Rewrite (Gemini)        │
│ 3. Gap Analysis    │               │ 3. Persist                │
│ 4. Outline         │               │                           │
│ 5. Write Sections  │               │                           │
│ 6. Generate Images │               │                           │
│ 7. Finalize        │               │                           │
└─────────┬──────────┘               └─────────────┬─────────────┘
          │                                        │
          ▼                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│  WORDPRESS PUBLISHER                                         │
│  - Upload featured image                                    │
│  - Create post                                           │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Reference: Key Dependencies

| Component | Purpose | Key File |
|-----------|---------|----------|
| FastAPI | Web framework | `app/main.py` |
| SQLAlchemy | Database ORM | `app/database.py` |
| Celery | Async tasks | `app/worker/celery_app.py`, `app/worker/tasks.py` |
| Gemini | AI writing | `app/ai/gemini_writer.py` |
| Imagen | Image generation | `app/ai/imagen_client.py` |
| DuckDuckGo | Search | `app/scrapers/duckduckgo.py` |
| Trafilatura | Content extraction | `app/scrapers/article_scraper.py` |
| Bleach | HTML sanitization | `app/security/__init__.py` |
| WordPress | Publishing | `app/integrations/wordpress.py` |

---

## Common Patterns

### 1. Adding a new API endpoint
```python
# In app/api/v1/your_module.py
router = APIRouter()

@router.get("/your-endpoint")
async def your_endpoint(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    # Your logic here
    return {"result": "data"}
```

Then add to `app/api/router.py`:
```python
api_router.include_router(your_module.router, prefix="/your-module", tags=["your-module"])
```

### 2. Adding a new database model
```python
# In app/models/your_model.py
class YourModel(Base):
    __tablename__ = "your_models"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
```

### 3. Creating a Celery task
```python
# In app/worker/tasks.py
@celery_app.task(bind=True, name="app.worker.tasks.your_task")
def your_task(self, param: str):
    # Your logic
    return {"result": param}
```

---

This guide covers every file in the content-engine backend. For more details, see the inline code comments in each file.