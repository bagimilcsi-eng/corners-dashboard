import { useQuery } from "@tanstack/react-query";

export interface Football25Tip {
  fixture_id:      number;
  home:            string;
  away:            string;
  league:          string;
  league_id:       number | null;
  country:         string | null;
  start_timestamp: number;
  tip:             "over" | "under";
  line:            number;
  odds:            number | null;
  bookmaker_count: number | null;
  h2h_over_rate:   number | null;
  home_over_rate:  number | null;
  away_over_rate:  number | null;
  combined_score:  number | null;
  ht_goal_rate:    number | null;
  sent_at:         number;
  result:          "win" | "loss" | null;
  actual_goals:    number | null;
}

const API_BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

async function fetchTips(): Promise<Football25Tip[]> {
  const res = await fetch(`${API_BASE}/api/football25-tips`);
  if (!res.ok) throw new Error("Nem sikerült betölteni az adatokat");
  return res.json();
}

export function useFootball25Tips() {
  return useQuery<Football25Tip[]>({
    queryKey: ["football25-tips"],
    queryFn: fetchTips,
    refetchInterval: 60_000,
  });
}
