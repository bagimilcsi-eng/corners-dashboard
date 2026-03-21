import { useQuery } from "@tanstack/react-query";

export interface BballTip {
  event_id: number;
  home: string;
  away: string;
  league: string;
  start_timestamp: number;
  tip: string;
  line: number;
  expected_total: number;
  home_off_rating: number | null;
  away_off_rating: number | null;
  home_def_rating: number | null;
  away_def_rating: number | null;
  home_pace: number | null;
  away_pace: number | null;
  odds: number | null;
  sent_at: number;
  result: "win" | "loss" | null;
  actual_total: number | null;
  confidence_score: number | null;
}

export interface BballStats {
  total: number;
  settled: number;
  wins: number;
  losses: number;
  pending: number;
  winRate: number;
  roi: number;
  avgOdds: number | null;
  leagueStats: Record<string, { wins: number; losses: number; pending: number }>;
  allTips: BballTip[];
}

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export function useBasketballStats() {
  return useQuery<BballStats>({
    queryKey: ["basketball-stats"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/basketball/stats`);
      if (!res.ok) throw new Error("Failed to fetch basketball stats");
      return res.json();
    },
    refetchInterval: 30_000,
    staleTime: 0,
  });
}
