import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { dashboard } from '@/lib/api';
import { Card, CardHeader, Button, Badge, Loading, Progress } from '@/components/ui';
import { Briefcase, FileText, CreditCard, Plus, ArrowRight, CheckCircle } from 'lucide-react';
import { format } from 'date-fns';
import { Job } from '@/types';

export function DashboardPage() {
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['dashboard', 'stats'],
    queryFn: dashboard.stats,
  });

  const { data: recentJobs, isLoading: jobsLoading } = useQuery({
    queryKey: ['dashboard', 'recent-jobs'],
    queryFn: () => dashboard.recentJobs(10),
  });

  if (statsLoading || jobsLoading) {
    return <Loading text="Loading dashboard..." />;
  }

  const getWorkflowLabel = (type: string) => {
    if (type === 'keyword_to_article') return 'Keyword';
    if (type === 'reference_to_article') return 'Link';
    return type;
  };

  const getInputValue = (job: Job) => {
    return job.input_keyword || job.input_url || '-';
  };

  const statCards = [
    {
      label: 'Total Jobs',
      value: stats?.total_jobs || 0,
      icon: Briefcase,
      color: 'bg-blue-50 dark:bg-blue-900/20 text-blue-600',
    },
    {
      label: 'Completed',
      value: stats?.completed_jobs || 0,
      icon: CheckCircle,
      color: 'bg-green-50 dark:bg-green-900/20 text-green-600',
    },
    {
      label: 'Credits',
      value: stats?.credits || 0,
      icon: CreditCard,
      color: 'bg-purple-50 dark:bg-purple-900/20 text-purple-600',
    },
    {
      label: 'Articles',
      value: stats?.total_articles || 0,
      icon: FileText,
      color: 'bg-amber-50 dark:bg-amber-900/20 text-amber-600',
    },
    {
      label: 'Published',
      value: stats?.published_articles || 0,
      icon: ArrowRight,
      color: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600',
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Dashboard</h1>
          <p className="text-gray-600 dark:text-gray-400">Overview of your content generation</p>
        </div>
        <Link to="/jobs">
          <Button leftIcon={<Plus className="w-4 h-4" />}>New Job</Button>
        </Link>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        {statCards.map((stat) => (
          <Card key={stat.label}>
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${stat.color}`}>
                <stat.icon className="w-5 h-5" />
              </div>
              <div>
                <p className="text-sm text-gray-600 dark:text-gray-400">{stat.label}</p>
                <p className="text-xl font-bold text-gray-900 dark:text-white">{stat.value}</p>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader
          title="Recent Jobs"
          subtitle="Your latest content generation jobs"
          action={
            <Link to="/jobs">
              <Button variant="ghost" size="sm" rightIcon={<ArrowRight className="w-4 h-4" />}>
                View All
              </Button>
            </Link>
          }
        />

        {recentJobs && recentJobs.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Input</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {(Array.isArray(recentJobs) ? recentJobs : []).map((job: Job) => (
                  <tr key={job.id}>
                    <td className="capitalize">{getWorkflowLabel(job.workflow_type)}</td>
                    <td>
                      <Badge variant={job.status}>{job.status}</Badge>
                    </td>
                    <td>
                      <div className="w-20">
                        <Progress value={job.progress_pct} size="sm" />
                      </div>
                    </td>
                    <td className="max-w-[200px] truncate">{getInputValue(job)}</td>
                    <td className="text-gray-500">{format(new Date(job.created_at), 'MMM d, yyyy HH:mm')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-12">
            <Briefcase className="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-4" />
            <p className="text-gray-500 dark:text-gray-400">No jobs yet</p>
            <Link to="/jobs" className="mt-4 inline-block">
              <Button variant="secondary" size="sm">Create your first job</Button>
            </Link>
          </div>
        )}
      </Card>
    </div>
  );
}