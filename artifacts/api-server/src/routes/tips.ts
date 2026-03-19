import { Router } from "express";
import { Pool } from "pg";

const router = Router();

const dbUrl = process.env.DATABASE_URL || "";
const isLocal = dbUrl.includes("helium") || dbUrl.includes("localhost") || dbUrl.includes("sslmode=disable");

const pool = new Pool({
  connectionString: dbUrl,
  ssl: isLocal ? false : { rejectUnauthorized: false },
});

async function initDb() {
  const client = await pool.connect();
  try {
    await client.query(`
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
    const { rows } = await client.query("SELECT COUNT(*) FROM tips");
    console.log(`DB connected. Tips in database: ${rows[0].count}`);
  } finally {
    client.release();
  }
}

initDb().catch((err) => console.error("DB init error:", err.message));

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

async function loadTipsFromKV(): Promise<Tip[]> {
  const kvUrl = process.env.REPLIT_DB_URL;
  if (!kvUrl) return [];
  try {
    const resp = await fetch(`${kvUrl}/tips_data`);
    if (!resp.ok) return [];
    const text = await resp.text();
    if (!text) return [];
    const parsed = JSON.parse(decodeURIComponent(text));
    return Array.isArray(parsed) ? parsed : [];
  } catch (err: any) {
    console.error("KV loadTips error:", err.message);
    return [];
  }
}

async function loadTips(): Promise<Tip[]> {
  try {
    const { rows } = await pool.query<Tip>(
      "SELECT * FROM tips ORDER BY sent_at DESC"
    );
    if (rows.length > 0) return rows;
    // Ha a prod DB üres, a KV store-ból olvas (dev bot írja)
    console.log("DB empty, falling back to KV store...");
    return await loadTipsFromKV();
  } catch (err: any) {
    console.error("DB loadTips error:", err.message);
    return await loadTipsFromKV();
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
  let oddsSum = 0;
  let oddsCount = 0;
  for (const t of settled) {
    if (t.odds) {
      roiSum += t.result === "win" ? Number(t.odds) - 1 : -1;
      roiCount++;
      oddsSum += Number(t.odds);
      oddsCount++;
    }
  }
  const roi = roiCount > 0 ? (roiSum / roiCount) * 100 : 0;
  const avgOdds = oddsCount > 0 ? oddsSum / oddsCount : null;

  const leagueMap: Record<string, { wins: number; losses: number; pending: number }> = {};
  for (const t of tips) {
    if (!leagueMap[t.league]) leagueMap[t.league] = { wins: 0, losses: 0, pending: 0 };
    if (t.result === "win") leagueMap[t.league].wins++;
    else if (t.result === "loss") leagueMap[t.league].losses++;
    else leagueMap[t.league].pending++;
  }

  res.json({
    total: tips.length,
    settled: settled.length,
    wins: wins.length,
    losses: losses.length,
    pending: pending.length,
    winRate: Math.round(winRate * 10) / 10,
    roi: Math.round(roi * 10) / 10,
    avgOdds: avgOdds !== null ? Math.round(avgOdds * 100) / 100 : null,
    leagueStats: leagueMap,
    recentTips: [...tips].slice(0, 20),
  });
});

router.post("/tips", async (req, res) => {
  const { event_id, home, away, league, predicted, predicted_name, odds, start_timestamp, sent_at } = req.body;
  if (!event_id || !home || !away || !league || !predicted || !predicted_name || !start_timestamp || !sent_at) {
    return res.status(400).json({ error: "Missing required fields" });
  }
  try {
    await pool.query(
      `INSERT INTO tips (event_id, home, away, league, predicted, predicted_name, odds, start_timestamp, sent_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
       ON CONFLICT (event_id) DO NOTHING`,
      [event_id, home, away, league, predicted, predicted_name, odds ?? null, start_timestamp, sent_at]
    );
    res.json({ ok: true });
  } catch (err: any) {
    console.error("POST /tips error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

router.patch("/tips/:event_id", async (req, res) => {
  const { event_id } = req.params;
  const { result, actual_winner } = req.body;
  if (!result) return res.status(400).json({ error: "Missing result" });
  try {
    await pool.query(
      "UPDATE tips SET result=$1, actual_winner=$2 WHERE event_id=$3",
      [result, actual_winner ?? null, event_id]
    );
    res.json({ ok: true });
  } catch (err: any) {
    console.error("PATCH /tips error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

export default router;
