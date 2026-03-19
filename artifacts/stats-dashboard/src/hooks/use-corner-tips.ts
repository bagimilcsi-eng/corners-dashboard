import { useQuery } from "@tanstack/react-query";

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export interface CornerTip {
  event_id: number;
  home: string;
  away: string;
  league: string;
  league_id: number | null;
  start_timestamp: number;
  tip: "over" | "under";
  line: number;
  expected_corners: number;
  home_avg: number | null;
  away_avg: number | null;
  odds: number | null;
  sent_at: number;
  result: "win" | "loss" | null;
  actual_corners: number | null;
}

export function useCornerTips() {
  return useQuery<CornerTip[]>({
    queryKey: ["corner-tips"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/corner-tips`);
      if (!res.ok) throw new Error("Nem sikerült betölteni a szöglet tippeket");
      return res.json();
    },
    refetchInterval: 30_000,
    staleTime: 0,
  });
}
