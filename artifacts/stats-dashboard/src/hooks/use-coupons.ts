import { useQuery } from "@tanstack/react-query";

const API_BASE = (import.meta.env.VITE_API_BASE as string) || "";

export interface CouponPick {
  event_id: string;
  sport: string;
  sport_key: string;
  home: string;
  away: string;
  pick: "home" | "away";
  pick_name: string;
  odds: number;
  n_bookmakers: number;
  start_timestamp: number;
  sofa_confirmed: boolean;
  result: "win" | "loss" | null;
}

export interface Coupon {
  id: number;
  coupon_number: number;
  picks: CouponPick[];
  combined_odds: number;
  sent_at: number;
  result: "win" | "loss" | null;
  settled_at: number | null;
}

export function useAllCoupons() {
  return useQuery<Coupon[]>({
    queryKey: ["all-coupons"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/coupons`);
      if (!res.ok) throw new Error("Nem sikerült betölteni a szelvényeket");
      return res.json();
    },
    refetchInterval: 30_000,
    staleTime: 0,
  });
}
