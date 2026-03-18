import { useQuery } from "@tanstack/react-query";

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string;
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_KEY as string;

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
  sent_at: number;
  result: "win" | "loss" | null;
  actual_corners: number | null;
}

export function useCornerTips() {
  return useQuery<CornerTip[]>({
    queryKey: ["corner-tips"],
    queryFn: async () => {
      const res = await fetch(
        `${SUPABASE_URL}/rest/v1/corner_tips?order=start_timestamp.desc`,
        {
          headers: {
            apikey: SUPABASE_KEY,
            Authorization: `Bearer ${SUPABASE_KEY}`,
          },
        }
      );
      if (!res.ok) throw new Error("Nem sikerült betölteni a szöglet tippeket");
      return res.json();
    },
    refetchInterval: 60_000,
  });
}
