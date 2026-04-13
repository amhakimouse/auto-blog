import clsx from 'clsx';

interface BadgeProps {
  variant: 'pending' | 'processing' | 'completed' | 'failed' | 'draft_ready' | 'published' | 'scheduled' | 'publish_failed' | 'searching' | 'scraping' | 'analyzing' | 'outlining' | 'writing';
  children?: React.ReactNode;
}

const variantClasses: Record<string, string> = {
  pending: 'badge-pending',
  processing: 'badge-processing',
  completed: 'badge-completed',
  failed: 'badge-failed',
  draft_ready: 'badge-pending',
  published: 'badge-completed',
  scheduled: 'badge-processing',
  publish_failed: 'badge-failed',
  searching: 'badge-processing',
  scraping: 'badge-processing',
  analyzing: 'badge-processing',
  outlining: 'badge-processing',
  writing: 'badge-processing',
};

const variantLabels: Record<string, string> = {
  pending: 'Pending',
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Failed',
  draft_ready: 'Draft Ready',
  published: 'Published',
  scheduled: 'Scheduled',
  publish_failed: 'Publish Failed',
  searching: 'Searching',
  scraping: 'Scraping',
  analyzing: 'Analyzing',
  outlining: 'Outlining',
  writing: 'Writing',
};

export function Badge({ variant, children }: BadgeProps) {
  return (
    <span className={clsx('badge', variantClasses[variant])}>
      {children || variantLabels[variant] || variant}
    </span>
  );
}