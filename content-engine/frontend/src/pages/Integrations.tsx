import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { integrations } from '@/lib/api';
import { Card, Button, Input, Modal, Loading, useToast, Badge } from '@/components/ui';
import { Plus, Plug, Trash2, Edit, RefreshCw } from 'lucide-react';
import { format } from 'date-fns';
import { Integration } from '@/types';

export function IntegrationsPage() {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [editingIntegration, setEditingIntegration] = useState<Integration | null>(null);
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const { data: integrationsData, isLoading } = useQuery({
    queryKey: ['integrations'],
    queryFn: integrations.list,
  });

  const deleteMutation = useMutation({
    mutationFn: integrations.delete,
    onSuccess: () => {
      addToast('success', 'Integration deleted');
      queryClient.invalidateQueries({ queryKey: ['integrations'] });
    },
    onError: () => {
      addToast('error', 'Failed to delete integration');
    },
  });

  const testMutation = useMutation({
    mutationFn: integrations.test,
    onSuccess: () => {
      addToast('success', 'Connection successful!');
      queryClient.invalidateQueries({ queryKey: ['integrations'] });
    },
    onError: () => {
      addToast('error', 'Connection failed. Please check your credentials.');
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Integrations</h1>
          <p className="text-gray-600 dark:text-gray-400">Manage your WordPress connections</p>
        </div>
        <Button leftIcon={<Plus className="w-4 h-4" />} onClick={() => setIsCreateModalOpen(true)}>
          Add Integration
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {isLoading ? (
          <Loading text="Loading integrations..." />
        ) : integrationsData && integrationsData.length > 0 ? (
          integrationsData.map((integration: Integration) => (
            <Card key={integration.id}>
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-primary-50 dark:bg-primary-900/20">
                    <Plug className="w-5 h-5 text-primary-600" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-gray-900 dark:text-white">{integration.site_name}</h3>
                    <p className="text-sm text-gray-500 dark:text-gray-400">{integration.wp_url}</p>
                  </div>
                </div>
                <Badge variant={integration.is_connected ? 'completed' : 'failed'}>
                  {integration.is_connected ? 'Connected' : 'Disconnected'}
                </Badge>
              </div>

              <div className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                <p>Username: {integration.username}</p>
                <p>Added: {format(new Date(integration.created_at), 'MMM d, yyyy')}</p>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={testMutation.isPending ? <RefreshCw className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                  onClick={() => testMutation.mutate(String(integration.id))}
                  disabled={testMutation.isPending}
                >
                  Test
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Edit className="w-4 h-4" />}
                  onClick={() => setEditingIntegration(integration)}
                >
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Trash2 className="w-4 h-4" />}
                  onClick={() => {
                    if (confirm('Are you sure you want to delete this integration?')) {
                      deleteMutation.mutate(String(integration.id));
                    }
                  }}
                >
                  Delete
                </Button>
              </div>
            </Card>
          ))
        ) : (
          <div className="col-span-full">
            <Card>
              <div className="text-center py-12">
                <Plug className="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-4" />
                <p className="text-gray-500 dark:text-gray-400 mb-4">No integrations yet</p>
                <Button onClick={() => setIsCreateModalOpen(true)}>
                  Add your first WordPress site
                </Button>
              </div>
            </Card>
          </div>
        )}
      </div>

      <IntegrationModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
      />

      {editingIntegration && (
        <IntegrationModal
          isOpen={true}
          onClose={() => setEditingIntegration(null)}
          integration={editingIntegration}
        />
      )}
    </div>
  );
}

interface IntegrationModalProps {
  isOpen: boolean;
  onClose: () => void;
  integration?: Integration;
}

function IntegrationModal({ isOpen, onClose, integration }: IntegrationModalProps) {
  const [siteName, setSiteName] = useState(integration?.site_name || '');
  const [wpUrl, setWpUrl] = useState(integration?.wp_url || '');
  const [username, setUsername] = useState(integration?.username || '');
  const [wpPassword, setWpPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const isEditing = !!integration;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      if (isEditing) {
        await integrations.update(String(integration.id), {
          site_name: siteName,
          wp_url: wpUrl,
          username,
          wp_password: wpPassword || integration.wp_password || '',
        });
        addToast('success', 'Integration updated');
      } else {
        await integrations.create({
          site_name: siteName,
          wp_url: wpUrl,
          username,
          wp_password: wpPassword,
        });
        addToast('success', 'Integration created');
      }
      queryClient.invalidateQueries({ queryKey: ['integrations'] });
      onClose();
      setSiteName('');
      setWpUrl('');
      setUsername('');
      setWpPassword('');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save integration');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={isEditing ? 'Edit Integration' : 'Add Integration'}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        <Input
          label="Site Name"
          value={siteName}
          onChange={(e) => setSiteName(e.target.value)}
          placeholder="My WordPress Site"
          required
        />

        <Input
          label="WordPress URL"
          value={wpUrl}
          onChange={(e) => setWpUrl(e.target.value)}
          placeholder="https://example.com"
          required
        />

        <Input
          label="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="admin"
          required
        />

        <Input
          label="Application Password"
          type="password"
          value={wpPassword}
          onChange={(e) => setWpPassword(e.target.value)}
          placeholder={isEditing ? 'Leave blank to keep current' : 'Generate from WP Admin'}
          helperText={
            isEditing
              ? 'Leave blank to keep your current password'
              : 'Generate at WordPress > Users > Profile > Application Passwords'
          }
          required={!isEditing}
        />

        <div className="flex justify-end gap-3 pt-4 border-t border-gray-200 dark:border-dark-border">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" isLoading={isLoading}>
            {isEditing ? 'Update' : 'Create'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}