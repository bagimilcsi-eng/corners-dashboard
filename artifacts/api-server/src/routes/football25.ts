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

async function initDb() {
  const client = await pool.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS football25_tips (
        fixture_id       BIGINT PRIMARY KEY,
        home             TEXT NOT NULL,
        away             TEXT NOT NULL,
        league           TEXT NOT NULL,
        league_id        INTEGER,
        country          TEXT,
        start_timestamp  BIGINT NOT NULL,
        tip              TEXT NOT NULL,
        line             REAL DEFAULT 2.5,
        odds             REAL,
        bookmaker_count  INTEGER,
        h2h_over_rate    REAL,
        home_over_rate   REAL,
        away_over_rate   REAL,
        combined_score   REAL,
        ht_goal_rate     REAL,
        sent_at          BIGINT NOT NULL,
        result           TEXT DEFAULT NULL,
        actual_goals     INTEGER DEFAULT NULL
      )
    `);
  } finally {
    client.release();
  }
}

initDb().catch((e) =>
  console.error("football25_tips tábla init hiba:", e.message)
);

router.get("/football25-tips", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM football25_tips ORDER BY start_timestamp DESC LIMIT 200"
    );
    res.json(rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/football25-tips", async (req, res) => {
  const d = req.body;
  try {
    await pool.query(
      `INSERT INTO football25_tips
        (fixture_id, home, away, league, league_id, country,
         start_timestamp, tip, line, odds, bookmaker_count,
         h2h_over_rate, home_over_rate, away_over_rate,
         combined_score, ht_goal_rate, sent_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
       ON CONFLICT (fixture_id) DO NOTHING`,
      [
        d.fixture_id, d.home, d.away, d.league, d.league_id, d.country,
        d.start_timestamp, d.tip, d.line ?? 2.5, d.odds, d.bookmaker_count,
        d.h2h_over_rate, d.home_over_rate, d.away_over_rate,
        d.combined_score, d.ht_goal_rate, d.sent_at,
      ]
    );
    res.json({ ok: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.patch("/football25-tips/:fixture_id", async (req, res) => {
  const { fixture_id } = req.params;
  const { result, actual_goals } = req.body;
  try {
    await pool.query(
      "UPDATE football25_tips SET result=$1, actual_goals=$2 WHERE fixture_id=$3",
      [result, actual_goals, fixture_id]
    );
    res.json({ ok: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.get("/football25-tips/stats", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM football25_tips ORDER BY start_timestamp DESC"
    );
    const settled = rows.filter((r) => r.result !== null);
    const wins    = settled.filter((r) => r.result === "win").length;
    const losses  = settled.length - wins;
    const pending = rows.filter((r) => r.result === null).length;
    const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0;
    let roiSum = 0, oddsSum = 0;
    for (const r of settled) {
      const o = Number(r.odds) || 1.55;
      roiSum  += r.result === "win" ? o - 1 : -1;
      oddsSum += o;
    }
    const roi     = settled.length > 0 ? (roiSum / settled.length) * 100 : 0;
    const avgOdds = settled.length > 0 ? oddsSum / settled.length : null;

    const overRows  = settled.filter((r) => r.tip === "over");
    const underRows = settled.filter((r) => r.tip === "under");

    res.json({
      total:   rows.length,
      settled: settled.length,
      wins,
      losses,
      pending,
      winRate:  Math.round(winRate  * 10) / 10,
      roi:      Math.round(roi      * 10) / 10,
      avgOdds:  avgOdds !== null ? Math.round(avgOdds * 100) / 100 : null,
      overWins:  overRows.filter((r) => r.result === "win").length,
      overTotal: overRows.length,
      underWins:  underRows.filter((r) => r.result === "win").length,
      underTotal: underRows.length,
      recentTips: rows.slice(0, 30),
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
