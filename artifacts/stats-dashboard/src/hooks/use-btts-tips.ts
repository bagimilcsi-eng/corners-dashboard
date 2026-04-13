import { useQuery } from "@tanstack/react-query";

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export interface BttsTip {
  fixture_id: number;
  home: string;
  away: string;
  league: string;
  league_id: number | null;
  country: string | null;
  match_time: string;
  odds: number;
  home_btts_rate: number | null;
  away_btts_rate: number | null;
  confidence: number | null;
  result: "WIN" | "LOSS" | null;
  actual_home_goals: number | null;
  actual_away_goals: number | null;
  sent_at: string;
}

export function useBttsTips() {
  return useQuery<BttsTip[]>({
    queryKey: ["btts-tips"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/btts-tips`);
      if (!res.ok) throw new Error("Nem sikerült betölteni a BTTS tippeket");
      return res.json();
    },
    refetchInterval: 30_000,
    staleTime: 0,
  });
}
