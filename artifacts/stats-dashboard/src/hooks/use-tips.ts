import { useQuery } from "@tanstack/react-query";

export interface Tip {
  event_id: number;
  home: string;
  away: string;
  league: string;
  predicted: "home" | "away";
  predicted_name: string;
  odds: number | null;
  start_timestamp: number;
  sent_at: number;
  result: "win" | "loss" | null;
  actual_winner: "home" | "away" | null;
}

export interface TipStats {
  total: number;
  settled: number;
  wins: number;
  losses: number;
  pending: number;
  winRate: number;
  roi: number;
  leagueStats: Record<string, { wins: number; losses: number; pending: number }>;
  recentTips: Tip[];
}

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export function useTipStats() {
  return useQuery<TipStats>({
    queryKey: ["tip-stats"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/tips/stats`);
      if (!res.ok) {
        throw new Error("Failed to fetch tip stats");
      }
      return res.json();
    },
    refetchInterval: 15000,
    staleTime: 0,
  });
}
