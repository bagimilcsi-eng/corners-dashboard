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

async function initBasketballDb() {
  const client = await pool.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS basketball_tips (
        event_id        BIGINT PRIMARY KEY,
        home            TEXT NOT NULL,
        away            TEXT NOT NULL,
        league          TEXT NOT NULL,
        league_id       INTEGER,
        start_timestamp BIGINT NOT NULL,
        tip             TEXT NOT NULL,
        line            REAL NOT NULL,
        expected_total  REAL NOT NULL,
        home_off_rating REAL,
        away_off_rating REAL,
        home_def_rating REAL,
        away_def_rating REAL,
        home_pace       REAL,
        away_pace       REAL,
        odds            REAL,
        sent_at         BIGINT NOT NULL,
        result          TEXT DEFAULT NULL,
        actual_total    INTEGER DEFAULT NULL,
        confidence_score INTEGER DEFAULT NULL,
        injury_impact   REAL DEFAULT NULL
      )
    `);
  } finally {
    client.release();
  }
}

initBasketballDb().catch((e) =>
  console.error("basketball_tips init hiba:", e.message)
);

interface BballTip {
  event_id: number;
  home: string;
  away: string;
  league: string;
  league_id: number | null;
  start_timestamp: number;
  tip: string;
  line: number;
  expected_total: number;
  home_off_rating: number | null;
  away_off_rating: number | null;
  home_def_rating: number | null;
  away_def_rating: number | null;
  home_pace: number | null;
  away_pace: number | null;
  odds: number | null;
  sent_at: number;
  result: "win" | "loss" | null;
  actual_total: number | null;
  confidence_score: number | null;
}

async function loadBballTips(): Promise<BballTip[]> {
  try {
    const { rows } = await pool.query<BballTip>(
      "SELECT * FROM basketball_tips ORDER BY sent_at DESC"
    );
    return rows;
  } catch (err: any) {
    console.error("DB loadBballTips error:", err.message);
    return [];
  }
}

router.get("/basketball", async (_req, res) => {
  const tips = await loadBballTips();
  res.json({ tips });
});

router.get("/basketball/stats", async (_req, res) => {
  const tips = await loadBballTips();

  const settled = tips.filter((t) => t.result === "win" || t.result === "loss");
  const wins    = settled.filter((t) => t.result === "win");
  const losses  = settled.filter((t) => t.result === "loss");
  const pending = tips.filter((t) => t.result === null);

  const winRate = settled.length > 0 ? (wins.length / settled.length) * 100 : 0;

  let roiSum = 0, roiCount = 0, oddsSum = 0, oddsCount = 0;
  for (const t of settled) {
    if (t.odds) {
      roiSum += t.result === "win" ? Number(t.odds) - 1 : -1;
      roiCount++;
      oddsSum += Number(t.odds);
      oddsCount++;
    }
  }
  const roi     = roiCount > 0 ? (roiSum / roiCount) * 100 : 0;
  const avgOdds = oddsCount > 0 ? oddsSum / oddsCount : null;

  const leagueMap: Record<string, { wins: number; losses: number; pending: number }> = {};
  for (const t of tips) {
    if (!leagueMap[t.league]) leagueMap[t.league] = { wins: 0, losses: 0, pending: 0 };
    if (t.result === "win")  leagueMap[t.league].wins++;
    else if (t.result === "loss") leagueMap[t.league].losses++;
    else leagueMap[t.league].pending++;
  }

  res.json({
    total:    tips.length,
    settled:  settled.length,
    wins:     wins.length,
    losses:   losses.length,
    pending:  pending.length,
    winRate:  Math.round(winRate * 10) / 10,
    roi:      Math.round(roi * 10) / 10,
    avgOdds:  avgOdds !== null ? Math.round(avgOdds * 100) / 100 : null,
    leagueStats: leagueMap,
    allTips:  tips,
  });
});

router.patch("/basketball/:event_id", async (req, res) => {
  const { event_id } = req.params;
  const { result, actual_total } = req.body;
  if (!result) return res.status(400).json({ error: "Missing result" });
  try {
    await pool.query(
      "UPDATE basketball_tips SET result=$1, actual_total=$2 WHERE event_id=$3",
      [result, actual_total ?? null, event_id]
    );
    res.json({ ok: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
