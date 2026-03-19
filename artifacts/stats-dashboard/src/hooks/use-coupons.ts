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

export interface CouponStats {
  total: number;
  settled: number;
  wins: number;
  losses: number;
  pending: number;
  winRate: number;
  roi: number;
  avgOdds: number | null;
  recentCoupons: Coupon[];
}

export function useCouponStats() {
  return useQuery<CouponStats>({
    queryKey: ["coupon-stats"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/coupons/stats`);
      if (!res.ok) throw new Error("Nem sikerült betölteni a szelvény statisztikákat");
      return res.json();
    },
    refetchInterval: 30_000,
  });
}
