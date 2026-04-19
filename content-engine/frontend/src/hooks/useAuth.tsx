import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { auth } from '@/lib/api';
import { User } from '@/types';

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
  refetchUser: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const queryClient = useQueryClient();
  const token = localStorage.getItem('token');

  // Use the query to fetch the user if a token exists
  const { data, isLoading: queryLoading, isError, isSuccess, refetch } = useQuery({
    queryKey: ['user'],
    queryFn: () => auth.me(),
    enabled: !!token,
    retry: false,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });

  // Sync the query result with our local user state
  useEffect(() => {
    if (token) {
      if (isSuccess && data) {
        setUser(data as User);
      } else if (isError) {
        setUser(null);
        localStorage.removeItem('token'); // Clear invalid token
      }
    } else {
      setUser(null);
    }
  }, [data, isSuccess, isError, token]);

  const login = (token: string, userData: User) => {
    localStorage.setItem('token', token);
    setUser(userData);
    queryClient.setQueryData(['user'], userData);
  };

  const logout = () => {
    localStorage.removeItem('token');
    setUser(null);
    queryClient.clear();
  };

  const refetchUser = () => {
    refetch();
  };

  // We are "loading" only if we have a token but the query hasn't finished yet
  const isLoading = !!token && queryLoading && !data && !isError;

  return (
    <AuthContext.Provider value={{ user, isLoading, isAuthenticated: !!user, login, logout, refetchUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
