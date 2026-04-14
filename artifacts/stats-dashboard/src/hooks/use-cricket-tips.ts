import { useQuery } from "@tanstack/react-query";

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export interface CricketTip {
  id: number;
  event_id: number;
  home: string;
  away: string;
  league: string;
  match_time: string;
  tip_team: string;
  tip_side: string;
  home_odds: number;
  away_odds: number;
  dog_odds: number;
  sent_at: string;
  result: "win" | "loss" | null;
  actual_winner: string | null;
  resolved_at: string | null;
}

export function useCricketTips() {
  return useQuery<CricketTip[]>({
    queryKey: ["cricket-tips"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/cricket-tips`);
      if (!res.ok) throw new Error("Hiba a cricket tippek betöltésekor");
      return res.json();
    },
    refetchInterval: 60_000,
  });
}
