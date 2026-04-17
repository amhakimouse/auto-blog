import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { jobs } from '@/lib/api';
import { Card, Button, Badge, Loading, Progress, useToast } from '@/components/ui';
import { ArrowLeft, ExternalLink, CheckCircle, RefreshCw } from 'lucide-react';
import { format } from 'date-fns';

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const { data: jobResponse, isLoading, error } = useQuery({
    queryKey: ['job', id],
    queryFn: async () => {
        const response = await jobs.get(id!);
        return (response as any).data || response;
    },
    refetchInterval: (data) => {
        const job = (data as any);
        return (job && (job.status === 'processing' || job.status === 'pending' || job.status === 'scraping' || job.status === 'analyzing')) ? 3000 : false;
    }
  });

  const job = jobResponse;

  const resumeMutation = useMutation({
    mutationFn: () => jobs.resume(id!),
    onSuccess: () => {
      addToast('success', 'Generation resumed! Finalizing article...');
      queryClient.invalidateQueries({ queryKey: ['job', id] });
    },
    onError: (err: any) => {
      addToast('error', err.response?.data?.detail || 'Failed to resume job');
    }
  });

  if (isLoading) {
    return <Loading text="Loading job details..." />;
  }

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

  const timelineSteps = [
    { status: 'pending', label: 'Queued', description: 'Job added to queue' },
    { status: 'scraping', label: 'Scraping', description: 'Fetching competitor data' },
    { status: 'analyzing', label: 'Analyzing', description: 'Unified Gemini analysis' },
    { status: 'awaiting_preview', label: 'Preview', description: 'Awaiting your approval' },
    { status: 'processing', label: 'Generating', description: 'Building final assets & HTML' },
    { status: 'completed', label: 'Completed', description: 'Article ready' },
  ];

  const currentStepIndex = timelineSteps.findIndex((s) => s.status === job.status);
  const isFailed = job.status === 'failed';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
            <Link to="/jobs">
            <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />}>
                Back
            </Button>
            </Link>
            <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Job Details</h1>
            <p className="text-gray-500 dark:text-gray-400">ID: {job.id}</p>
            </div>
        </div>

        {job.status === 'awaiting_preview' && (
            <Button 
                onClick={() => resumeMutation.mutate()} 
                isLoading={resumeMutation.isPending}
                leftIcon={<CheckCircle className="w-4 h-4" />}
                className="bg-green-600 hover:bg-green-700"
            >
                Approve & Complete Generation
            </Button>
        )}
      </div>

      {/* Status Card */}
      <Card shadow="sm">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">
              {job.workflow_type === 'keyword_to_article' ? 'Keyword Based' : 'Reference Based'} Article Generation
            </h2>
            <p className="text-gray-600 dark:text-gray-400">{job.input_keyword || job.input_url}</p>
          </div>
          <Badge variant={job.status}>{job.status.replace(/_/g, ' ')}</Badge>
          </div>

          {/* Progress */}
          <div className="mb-6">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Overall Progress</span>
            <span className="text-sm text-gray-500">{job.progress_pct}%</span>
          </div>
          <Progress value={job.progress_pct} showLabel />
          </div>
        {/* Timeline */}
        <div className="relative pt-4 pb-8 overflow-x-auto">
          <div className="flex items-center min-w-[800px] px-4">
            {timelineSteps.map((step, index) => (
              <div key={step.status} className="flex flex-col items-center flex-1 relative">
                {/* Line */}
                {index < timelineSteps.length - 1 && (
                  <div
                    className={`absolute top-4 left-1/2 w-full h-0.5 ${
                      index < currentStepIndex || (job.status === 'completed')
                        ? 'bg-primary-500'
                        : 'bg-gray-200 dark:bg-gray-700'
                    }`}
                  />
                )}
                {/* Dot */}
                <div
                  className={`relative z-10 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-300 ${
                    index < currentStepIndex || (job.status === 'completed')
                      ? 'bg-primary-500 text-white shadow-lg shadow-primary-500/20'
                      : isFailed && index === currentStepIndex
                      ? 'bg-red-500 text-white'
                      : index === currentStepIndex
                      ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 ring-4 ring-primary-50 dark:ring-primary-900/10'
                      : 'bg-gray-200 dark:bg-gray-700 text-gray-400'
                  }`}
                >
                  {index < currentStepIndex || job.status === 'completed' ? (
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                      <path
                        fillRule="evenodd"
                        d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                        clipRule="evenodd"
                      />
                    </svg>
                  ) : isFailed && index === currentStepIndex ? (
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                      <path
                        fillRule="evenodd"
                        d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                        clipRule="evenodd"
                      />
                    </svg>
                  ) : (
                    <span className="text-xs font-medium">{index + 1}</span>
                  )}
                </div>
                <span className={`mt-2 text-xs font-semibold text-center ${index === currentStepIndex ? 'text-primary-600 dark:text-primary-400' : 'text-gray-500'}`}>
                  {step.label}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Error Message */}
        {job.error_message && (
          <div className="mt-6 p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <h3 className="font-medium text-red-600 dark:text-red-400 mb-1">Error</h3>
            <p className="text-sm text-red-600 dark:text-red-400">{job.error_message}</p>
          </div>
        )}
      </Card>

      {/* Unified Analysis Preview (Awaiting Preview Stage) */}
      {job.meta?.unified_analysis && (
        <Card title="Unified Analysis Preview" shadow="sm">
          <div className="overflow-hidden border border-gray-200 dark:border-gray-700 rounded-lg">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700 text-sm">
                <tr>
                  <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 w-1/4">Focus Keyword</td>
                  <td className="px-4 py-3 font-semibold text-primary-600">{job.meta.unified_analysis.focus_keyword}</td>
                </tr>
                <tr>
                  <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50">Article Title</td>
                  <td className="px-4 py-3">{job.meta.unified_analysis.title}</td>
                </tr>
                <tr>
                  <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50">SEO Title</td>
                  <td className="px-4 py-3">{job.meta.unified_analysis.seo_title}</td>
                </tr>
                <tr>
                  <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50">Slug</td>
                  <td className="px-4 py-3"><code>{job.meta.unified_analysis.slug}</code></td>
                </tr>
                <tr>
                  <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50">SEO Description</td>
                  <td className="px-4 py-3 text-gray-600 dark:text-gray-400">{job.meta.unified_analysis.seo_description}</td>
                </tr>
                <tr>
                    <td className="px-4 py-3 font-medium text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50">Image Prompts</td>
                    <td className="px-4 py-3">
                        <ul className="list-disc list-inside space-y-1">
                            {job.meta.unified_analysis.image_prompts?.map((img: any, i: number) => (
                                <li key={i} className="text-gray-600 dark:text-gray-400">
                                    <span className="font-medium text-gray-900 dark:text-white">{img.title}:</span> {img.prompt}
                                </li>
                            ))}
                        </ul>
                    </td>
                </tr>
              </tbody>
            </table>
          </div>

          {job.status === 'awaiting_preview' && (
              <div className="mt-6 flex justify-end">
                <Button 
                    onClick={() => resumeMutation.mutate()} 
                    isLoading={resumeMutation.isPending}
                    leftIcon={<CheckCircle className="w-4 h-4" />}
                >
                    Approve & Start Final Generation
                </Button>
              </div>
          )}
        </Card>
      )}

      {/* Details */}
      <Card shadow="sm">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Meta Information</h3>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <dt className="text-sm text-gray-500 dark:text-gray-400">Created</dt>
            <dd className="text-gray-900 dark:text-white font-medium">{format(new Date(job.created_at), 'MMM d, yyyy HH:mm')}</dd>
          </div>
          <div>
            <dt className="text-sm text-gray-500 dark:text-gray-400">Workflow</dt>
            <dd className="text-gray-900 dark:text-white capitalize font-medium">{job.workflow_type.replace(/_/g, ' ')}</dd>
          </div>
        </dl>
      </Card>

      {/* View Article */}
      {job.status === 'completed' && (
        <div className="flex justify-end">
          <Link to={`/articles`}>
            <Button rightIcon={<ExternalLink className="w-4 h-4" />} shadow="lg">
              View Final Article
            </Button>
          </Link>
        </div>
      )}
    </div>
  );
}