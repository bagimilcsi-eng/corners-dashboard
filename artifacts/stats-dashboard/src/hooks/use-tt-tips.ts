import { useQuery } from "@tanstack/react-query";
import type { Tip } from "./use-tips";

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

const TT_LEAGUES = ["Setka Cup", "Czech Liga Pro", "TT Cup"];

export type { Tip as TtTip };

export function useTtTips() {
  return useQuery<Tip[]>({
    queryKey: ["tt-tips"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/tips`);
      if (!res.ok) throw new Error("Nem sikerült betölteni az asztalitenisz tippeket");
      const data = await res.json();
      const all: Tip[] = Array.isArray(data) ? data : data.tips ?? [];
      return all.filter((t) =>
        TT_LEAGUES.some((l) => t.league.toLowerCase().includes(l.toLowerCase()))
      );
    },
    refetchInterval: 30_000,
    staleTime: 0,
  });
}
