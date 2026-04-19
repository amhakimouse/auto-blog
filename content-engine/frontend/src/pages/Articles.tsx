import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { articles, integrations } from '@/lib/api';
import { Card, Button, Input, Select, Badge, Modal, Loading, useToast } from '@/components/ui';
import { Search, Eye, Edit, Trash2, Send, ExternalLink } from 'lucide-react';
import { format } from 'date-fns';
import { Article } from '@/types';

export function ArticlesPage() {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(1);
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const { data: articlesData, isLoading } = useQuery({
    queryKey: ['articles', page, statusFilter],
    queryFn: () => articles.list({ limit: 20, offset: (page - 1) * 20, status: statusFilter || undefined }),
  });

  const { data: integrationsData } = useQuery({
    queryKey: ['integrations'],
    queryFn: integrations.list,
  });

  const deleteMutation = useMutation({
    mutationFn: articles.delete,
    onSuccess: () => {
      addToast('success', 'Article deleted successfully');
      queryClient.invalidateQueries({ queryKey: ['articles'] });
    },
    onError: () => {
      addToast('error', 'Failed to delete article');
    },
  });

  const articleList = Array.isArray(articlesData) 
    ? articlesData 
    : (Array.isArray(articlesData?.items) ? articlesData.items : []);

  const filteredArticles = articleList.filter((article: Article) =>
    article.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handlePublishClick = (article: Article) => {
    setSelectedArticle(article);
    setIsPublishModalOpen(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Articles</h1>
          <p className="text-gray-600 dark:text-gray-400">Manage your generated articles</p>
        </div>
      </div>

      <Card padding="sm">
        <div className="flex flex-col md:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              placeholder="Search articles..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-transparent border-none focus:outline-none text-gray-900 dark:text-white placeholder-gray-400"
            />
          </div>
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            options={[
              { value: '', label: 'All Status' },
              { value: 'draft_ready', label: 'Draft Ready' },
              { value: 'published', label: 'Published' },
              { value: 'scheduled', label: 'Scheduled' },
              { value: 'publish_failed', label: 'Publish Failed' },
            ]}
            className="w-full md:w-40"
          />
        </div>
      </Card>

      <Card padding="none">
        {isLoading ? (
          <Loading text="Loading articles..." />
        ) : filteredArticles.length > 0 ? (
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Status</th>
                  <th>WordPress</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredArticles.map((article: Article) => (
                  <tr key={article.id}>
                    <td className="font-medium text-gray-900 dark:text-white max-w-[300px] truncate">
                      {article.title}
                    </td>
                    <td>
                      <Badge variant={article.article_status}>{article.article_status.replace('_', ' ')}</Badge>
                    </td>
                    <td>
                      {article.wp_post_url ? (
                        <a
                          href={article.wp_post_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-primary-600 hover:text-primary-700"
                        >
                          <ExternalLink className="w-4 h-4" />
                          View
                        </a>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>
                    <td className="text-gray-500">{format(new Date(article.created_at), 'MMM d, yyyy')}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <Link to={`/articles/${article.id}`}>
                          <Button variant="ghost" size="sm" leftIcon={<Eye className="w-4 h-4" />}>
                            View
                          </Button>
                        </Link>
                        <Link to={`/articles/${article.id}/edit`}>
                          <Button variant="ghost" size="sm" leftIcon={<Edit className="w-4 h-4" />}>
                            Edit
                          </Button>
                        </Link>
                        {article.article_status === 'draft_ready' && (
                          <Button
                            variant="ghost"
                            size="sm"
                            leftIcon={<Send className="w-4 h-4" />}
                            onClick={() => handlePublishClick(article)}
                          >
                            Publish
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          leftIcon={<Trash2 className="w-4 h-4" />}
                          onClick={() => {
                            if (confirm('Are you sure you want to delete this article?')) {
                              deleteMutation.mutate(String(article.id));
                            }
                          }}
                        >
                          Delete
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-center py-12">
            <p className="text-gray-500 dark:text-gray-400">No articles found</p>
          </div>
        )}
      </Card>

      {articlesData && articlesData.total_pages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <span className="text-sm text-gray-600 dark:text-gray-400">
            Page {page} of {articlesData.total_pages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={page === articlesData.total_pages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}

      {isPublishModalOpen && selectedArticle && (
        <PublishModal
          isOpen={isPublishModalOpen}
          onClose={() => {
            setIsPublishModalOpen(false);
            setSelectedArticle(null);
          }}
          article={selectedArticle}
          integrationsList={integrationsData || []}
        />
      )}
    </div>
  );
}

interface PublishModalProps {
  isOpen: boolean;
  onClose: () => void;
  article: Article;
  integrationsList: Integration[];
}

function PublishModal({ isOpen, onClose, article, integrationsList }: PublishModalProps) {
  const [publishType, setPublishType] = useState<'immediate' | 'scheduled'>('immediate');
  const [scheduledAt, setScheduledAt] = useState('');
  const [integrationId, setIntegrationId] = useState(article.integration_id || '');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    if (!integrationId) {
      setError('Please select a Brand Profile (Integration)');
      setIsLoading(false);
      return;
    }

    try {
      const articleData = await articles.get(article.id);
      await articles.publish(
        article.id,
        articleData.html_content,
        publishType === 'scheduled' ? scheduledAt : undefined,
        integrationId
      );
      addToast('success', publishType === 'immediate' ? 'Article published!' : 'Article scheduled!');
      queryClient.invalidateQueries({ queryKey: ['articles'] });
      onClose();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to publish article');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Publish Article">
      <form onSubmit={handleSubmit} className="space-y-6">
        {error && (
          <div className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        <div className="p-4 rounded-lg bg-gray-50 dark:bg-dark-card">
          <p className="font-medium text-gray-900 dark:text-white">{article.title}</p>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {article.word_count} words
          </p>
        </div>

        <Select
          label="Brand Profile"
          value={integrationId}
          onChange={(e) => setIntegrationId(e.target.value)}
          options={[
            { value: '', label: 'Select brand...' },
            ...integrationsList.map((i: Integration) => ({ value: i.id, label: i.site_name })),
          ]}
          required
          helperText="Select which WordPress site this article will be published to"
        />

        <div>
          <label className="label">Publish Type</label>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="publishType"
                value="immediate"
                checked={publishType === 'immediate'}
                onChange={() => setPublishType('immediate')}
                className="text-primary-500"
              />
              <span>Immediate</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="publishType"
                value="scheduled"
                checked={publishType === 'scheduled'}
                onChange={() => setPublishType('scheduled')}
                className="text-primary-500"
              />
              <span>Schedule</span>
            </label>
          </div>
        </div>

        {publishType === 'scheduled' && (
          <Input
            label="Schedule Date & Time"
            type="datetime-local"
            value={scheduledAt}
            onChange={(e) => setScheduledAt(e.target.value)}
            required
          />
        )}

        <div className="flex justify-end gap-3 pt-4 border-t border-gray-200 dark:border-dark-border">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" isLoading={isLoading}>
            {publishType === 'immediate' ? 'Publish Now' : 'Schedule'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}