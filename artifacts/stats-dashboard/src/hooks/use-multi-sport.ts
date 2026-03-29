import { useQuery } from "@tanstack/react-query";

export interface MultiSportTip {
  event_id: number;
  sport: string;
  home: string;
  away: string;
  league: string;
  start_timestamp: number;
  tip: string;
  line: number;
  expected_total: number;
  home_avg_scored: number | null;
  away_avg_scored: number | null;
  home_avg_conceded: number | null;
  away_avg_conceded: number | null;
  odds: number | null;
  sent_at: number;
  result: "win" | "loss" | null;
  actual_total: number | null;
  confidence_score: number | null;
}

export interface MultiSportStats {
  total: number;
  settled: number;
  wins: number;
  losses: number;
  pending: number;
  winRate: number;
  roi: number;
  avgOdds: number | null;
  sportStats: Record<string, { wins: number; losses: number; pending: number }>;
  leagueStats: Record<string, { wins: number; losses: number; pending: number }>;
  allTips: MultiSportTip[];
}

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export function useMultiSportStats() {
  return useQuery<MultiSportStats>({
    queryKey: ["multi-sport-stats"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/multi-sport/stats`);
      if (!res.ok) throw new Error("Failed to fetch multi-sport stats");
      return res.json();
    },
    refetchInterval: 30_000,
    staleTime: 0,
  });
}
