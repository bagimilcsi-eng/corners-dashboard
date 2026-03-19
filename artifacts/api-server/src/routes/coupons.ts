import { Router } from "express";
import { Pool } from "pg";

const router = Router();

const dbUrl = process.env.SUPABASE_DATABASE_URL || process.env.DATABASE_URL || "";
const isLocal =
  dbUrl.includes("helium") ||
  dbUrl.includes("localhost") ||
  dbUrl.includes("sslmode=disable");

const pool = new Pool({
  connectionString: dbUrl,
  ssl: isLocal ? false : { rejectUnauthorized: false },
});

router.get("/coupons", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM coupons ORDER BY sent_at DESC"
    );
    const parsed = rows.map((r) => ({
      ...r,
      picks: typeof r.picks === "string" ? JSON.parse(r.picks) : r.picks,
    }));
    res.json(parsed);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.get("/coupons/stats", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM coupons ORDER BY sent_at DESC"
    );

    const coupons = rows.map((r) => ({
      ...r,
      picks: typeof r.picks === "string" ? JSON.parse(r.picks) : r.picks,
    }));

    const settled = coupons.filter((c) => c.result !== null);
    const wins = settled.filter((c) => c.result === "win").length;
    const losses = settled.length - wins;
    const pending = coupons.filter((c) => c.result === null).length;
    const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0;

    let roiSum = 0;
    let oddsSum = 0;
    let oddsCount = 0;
    for (const c of settled) {
      const o = Number(c.combined_odds);
      roiSum += c.result === "win" ? o - 1 : -1;
      oddsSum += o;
      oddsCount++;
    }
    const roi = settled.length > 0 ? (roiSum / settled.length) * 100 : 0;
    const avgOdds = oddsCount > 0 ? oddsSum / oddsCount : null;

    res.json({
      total: coupons.length,
      settled: settled.length,
      wins,
      losses,
      pending,
      winRate: Math.round(winRate * 10) / 10,
      roi: Math.round(roi * 10) / 10,
      avgOdds: avgOdds !== null ? Math.round(avgOdds * 100) / 100 : null,
      recentCoupons: coupons.slice(0, 20),
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
