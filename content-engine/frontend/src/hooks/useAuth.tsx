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
  const [isLoading, setIsLoading] = useState(true);
  const queryClient = useQueryClient();

  const { refetch } = useQuery({
    queryKey: ['user'],
    queryFn: async () => {
      const response = await auth.me();
      return response as User;
    },
    enabled: false,
    retry: false,
  });

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (token) {
      refetch().then(({ data }) => {
        if (data) {
          setUser(data);
        }
        setIsLoading(false);
      }).catch(() => {
        setUser(null);
        setIsLoading(false);
      });
    } else {
      setIsLoading(false);
    }
  }, [refetch]);

  const login = (token: string, userData: User) => {
    localStorage.setItem('token', token);
    setUser(userData);
  };

  const logout = () => {
    localStorage.removeItem('token');
    setUser(null);
    queryClient.clear();
  };

  const refetchUser = () => {
    refetch().then(({ data }) => {
      if (data) {
        setUser(data);
      }
    });
  };

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