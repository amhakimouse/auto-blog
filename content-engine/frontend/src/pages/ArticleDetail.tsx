import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { articles } from '@/lib/api';
import { Card, Button, Badge, Loading } from '@/components/ui';
import { ArrowLeft, Edit, ExternalLink, Calendar, Clock } from 'lucide-react';
import { format } from 'date-fns';

export function ArticleDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data: article, isLoading, error } = useQuery({
    queryKey: ['article', id],
    queryFn: () => articles.get(id!),
  });

  if (isLoading) {
    return <Loading text="Loading article..." />;
  }

  if (error || !article) {
    return (
      <div className="text-center py-12">
        <p className="text-red-500">Failed to load article</p>
        <Link to="/articles">
          <Button variant="secondary" className="mt-4">Back to Articles</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/articles">
          <Button variant="ghost" size="sm" leftIcon={<ArrowLeft className="w-4 h-4" />}>
            Back
          </Button>
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">{article.title}</h1>
          <p className="text-gray-500 dark:text-gray-400">Created {format(new Date(article.created_at), 'MMMM d, yyyy')}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={article.status}>{article.status.replace('_', ' ')}</Badge>
        </div>
      </div>

      {/* Article Info */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-blue-50 dark:bg-blue-900/20">
              <Calendar className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Created</p>
              <p className="font-medium text-gray-900 dark:text-white">
                {format(new Date(article.created_at), 'MMM d, yyyy')}
              </p>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-purple-50 dark:bg-purple-900/20">
              <Clock className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Updated</p>
              <p className="font-medium text-gray-900 dark:text-white">
                {format(new Date(article.updated_at), 'MMM d, yyyy')}
              </p>
            </div>
          </div>
        </Card>
        {article.wp_post_url && (
          <Card>
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-green-50 dark:bg-green-900/20">
                <ExternalLink className="w-5 h-5 text-green-600" />
              </div>
              <div>
                <p className="text-sm text-gray-500 dark:text-gray-400">WordPress</p>
                <a
                  href={article.wp_post_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary-600 hover:text-primary-700"
                >
                  View Post
                </a>
              </div>
            </div>
          </Card>
        )}
      </div>

      {/* Content */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Article Content</h2>
          <Link to={`/articles/${article.id}/edit`}>
            <Button leftIcon={<Edit className="w-4 h-4" />}>Edit</Button>
          </Link>
        </div>
        <div
          className="prose prose-lg dark:prose-invert max-w-none"
          dangerouslySetInnerHTML={{ __html: article.html_content }}
        />
      </Card>
    </div>
  );
}