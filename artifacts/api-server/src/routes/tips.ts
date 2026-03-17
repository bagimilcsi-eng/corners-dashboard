import { Router } from "express";
import { Pool } from "pg";

const router = Router();

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.DATABASE_URL?.includes("localhost")
    ? false
    : { rejectUnauthorized: false },
});

async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS tips (
      event_id BIGINT PRIMARY KEY,
      home VARCHAR(255) NOT NULL,
      away VARCHAR(255) NOT NULL,
      league VARCHAR(255) NOT NULL,
      predicted VARCHAR(10) NOT NULL,
      predicted_name VARCHAR(255) NOT NULL,
      odds NUMERIC(6,2),
      start_timestamp BIGINT NOT NULL,
      sent_at BIGINT NOT NULL,
      result VARCHAR(10) DEFAULT NULL,
      actual_winner VARCHAR(10) DEFAULT NULL
    )
  `);
}

initDb().catch(console.error);

interface Tip {
  event_id: number;
  home: string;
  away: string;
  league: string;
  predicted: "home" | "away";
  predicted_name: string;
  odds: number | null;
  start_timestamp: number;
  sent_at: number;
  result: "win" | "loss" | null;
  actual_winner: "home" | "away" | null;
}

async function loadTips(): Promise<Tip[]> {
  try {
    const { rows } = await pool.query<Tip>(
      "SELECT * FROM tips ORDER BY sent_at DESC"
    );
    return rows;
  } catch (err) {
    console.error("DB loadTips error:", err);
    return [];
  }
}

router.get("/tips", async (_req, res) => {
  const tips = await loadTips();
  res.json({ tips });
});

router.get("/tips/stats", async (_req, res) => {
  const tips = await loadTips();

  const settled = tips.filter((t) => t.result !== null);
  const wins = settled.filter((t) => t.result === "win");
  const losses = settled.filter((t) => t.result === "loss");
  const pending = tips.filter((t) => t.result === null);

  const winRate = settled.length > 0 ? (wins.length / settled.length) * 100 : 0;

  let roiSum = 0;
  let roiCount = 0;
  for (const t of settled) {
    if (t.odds) {
      roiSum += t.result === "win" ? Number(t.odds) - 1 : -1;
      roiCount++;
    }
  }
  const roi = roiCount > 0 ? (roiSum / roiCount) * 100 : 0;

  const leagueMap: Record<string, { wins: number; losses: number; pending: number }> = {};
  for (const t of tips) {
    if (!leagueMap[t.league]) leagueMap[t.league] = { wins: 0, losses: 0, pending: 0 };
    if (t.result === "win") leagueMap[t.league].wins++;
    else if (t.result === "loss") leagueMap[t.league].losses++;
    else leagueMap[t.league].pending++;
  }

  const recent = [...tips].slice(0, 20);

  res.json({
    total: tips.length,
    settled: settled.length,
    wins: wins.length,
    losses: losses.length,
    pending: pending.length,
    winRate: Math.round(winRate * 10) / 10,
    roi: Math.round(roi * 10) / 10,
    leagueStats: leagueMap,
    recentTips: recent,
  });
});

export default router;
