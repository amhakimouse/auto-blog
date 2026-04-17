import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { jobs } from '@/lib/api';
import { Card, Button, Badge, Loading, Progress } from '@/components/ui';
import { ArrowLeft, ExternalLink } from 'lucide-react';
import { format } from 'date-fns';

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data: job, isLoading, error } = useQuery({
    queryKey: ['job', id],
    queryFn: () => jobs.get(id!),
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
    { status: 'processing', label: 'Processing', description: 'Generating content...' },
    { status: 'completed', label: 'Completed', description: 'Article ready' },
  ];

  const currentStepIndex = timelineSteps.findIndex((s) => s.status === job.status);
  const isFailed = job.status === 'failed';

  return (
    <div className="space-y-6">
      {/* Header */}
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

      {/* Status Card */}
      <Card>
        <div className="flex items-start justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">
              {job.workflow_type === 'keyword_to_article' ? 'Keyword Based' : 'Reference Based'} Article Generation
            </h2>
            <p className="text-gray-600 dark:text-gray-400">{job.input_keyword || job.input_url}</p>
          </div>
          <Badge variant={job.status}>{job.status}</Badge>
          </div>

          {/* Progress */}
          <div className="mb-6">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Progress</span>
            <span className="text-sm text-gray-500">{job.progress_pct}%</span>
          </div>
          <Progress value={job.progress_pct} showLabel />
          </div>
        {/* Timeline */}
        <div className="relative">
          <div className="flex items-center justify-between">
            {timelineSteps.map((step, index) => (
              <div key={step.status} className="flex flex-col items-center flex-1 relative">
                {/* Line */}
                {index < timelineSteps.length - 1 && (
                  <div
                    className={`absolute top-4 left-1/2 w-full h-0.5 ${
                      index < currentStepIndex || isFailed
                        ? 'bg-primary-500'
                        : 'bg-gray-200 dark:bg-gray-700'
                    }`}
                  />
                )}
                {/* Dot */}
                <div
                  className={`relative z-10 w-8 h-8 rounded-full flex items-center justify-center ${
                    index < currentStepIndex || (isFailed && index === currentStepIndex)
                      ? 'bg-primary-500 text-white'
                      : isFailed && index === currentStepIndex
                      ? 'bg-red-500 text-white'
                      : index === currentStepIndex
                      ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-600 dark:text-primary-400 ring-2 ring-primary-500'
                      : 'bg-gray-200 dark:bg-gray-700 text-gray-400'
                  }`}
                >
                  {index < currentStepIndex ? (
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
                <span className="mt-2 text-sm font-medium text-gray-700 dark:text-gray-300">{step.label}</span>
                <span className="text-xs text-gray-500 dark:text-gray-400 mt-1">{step.description}</span>
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

      {/* Details */}
      <Card>
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Details</h3>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <dt className="text-sm text-gray-500 dark:text-gray-400">Created</dt>
            <dd className="text-gray-900 dark:text-white">{format(new Date(job.created_at), 'MMM d, yyyy HH:mm')}</dd>
          </div>
          <div>
            <dt className="text-sm text-gray-500 dark:text-gray-400">Last Updated</dt>
            <dd className="text-gray-900 dark:text-white">{format(new Date(job.updated_at), 'MMM d, yyyy HH:mm')}</dd>
          </div>
          <div>
            <dt className="text-sm text-gray-500 dark:text-gray-400">Workflow</dt>
            <dd className="text-gray-900 dark:text-white capitalize">{job.workflow_type.replace(/_/g, ' ')}</dd>
          </div>
        </dl>

        {job.meta?.seo_title && (
          <div className="mt-6 pt-6 border-t border-gray-200 dark:border-dark-border">
            <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-4">SEO Analysis & Metadata</h4>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-1">
                <span className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">SEO Title</span>
                <p className="text-sm text-gray-900 dark:text-white font-medium">{job.meta.seo_title}</p>
              </div>
              <div className="space-y-1">
                <span className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">Target Keyword</span>
                <p className="text-sm text-gray-900 dark:text-white font-medium">
                  <Badge variant="secondary">{job.meta.target_keyword}</Badge>
                </p>
              </div>
              <div className="md:col-span-2 space-y-1">
                <span className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">SEO Description</span>
                <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{job.meta.seo_description}</p>
              </div>
            </div>
          </div>
        )}

        {job.meta?.extracted_source && (
          <div className="mt-6 pt-6 border-t border-gray-200 dark:border-dark-border">
            <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-2">Extracted Source Content</h4>
            <div className="bg-gray-50 dark:bg-dark-card p-4 rounded-lg max-h-60 overflow-y-auto">
              <pre className="text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap font-sans">
                {job.meta.extracted_source}
              </pre>
            </div>
          </div>
        )}
      </Card>

      {/* View Article */}
      {job.status === 'completed' && (
        <div className="flex justify-end">
          <Link to={`/articles`}>
            <Button rightIcon={<ExternalLink className="w-4 h-4" />}>
              View in Articles List
            </Button>
          </Link>
        </div>
      )}
    </div>
  );
}