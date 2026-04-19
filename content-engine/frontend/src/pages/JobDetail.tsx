import { useParams, Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { jobs } from '@/lib/api';
import { Card, Button, Badge, Loading, Progress, useToast } from '@/components/ui';
import {
  ArrowLeft, ExternalLink, CheckCircle, FileText,
  Globe, AlertTriangle, ImageIcon, Loader2, X, Trash2
} from 'lucide-react';
import { format } from 'date-fns';

// ── Active statuses that require polling ───────────────────────────────────────
const ACTIVE_STATUSES = new Set([
  'pending', 'searching', 'scraping', 'content_extracted',
  'analyzing', 'generating_assets', 'writing', 'publishing', 'processing',
]);

// ── Full pipeline timeline steps ───────────────────────────────────────────────
const TIMELINE_STEPS = [
  { status: 'pending',            label: 'Queued',     description: 'Job added to queue' },
  { status: 'searching',         label: 'Searching',  description: 'Finding competitor articles' },
  { status: 'scraping',          label: 'Scraping',   description: 'Extracting article content' },
  { status: 'content_extracted', label: 'Extracted',  description: 'Content ready for AI' },
  { status: 'analyzing',         label: 'Analyzing',  description: 'Unified Gemini analysis' },
  { status: 'awaiting_preview',  label: 'Preview',    description: 'Awaiting your approval' },
  { status: 'generating_assets', label: 'Images',     description: 'Generating & uploading images' },
  { status: 'writing',           label: 'Writing',    description: 'Structuring final HTML' },
  { status: 'publishing',        label: 'Publishing', description: 'Creating WP draft' },
  { status: 'completed',         label: 'Done',       description: 'Article ready' },
];

const STATUS_TO_STEP_INDEX: Record<string, number> = Object.fromEntries(
  TIMELINE_STEPS.map((s, i) => [s.status, i])
);

function currentStepIndex(status: string): number {
  return STATUS_TO_STEP_INDEX[status] ?? (status === 'failed' ? -1 : 0);
}

// ── Scraped sources table ──────────────────────────────────────────────────────
function ScrapedSourcesPanel({ scrapedUrls }: { scrapedUrls: any[] }) {
  if (!scrapedUrls?.length) return null;
  return (
    <Card title="Scraped Sources" shadow="sm">
      <div className="overflow-hidden border border-gray-200 dark:border-gray-700 rounded-lg">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
          <thead className="bg-gray-50 dark:bg-gray-900/50">
            <tr>
              <th className="px-4 py-2 text-left font-medium text-gray-500 dark:text-gray-400">URL</th>
              <th className="px-4 py-2 text-right font-medium text-gray-500 dark:text-gray-400">Characters</th>
              <th className="px-4 py-2 text-center font-medium text-gray-500 dark:text-gray-400">Status</th>
            </tr>
          </thead>
          <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
            {scrapedUrls.map((item: any, i: number) => (
              <tr key={i}>
                <td className="px-4 py-2 max-w-xs">
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary-600 hover:underline truncate block max-w-xs"
                  >
                    {item.url}
                  </a>
                </td>
                <td className="px-4 py-2 text-right text-gray-600 dark:text-gray-400 tabular-nums">
                  {item.chars ? item.chars.toLocaleString() : '—'}
                </td>
                <td className="px-4 py-2 text-center">
                  {item.status === 'ok' ? (
                    <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400 font-medium">
                      <CheckCircle className="w-3.5 h-3.5" /> Scraped
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
                      <AlertTriangle className="w-3.5 h-3.5" /> Failed
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ── Generated images grid ──────────────────────────────────────────────────────
function GeneratedImagesPanel({ generatedImages }: { generatedImages: any[] }) {
  if (!generatedImages?.length) return null;
  const successful = generatedImages.filter((img: any) => img.status === 'success');
  if (!successful.length) return null;

  return (
    <Card title={`Generated Images (${successful.length})`} shadow="sm">
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {successful.map((img: any, i: number) => {
          const src = img.wp_url || (img.webp_base64 ? `data:image/webp;base64,${img.webp_base64}` : null);
          if (!src) return null;
          return (
            <div key={i} className="relative group rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700 aspect-video bg-gray-100 dark:bg-gray-900">
              <img
                src={src}
                alt={img.metadata?.alt_text || `Generated image ${i + 1}`}
                className="w-full h-full object-cover"
                loading="lazy"
              />
              {img.wp_url && (
                <a
                  href={img.wp_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Globe className="w-5 h-5 text-white" />
                </a>
              )}
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent px-2 py-1">
                <p className="text-white text-xs truncate">
                  {img.metadata?.title || `Image ${img.index + 1}`}
                </p>
                {img.wp_url && (
                  <p className="text-green-300 text-xs">Uploaded to WP ✓</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ── Live step label badge ──────────────────────────────────────────────────────
function LiveStepLabel({ label, status }: { label?: string; status: string }) {
  if (!label || status === 'completed' || status === 'failed') return null;
  return (
    <div className="flex items-center gap-2 mt-3 px-3 py-2 bg-primary-50 dark:bg-primary-900/20 border border-primary-200 dark:border-primary-800 rounded-lg">
      <Loader2 className="w-4 h-4 text-primary-500 animate-spin flex-shrink-0" />
      <span className="text-sm text-primary-700 dark:text-primary-300 font-medium">{label}</span>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────
export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const { data: job, isLoading, error } = useQuery({
    queryKey: ['job', id],
    queryFn: async () => {
      const response = await jobs.get(id!);
      return (response as any).data || response;
    },
    refetchInterval: (query) => {
      const j = query.state.data as any;
      if (j && ACTIVE_STATUSES.has(j.status)) return 2000;
      return false;
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () => jobs.resume(id!),
    onSuccess: () => {
      addToast('success', 'Generation resumed! Building final article...');
      queryClient.invalidateQueries({ queryKey: ['job', id] });
    },
    onError: (err: any) => {
      addToast('error', err.response?.data?.detail || 'Failed to resume job');
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => jobs.cancel(id!),
    onSuccess: () => {
      addToast('success', 'Job cancelled successfully');
      queryClient.invalidateQueries({ queryKey: ['job', id] });
    },
    onError: () => {
      addToast('error', 'Failed to cancel job');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => jobs.delete(id!),
    onSuccess: () => {
      addToast('success', 'Job deleted successfully');
      navigate('/jobs');
    },
    onError: () => {
      addToast('error', 'Failed to delete job');
    },
  });

  if (isLoading) return <Loading text="Loading job details..." />;
  if (error || !job) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500">Failed to load job details</p>
        <Link to="/jobs">
          <Button variant="secondary" className="mt-4">Back to Jobs</Button>
        </Link>
      </div>
    );
  }

  const stepIdx = currentStepIndex(job.status);
  const isFailed = job.status === 'failed';
  const isCompleted = job.status === 'completed';
  const isAwaiting = job.status === 'awaiting_preview';
  const stepLabel: string | undefined = job.meta?.current_step_label;

  return (
    <div className="space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/jobs">
            <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />}>
              Back
            </Button>
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Job Details</h1>
            <p className="text-gray-500 dark:text-gray-400 text-sm font-mono">{job.id}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {(job.status === 'pending' || job.status === 'processing' || job.status === 'awaiting_preview') && (
            <Button
              variant="secondary"
              onClick={() => {
                if (window.confirm('Are you sure you want to cancel this job?')) {
                  cancelMutation.mutate();
                }
              }}
              isLoading={cancelMutation.isPending}
              leftIcon={<X className="w-4 h-4" />}
            >
              Cancel
            </Button>
          )}

          <Button
            variant="secondary"
            className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20"
            onClick={() => {
              if (window.confirm('Are you sure you want to delete this job permanently?')) {
                deleteMutation.mutate();
              }
            }}
            isLoading={deleteMutation.isPending}
            leftIcon={<Trash2 className="w-4 h-4" />}
          >
            Delete
          </Button>

          {isAwaiting && (
            <Button
              onClick={() => resumeMutation.mutate()}
              isLoading={resumeMutation.isPending}
              leftIcon={<CheckCircle className="w-4 h-4" />}
              className="bg-green-600 hover:bg-green-700"
            >
              Approve
            </Button>
          )}
        </div>
      </div>

      {/* ── Status Card ── */}
      <Card shadow="sm">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">
              {(job.workflow_type || '').replace(/_/g, ' ')}
            </h2>
            <p className="text-gray-600 dark:text-gray-400">
              {job.input_keyword || job.input_url || '—'}
            </p>
          </div>
          <Badge variant={job.status}>{job.status.replace(/_/g, ' ')}</Badge>
        </div>

        {/* Progress bar */}
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Progress</span>
            <span className="text-sm text-gray-500 tabular-nums">{job.progress_pct || 0}%</span>
          </div>
          <Progress value={job.progress_pct || 0} />
        </div>

        {/* Live step label */}
        <LiveStepLabel label={stepLabel} status={job.status} />

        {/* Timeline */}
        <div className="relative pt-6 pb-4 overflow-x-auto">
          <div className="flex items-start min-w-[900px] px-2">
            {TIMELINE_STEPS.map((step, index) => {
              const isPast = isCompleted || index < stepIdx;
              const isCurrent = !isCompleted && index === stepIdx;
              const isFuture = !isCompleted && index > stepIdx;

              return (
                <div key={step.status} className="flex flex-col items-center flex-1 relative">
                  {/* Connector line */}
                  {index < TIMELINE_STEPS.length - 1 && (
                    <div
                      className={`absolute top-4 left-1/2 w-full h-0.5 transition-colors duration-500 ${
                        isPast ? 'bg-primary-500' : 'bg-gray-200 dark:bg-gray-700'
                      }`}
                    />
                  )}

                  {/* Dot */}
                  <div
                    className={`relative z-10 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-300 ${
                      isPast
                        ? 'bg-primary-500 text-white shadow-lg shadow-primary-500/20'
                        : isCurrent && isFailed
                        ? 'bg-red-500 text-white'
                        : isCurrent
                        ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-600 ring-4 ring-primary-50 dark:ring-primary-900/10'
                        : 'bg-gray-200 dark:bg-gray-700 text-gray-400'
                    }`}
                  >
                    {isPast ? (
                      <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                    ) : isCurrent && isFailed ? (
                      <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                      </svg>
                    ) : isCurrent && !isFailed ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <span className="text-xs font-medium">{index + 1}</span>
                    )}
                  </div>

                  {/* Label */}
                  <span className={`mt-2 text-xs font-semibold text-center leading-tight ${
                    isCurrent ? 'text-primary-600 dark:text-primary-400' : 'text-gray-500 dark:text-gray-400'
                  }`}>
                    {step.label}
                  </span>
                  {isCurrent && (
                    <span className="mt-0.5 text-xs text-gray-400 dark:text-gray-500 text-center hidden md:block">
                      {step.description}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Error message */}
        {isFailed && job.error_message && (
          <div className="mt-4 p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <h3 className="font-medium text-red-600 dark:text-red-400 mb-1">Error</h3>
            <p className="text-sm text-red-600 dark:text-red-400">{job.error_message}</p>
          </div>
        )}
      </Card>

      {/* ── Awaiting Preview — Analysis Table ── */}
      {job.meta?.unified_analysis && (
        <Card title="AI Analysis Preview" shadow="sm">
          <div className="overflow-hidden border border-gray-200 dark:border-gray-700 rounded-lg">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700 text-sm">
              <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                {[
                  { label: 'Focus Keyword', value: job.meta.unified_analysis.focus_keyword, highlight: true },
                  { label: 'Article Title', value: job.meta.unified_analysis.title },
                  { label: 'SEO Title', value: job.meta.unified_analysis.seo_title },
                  { label: 'Slug', value: job.meta.unified_analysis.slug, mono: true },
                  { label: 'SEO Description', value: job.meta.unified_analysis.seo_description, muted: true },
                ].map(({ label, value, highlight, mono, muted }) => (
                  <tr key={label}>
                    <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 w-1/4 whitespace-nowrap">
                      {label}
                    </td>
                    <td className={`px-4 py-3 ${
                      highlight ? 'font-semibold text-primary-600' :
                      mono ? 'font-mono text-gray-700 dark:text-gray-300' :
                      muted ? 'text-gray-600 dark:text-gray-400' : ''
                    }`}>
                      {mono ? <code>{value}</code> : value}
                    </td>
                  </tr>
                ))}

                {/* Image prompts */}
                {job.meta.unified_analysis.image_prompts?.length > 0 && (
                  <tr>
                    <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 align-top whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        <ImageIcon className="w-3.5 h-3.5" />
                        Image Prompts ({job.meta.unified_analysis.image_prompts.length})
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <ul className="space-y-2">
                        {job.meta.unified_analysis.image_prompts.map((img: any, i: number) => (
                          <li key={i} className="text-sm">
                            <span className="font-medium text-gray-900 dark:text-white">
                              {i + 1}. {img.title}:
                            </span>{' '}
                            <span className="text-gray-600 dark:text-gray-400">{img.prompt}</span>
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {isAwaiting && (
            <div className="mt-6 flex items-center justify-between">
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Review the AI analysis above, then approve to generate images and write the final article.
              </p>
              <Button
                onClick={() => resumeMutation.mutate()}
                isLoading={resumeMutation.isPending}
                leftIcon={<CheckCircle className="w-4 h-4" />}
              >
                Approve &amp; Start Generation
              </Button>
            </div>
          )}
        </Card>
      )}

      {/* ── Scraped Sources ── */}
      <ScrapedSourcesPanel scrapedUrls={job.meta?.scraped_urls} />

      {/* ── Extracted Source Preview ── */}
      {job.meta?.extracted_source && (
        <Card title="Extracted Source Content" shadow="sm">
          <div className="max-h-64 overflow-y-auto p-4 bg-gray-50 dark:bg-gray-900/50 rounded-lg border border-gray-200 dark:border-gray-700">
            <pre className="text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-sans leading-relaxed">
              {job.meta.extracted_source}
            </pre>
          </div>
        </Card>
      )}

      {/* ── Generated Images ── */}
      <GeneratedImagesPanel generatedImages={job.meta?.generated_images} />

      {/* ── Meta ── */}
      <Card shadow="sm">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Job Information</h3>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-gray-500 dark:text-gray-400">Created</dt>
            <dd className="text-gray-900 dark:text-white font-medium">
              {format(new Date(job.created_at), 'MMM d, yyyy HH:mm')}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500 dark:text-gray-400">Workflow</dt>
            <dd className="text-gray-900 dark:text-white font-medium capitalize">
              {job.workflow_type.replace(/_/g, ' ')}
            </dd>
          </div>
          {job.completed_at && (
            <div>
              <dt className="text-gray-500 dark:text-gray-400">Completed</dt>
              <dd className="text-gray-900 dark:text-white font-medium">
                {format(new Date(job.completed_at), 'MMM d, yyyy HH:mm')}
              </dd>
            </div>
          )}
        </dl>
      </Card>

      {/* ── Article Ready Banner ── */}
      {isCompleted && (
        <div className="bg-primary-50 dark:bg-primary-900/20 border border-primary-200 dark:border-primary-800 p-6 rounded-xl flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 rounded-full bg-primary-100 dark:bg-primary-800 flex items-center justify-center text-primary-600 dark:text-primary-400">
              <FileText className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-white">Article is ready!</h3>
              <p className="text-gray-600 dark:text-gray-400">
                Your content has been generated and saved as a draft.
              </p>
            </div>
          </div>
          <div className="flex gap-3">
            {job.article_id ? (
              <Link to={`/articles/${job.article_id}`}>
                <Button rightIcon={<ExternalLink className="w-4 h-4" />}>
                  Review Article
                </Button>
              </Link>
            ) : (
              <Button disabled isLoading>Finding Article...</Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}