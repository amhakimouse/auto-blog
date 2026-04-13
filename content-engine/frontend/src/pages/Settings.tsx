import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardHeader, Button, Input, Spinner, useToast } from '@/components/ui';
import { CreditCard, Save } from 'lucide-react';

export function SettingsPage() {
  const { user, refetchUser } = useAuth();
  const { addToast } = useToast();

  const [fullName, setFullName] = useState(user?.full_name || '');
  const [isSaving, setIsSaving] = useState(false);

  const updateMutation = useMutation({
    mutationFn: async () => {
      return { user: { ...user, full_name: fullName } };
    },
    onSuccess: () => {
      addToast('success', 'Profile updated successfully');
      refetchUser();
    },
    onError: () => {
      addToast('error', 'Failed to update profile');
    },
  });

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    await updateMutation.mutateAsync();
    setIsSaving(false);
  };

  if (!user) {
    return (
      <div className="flex items-center justify-center py-12">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
        <p className="text-gray-600 dark:text-gray-400">Manage your account settings</p>
      </div>

      <Card>
        <CardHeader title="Profile" subtitle="Update your personal information" />
        <form onSubmit={handleSave} className="space-y-4">
          <Input
            label="Full Name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Your full name"
          />
          <Input
            label="Email"
            type="email"
            value={user.email}
            disabled
            helperText="Email cannot be changed"
          />
          <div className="pt-4">
            <Button type="submit" leftIcon={<Save className="w-4 h-4" />} isLoading={isSaving}>
              Save Changes
            </Button>
          </div>
        </form>
      </Card>

      <Card>
        <CardHeader title="Credits" subtitle="Your available credits" />
        <div className="flex items-center gap-4">
          <div className="p-3 rounded-lg bg-purple-50 dark:bg-purple-900/20">
            <CreditCard className="w-6 h-6 text-purple-600" />
          </div>
          <div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white">{user.credits}</p>
            <p className="text-sm text-gray-500 dark:text-gray-400">credits remaining</p>
          </div>
        </div>
        <div className="mt-4 pt-4 border-t border-gray-200 dark:border-dark-border">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Current plan: {user.plan}
          </p>
        </div>
      </Card>

      <Card>
        <CardHeader title="Account Information" />
        <dl className="space-y-3">
          <div className="flex justify-between">
            <dt className="text-gray-500 dark:text-gray-400">Account ID</dt>
            <dd className="text-gray-900 dark:text-white font-mono text-sm">{user.id}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500 dark:text-gray-400">Member since</dt>
            <dd className="text-gray-900 dark:text-white">
              {new Date(user.created_at).toLocaleDateString()}
            </dd>
          </div>
        </dl>
      </Card>
    </div>
  );
}