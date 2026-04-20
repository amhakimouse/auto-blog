import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { jobs, integrations } from '@/lib/api';
import { Card, Button, Input, Select, Toggle, Badge, Modal, Loading, Progress, useToast } from '@/components/ui';
import { Plus, Search, X, Eye, Loader, Trash2, Settings2, Type, Layout, Image as ImageIcon2, Mic, Globe } from 'lucide-react';
import { format } from 'date-fns';
import { Job, Integration, ContentCustomization } from '@/types';
import clsx from 'clsx';

export function JobsPage() {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const { data: jobsResponse, isLoading } = useQuery({
    queryKey: ['jobs', offset],
    queryFn: () => jobs.list({ limit: 20, offset }),
  });

  const { data: integrationsResponse } = useQuery({
    queryKey: ['integrations'],
    queryFn: async () => {
      const response = await integrations.list();
      return (response as any).data || response;
    },
  });

  const jobsData = Array.isArray(jobsResponse) ? jobsResponse : [];
  const integrationsData = Array.isArray(integrationsResponse) ? integrationsResponse : [];

  const cancelMutation = useMutation({
    mutationFn: jobs.cancel,
    onSuccess: () => {
      addToast('success', 'Job cancelled successfully');
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: () => {
      addToast('error', 'Failed to cancel job');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: jobs.delete,
    onSuccess: () => {
      addToast('success', 'Job deleted successfully');
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: () => {
      addToast('error', 'Failed to delete job');
    },
  });

  const filteredJobs = (Array.isArray(jobsData) ? jobsData : []).filter((job: Job) =>
    (job.input_keyword?.toLowerCase().includes(searchQuery.toLowerCase()) ||
     job.input_url?.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  const getWorkflowLabel = (type: string) => {
    if (type === 'keyword_to_article') return 'Keyword';
    if (type === 'reference_to_article') return 'Link';
    return type;
  };

  const getInputValue = (job: Job) => {
    return job.input_keyword || job.input_url || '-';
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Jobs</h1>
          <p className="text-gray-600 dark:text-gray-400">Manage your content generation jobs</p>
        </div>
        <Button leftIcon={<Plus className="w-4 h-4" />} onClick={() => setIsCreateModalOpen(true)}>
          New Job
        </Button>
      </div>

      <Card padding="sm">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search jobs by keyword or URL..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-transparent border-none focus:outline-none text-gray-900 dark:text-white placeholder-gray-400"
          />
        </div>
      </Card>

      <Card padding="none">
        {isLoading ? (
          <Loading text="Loading jobs..." />
        ) : filteredJobs.length > 0 ? (
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Input</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map((job: Job) => (
                  <tr key={job.id}>
                    <td className="font-mono text-xs">{String(job.id).slice(0, 8)}...</td>
                    <td className="capitalize">{getWorkflowLabel(job.workflow_type)}</td>
                    <td>
                      <Badge variant={job.status}>{job.status}</Badge>
                    </td>
                    <td>
                      <div className="w-24">
                        <Progress value={job.progress_pct} size="sm" />
                      </div>
                    </td>
                    <td className="max-w-[200px] truncate">{getInputValue(job)}</td>
                    <td className="text-gray-500">{format(new Date(job.created_at), 'MMM d, yyyy HH:mm')}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <Link to={`/jobs/${job.id}`}>
                          <Button variant="ghost" size="sm" leftIcon={<Eye className="w-4 h-4" />}>
                            View
                          </Button>
                        </Link>
                        {(job.status === 'pending' || job.status === 'processing') && (
                          <Button
                            variant="ghost"
                            size="sm"
                            leftIcon={cancelMutation.isPending ? <Loader className="w-4 h-4 animate-spin" /> : <X className="w-4 h-4" />}
                            onClick={() => cancelMutation.mutate(String(job.id))}
                            disabled={cancelMutation.isPending}
                          >
                            Cancel
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20"
                          leftIcon={deleteMutation.isPending ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                          onClick={() => {
                            if (window.confirm('Are you sure you want to delete this job?')) {
                              deleteMutation.mutate(String(job.id));
                            }
                          }}
                          disabled={deleteMutation.isPending}
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
            <p className="text-gray-500 dark:text-gray-400">No jobs found</p>
          </div>
        )}
      </Card>

      {jobsResponse && (Array.isArray(jobsResponse) ? jobsResponse.length > 0 : (jobsResponse as any).total > 0) && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset((p) => Math.max(0, p - 20))}
          >
            Previous
          </Button>
          <span className="text-sm text-gray-600 dark:text-gray-400">
            Page {Math.floor(offset / 20) + 1}
          </span>
          <Button
            variant="secondary"
            size="sm"
            disabled={Array.isArray(jobsResponse) ? jobsResponse.length < 20 : true}
            onClick={() => setOffset((p) => p + 20)}
          >
            Next
          </Button>
        </div>
      )}

      <CreateJobModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        integrationsList={integrationsData || []}
      />
    </div>
  );
}

interface CreateJobModalProps {
  isOpen: boolean;
  onClose: () => void;
  integrationsList: Integration[];
}

function CreateJobModal({ isOpen, onClose, integrationsList }: CreateJobModalProps) {
  const [activeTab, setActiveTab] = useState<'keyword' | 'reference'>('keyword');
  const [keyword, setKeyword] = useState('');
  const [referenceUrl, setReferenceUrl] = useState('');
  const [integrationId, setIntegrationId] = useState('');
  const [contentImageCount, setContentImageCount] = useState(3);
  const [includeFeaturedImage, setIncludeFeaturedImage] = useState(true);
  const [relatedArticles, setRelatedArticles] = useState<{ title: string; url: string }[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  // ── Granular Content Controls state ────────────────────────────────────────
  const [articleType, setArticleType] = useState<ContentCustomization['article_type']>('informational');
  const [articleStructure, setArticleStructure] = useState<ContentCustomization['article_structure']>('standard');
  const [imageStyle, setImageStyle] = useState<ContentCustomization['image_style']>('standard');
  const [toneOfVoice, setToneOfVoice] = useState<ContentCustomization['tone_of_voice']>('professional');
  const [language, setLanguage] = useState('english');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const handleAddRelatedArticle = () => {
    if (relatedArticles.length < 3) {
      setRelatedArticles([...relatedArticles, { title: '', url: '' }]);
    }
  };

  const handleRemoveRelatedArticle = (index: number) => {
    setRelatedArticles(relatedArticles.filter((_, i) => i !== index));
  };

  const handleRelatedArticleChange = (index: number, field: 'title' | 'url', value: string) => {
    const updated = [...relatedArticles];
    updated[index][field] = value;
    setRelatedArticles(updated);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      if (activeTab === 'keyword' && !keyword.trim()) {
        setError('Keyword is required');
        setIsLoading(false);
        return;
      }
      if (activeTab === 'reference' && !referenceUrl.trim()) {
        setError('Reference URL is required');
        setIsLoading(false);
        return;
      }

      if (!integrationId) {
        setError('Please select a Brand Profile');
        setIsLoading(false);
        return;
      }

      const payload = {
        workflow_type: activeTab === 'keyword' ? 'keyword_to_article' as const : 'reference_to_article' as const,
        keyword: activeTab === 'keyword' ? keyword : undefined,
        reference_url: activeTab === 'reference' ? referenceUrl : undefined,
        integration_id: integrationId || undefined,
        content_image_count: contentImageCount,
        include_featured_image: includeFeaturedImage,
        related_articles: relatedArticles.filter((a) => a.title && a.url),
        customization: {
          article_type: articleType,
          article_structure: articleStructure,
          image_style: imageStyle,
          tone_of_voice: toneOfVoice,
          language: language,
        },
      };

      await jobs.create(payload);
      addToast('success', 'Job created successfully');
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      onClose();
      setKeyword('');
      setReferenceUrl('');
      setIntegrationId('');
      setContentImageCount(3);
      setIncludeFeaturedImage(true);
      setRelatedArticles([]);
      setArticleType('informational');
      setArticleStructure('standard');
      setImageStyle('standard');
      setToneOfVoice('professional');
      setLanguage('english');
      setShowAdvanced(false);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create job');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Create New Job" size="lg">
      <form onSubmit={handleSubmit} className="space-y-6">
        {error && (
          <div className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        <div className="flex gap-2 border-b border-gray-200 dark:border-dark-border">
          <button
            type="button"
            onClick={() => setActiveTab('keyword')}
            className={clsx(
              'px-4 py-2 font-medium text-sm transition-colors border-b-2 -mb-px',
              activeTab === 'keyword'
                ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
            )}
          >
            Keyword to Article
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('reference')}
            className={clsx(
              'px-4 py-2 font-medium text-sm transition-colors border-b-2 -mb-px',
              activeTab === 'reference'
                ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
            )}
          >
            Link to Article
          </button>
        </div>

        <div className="pt-4 space-y-4">
          {activeTab === 'keyword' ? (
            <>
              <Input
                label="Target Keyword"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="Enter keyword..."
                required
                helperText="The AI will search, analyze, and generate an article based on this keyword"
              />

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
                <label className="label">Content Images</label>
                <div className="flex gap-2">
                  {[0, 1, 2, 3, 4, 5].map((num) => (
                    <button
                      key={num}
                      type="button"
                      onClick={() => setContentImageCount(num)}
                      className={clsx(
                        'w-10 h-10 rounded-lg font-medium text-sm transition-colors',
                        contentImageCount === num
                          ? 'bg-primary-500 text-white'
                          : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
                      )}
                    >
                      {num}
                    </button>
                  ))}
                </div>
              </div>

              <Toggle
                checked={includeFeaturedImage}
                onChange={setIncludeFeaturedImage}
                label="Include Featured Image"
              />

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="label mb-0">Related Articles (optional, max 3)</label>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={handleAddRelatedArticle}
                    disabled={relatedArticles.length >= 3}
                  >
                    + Add
                  </Button>
                </div>
                {relatedArticles.map((article, index) => (
                  <div key={index} className="flex items-center gap-2 mb-2">
                    <Input
                      placeholder="Title"
                      value={article.title}
                      onChange={(e) => handleRelatedArticleChange(index, 'title', e.target.value)}
                    />
                    <Input
                      placeholder="URL"
                      value={article.url}
                      onChange={(e) => handleRelatedArticleChange(index, 'url', e.target.value)}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRemoveRelatedArticle(index)}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <Input
                label="Competitor URL"
                value={referenceUrl}
                onChange={(e) => setReferenceUrl(e.target.value)}
                placeholder="https://competitor.com/article"
                required
                helperText="The AI will scrape this URL, analyze the content, and generate a similar article"
              />

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
                <label className="label">Content Images</label>
                <div className="flex gap-2">
                  {[0, 1, 2, 3, 4, 5].map((num) => (
                    <button
                      key={num}
                      type="button"
                      onClick={() => setContentImageCount(num)}
                      className={clsx(
                        'w-10 h-10 rounded-lg font-medium text-sm transition-colors',
                        contentImageCount === num
                          ? 'bg-primary-500 text-white'
                          : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
                      )}
                    >
                      {num}
                    </button>
                  ))}
                </div>
              </div>

              <Toggle
                checked={includeFeaturedImage}
                onChange={setIncludeFeaturedImage}
                label="Include Featured Image"
              />
            </>
          )}
        </div>

        {/* ── Granular Content Controls ── */}
        <div className="border border-gray-200 dark:border-dark-border rounded-xl overflow-hidden">
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-900/40 hover:bg-gray-100 dark:hover:bg-gray-800/60 transition-colors"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300">
              <Settings2 className="w-4 h-4 text-primary-500" />
              AI Content Controls
              <span className="text-xs font-normal text-gray-400 ml-1">(optional)</span>
            </div>
            <svg
              className={clsx('w-4 h-4 text-gray-400 transition-transform', showAdvanced && 'rotate-180')}
              fill="none" viewBox="0 0 24 24" stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {showAdvanced && (
            <div className="p-4 space-y-4 bg-white dark:bg-gray-800/30">
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Fine-tune how the AI writes and structures your article. All fields default to standard settings.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Article Type */}
                <div>
                  <label className="label flex items-center gap-1.5">
                    <Type className="w-3.5 h-3.5 text-primary-400" />
                    Article Type
                  </label>
                  <select
                    id="article-type-select"
                    value={articleType}
                    onChange={(e) => setArticleType(e.target.value as ContentCustomization['article_type'])}
                    className="input w-full text-sm"
                  >
                    <option value="informational">📖 Informational</option>
                    <option value="educational">🎓 Educational</option>
                    <option value="guide">🗺️ How-To Guide</option>
                    <option value="course_module">📚 Course Module</option>
                    <option value="listicle">📝 Listicle</option>
                  </select>
                </div>

                {/* Tone of Voice */}
                <div>
                  <label className="label flex items-center gap-1.5">
                    <Mic className="w-3.5 h-3.5 text-primary-400" />
                    Tone of Voice
                  </label>
                  <select
                    id="tone-of-voice-select"
                    value={toneOfVoice}
                    onChange={(e) => setToneOfVoice(e.target.value as ContentCustomization['tone_of_voice'])}
                    className="input w-full text-sm"
                  >
                    <option value="professional">💼 Professional</option>
                    <option value="conversational">💬 Conversational</option>
                    <option value="witty">😄 Witty</option>
                    <option value="academic">🔬 Academic</option>
                  </select>
                </div>

                {/* Article Structure */}
                <div>
                  <label className="label flex items-center gap-1.5">
                    <Layout className="w-3.5 h-3.5 text-primary-400" />
                    Article Structure
                  </label>
                  <select
                    id="article-structure-select"
                    value={articleStructure}
                    onChange={(e) => setArticleStructure(e.target.value as ContentCustomization['article_structure'])}
                    className="input w-full text-sm"
                  >
                    <option value="standard">📄 Standard</option>
                    <option value="content_cards">🃏 Content Cards</option>
                    <option value="q_and_a">❓ Q&amp;A Format</option>
                    <option value="step_by_step">👣 Step-by-Step</option>
                  </select>
                </div>

                {/* Image Style */}
                <div>
                  <label className="label flex items-center gap-1.5">
                    <ImageIcon2 className="w-3.5 h-3.5 text-primary-400" />
                    Image Style
                  </label>
                  <select
                    id="image-style-select"
                    value={imageStyle}
                    onChange={(e) => setImageStyle(e.target.value as ContentCustomization['image_style'])}
                    className="input w-full text-sm"
                  >
                    <option value="standard">🖼️ Standard</option>
                    <option value="ultra_realistic">📸 Ultra-Realistic</option>
                    <option value="minimalist">✨ Minimalist</option>
                    <option value="infographic">📊 Infographic</option>
                  </select>
                </div>

                {/* Language */}
                <div>
                  <label className="label flex items-center gap-1.5">
                    <Globe className="w-3.5 h-3.5 text-primary-400" />
                    Output Language
                  </label>
                  <select
                    id="language-select"
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                    className="input w-full text-sm"
                  >
                    <option value="english">🇺🇸 English</option>
                    <option value="french">🇫🇷 French</option>
                    <option value="spanish">🇪🇸 Spanish</option>
                    <option value="german">🇩🇪 German</option>
                    <option value="italian">🇮🇹 Italian</option>
                    <option value="portuguese">🇵🇹 Portuguese</option>
                    <option value="dutch">🇳🇱 Dutch</option>
                    <option value="arabic">🇸🇦 Arabic</option>
                    <option value="turkish">🇹🇷 Turkish</option>
                    <option value="polish">🇵🇱 Polish</option>
                    <option value="russian">🇷🇺 Russian</option>
                    <option value="japanese">🇯🇵 Japanese</option>
                    <option value="chinese">🇨🇳 Chinese</option>
                    <option value="korean">🇰🇷 Korean</option>
                    <option value="hindi">🇮🇳 Hindi</option>
                    <option value="indonesian">🇮🇩 Indonesian</option>
                    <option value="swedish">🇸🇪 Swedish</option>
                    <option value="norwegian">🇳🇴 Norwegian</option>
                    <option value="danish">🇩🇰 Danish</option>
                    <option value="finnish">🇫🇮 Finnish</option>
                  </select>
                </div>
              </div>

              {/* Summary badge row */}
              <div className="flex flex-wrap gap-1.5 pt-1">
                {[
                  { label: articleType.replace('_', ' '), color: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' },
                  { label: toneOfVoice, color: 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300' },
                  { label: articleStructure.replace('_', ' '), color: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' },
                  { label: imageStyle.replace('_', ' '), color: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300' },
                  { label: language, color: 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300' },
                ].map(({ label, color }) => (
                  <span key={label} className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium capitalize ${color}`}>
                    {label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 pt-4 border-t border-gray-200 dark:border-dark-border">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" isLoading={isLoading}>
            Create Job
          </Button>
        </div>
      </form>
    </Modal>
  );
}