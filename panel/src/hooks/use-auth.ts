import { useQuery, useMutation } from "@tanstack/react-query";
import { authApi } from "@/lib/api/auth";

export const authKeys = {
  status: ["auth", "status"] as const,
};

export function useAuthStatus() {
  return useQuery({
    queryKey: authKeys.status,
    queryFn: () => authApi.status(),
    staleTime: 60000,
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      authApi.login(email, password),
  });
}

export function useLogout() {
  return useMutation({
    mutationFn: () => authApi.logout(),
  });
}
