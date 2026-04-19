import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { articles, integrations } from '@/lib/api';
import { Card, Button, Badge, Loading, Modal, Input, useToast } from '@/components/ui';
import { ArrowLeft, Edit, ExternalLink, Calendar, Clock, Send } from 'lucide-react';
import { format } from 'date-fns';
import { Article } from '@/types';

export function ArticleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: article, isLoading, error } = useQuery({
    queryKey: ['article', id],
    queryFn: () => articles.get(id!),
  });

  const { data: integrationsData } = useQuery({
    queryKey: ['integrations'],
    queryFn: integrations.list,
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
          <Badge variant={article.article_status}>{article.article_status.replace('_', ' ')}</Badge>
          {article.article_status === 'draft_ready' && (
            <Button
              size="sm"
              leftIcon={<Send className="w-4 h-4" />}
              onClick={() => setIsPublishModalOpen(true)}
            >
              Publish
            </Button>
          )}
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

      {isPublishModalOpen && (
        <PublishModal
          isOpen={isPublishModalOpen}
          onClose={() => setIsPublishModalOpen(false)}
          article={article}
          onSuccess={() => queryClient.invalidateQueries({ queryKey: ['article', id] })}
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
  onSuccess?: () => void;
  integrationsList: Integration[];
}

function PublishModal({ isOpen, onClose, article, onSuccess, integrationsList }: PublishModalProps) {
  const [publishType, setPublishType] = useState<'immediate' | 'scheduled'>('immediate');
  const [scheduledAt, setScheduledAt] = useState('');
  const [integrationId, setIntegrationId] = useState(article.integration_id || '');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
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
      await articles.publish(
        article.id,
        article.html_content,
        publishType === 'scheduled' ? scheduledAt : undefined,
        integrationId
      );
      addToast('success', publishType === 'immediate' ? 'Article published!' : 'Article scheduled!');
      onSuccess?.();
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