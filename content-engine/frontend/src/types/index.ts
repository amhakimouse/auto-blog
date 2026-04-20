export interface User {
  id: string;
  email: string;
  full_name: string | null;
  plan: string;
  credits: number;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Job {
  id: string;
  workflow_type: 'keyword_to_article' | 'reference_to_article';
  status: 'pending' | 'processing' | 'searching' | 'scraping' | 'analyzing' | 'outlining' | 'writing' | 'completed' | 'failed';
  progress_pct: number;
  integration_id: string | null;
  input_keyword: string | null;
  input_url: string | null;
  celery_task_id: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface JobDetail extends Job {
  timeline: TimelineEvent[];
}

export interface TimelineEvent {
  status: string;
  message: string;
  timestamp: string;
}

export interface Article {
  id: string;
  job_id: string;
  integration_id: string | null;
  article_status: 'draft_ready' | 'published' | 'scheduled' | 'publish_failed';
  title: string;
  slug: string | null;
  html_content: string;
  word_count: number;
  meta_description: string | null;
  focus_keyword: string | null;
  tags: string[];
  images: unknown[];
  featured_image_url: string | null;
  wp_post_id: number | null;
  wp_post_url: string | null;
  scheduled_publish_at: string | null;
  published_at: string | null;
  source_urls: string[];
  created_at: string;
  updated_at: string;
}

export interface ArticleDetail extends Article {
  job: Job;
}

export interface Integration {
  id: string;
  site_name: string;
  wp_url: string;
  wp_username: string;
  wp_password?: string;
  is_connected: boolean;
  created_at: string;
  updated_at: string;
}

export interface ContentCustomization {
  article_type: 'informational' | 'educational' | 'guide' | 'course_module' | 'listicle';
  article_structure: 'standard' | 'content_cards' | 'q_and_a' | 'step_by_step';
  image_style: 'standard' | 'ultra_realistic' | 'minimalist' | 'infographic';
  tone_of_voice: 'professional' | 'conversational' | 'witty' | 'academic';
  language: string;
}

export interface CreateJobRequest {
  workflow_type: 'keyword_to_article' | 'reference_to_article';
  keyword?: string;
  reference_url?: string;
  integration_id?: string;
  content_image_count?: number;
  include_featured_image?: boolean;
  related_articles?: RelatedArticle[];
  customization?: ContentCustomization;
}

export interface RelatedArticle {
  title: string;
  url: string;
}

export interface CreateIntegrationRequest {
  site_name: string;
  wp_url: string;
  wp_username: string;
  wp_password: string;
}

export interface ScheduledJobItem {
  workflow_type: 'keyword_to_article' | 'reference_to_article';
  keyword?: string;
  reference_url?: string;
  execute_at: string;
}

export interface BatchScheduleCreate {
  integration_id: string;
  items: ScheduledJobItem[];
  content_image_count?: number;
  include_featured_image?: boolean;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface DashboardStats {
  total_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  total_articles: number;
  published_articles: number;
  credits: number;
}