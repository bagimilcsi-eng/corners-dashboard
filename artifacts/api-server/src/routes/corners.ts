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

async function initCornerDb() {
  const client = await pool.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS corner_tips (
        event_id         BIGINT PRIMARY KEY,
        home             TEXT NOT NULL,
        away             TEXT NOT NULL,
        league           TEXT NOT NULL,
        league_id        INTEGER,
        start_timestamp  BIGINT NOT NULL,
        tip              TEXT NOT NULL,
        line             REAL DEFAULT 9.5,
        expected_corners REAL NOT NULL,
        home_avg         REAL,
        away_avg         REAL,
        odds             REAL DEFAULT NULL,
        sent_at          BIGINT NOT NULL,
        result           TEXT DEFAULT NULL,
        actual_corners   INTEGER DEFAULT NULL
      )
    `);
    await client.query(`ALTER TABLE corner_tips ADD COLUMN IF NOT EXISTS odds REAL DEFAULT NULL`);
  } finally {
    client.release();
  }
}

initCornerDb().catch((e) =>
  console.error("corner_tips tábla init hiba:", e.message)
);

router.get("/corner-tips", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM corner_tips ORDER BY start_timestamp DESC"
    );
    res.json(rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.post("/corner-tips", async (req, res) => {
  const t = req.body;
  try {
    await pool.query(
      `INSERT INTO corner_tips
        (event_id, home, away, league, league_id, start_timestamp, tip, line,
         expected_corners, home_avg, away_avg, odds, sent_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
       ON CONFLICT (event_id) DO NOTHING`,
      [
        t.event_id, t.home, t.away, t.league, t.league_id,
        t.start_timestamp, t.tip, t.line ?? 9.5,
        t.expected_corners, t.home_avg, t.away_avg, t.odds ?? null, t.sent_at,
      ]
    );
    res.json({ ok: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.patch("/corner-tips/:event_id", async (req, res) => {
  const { event_id } = req.params;
  const { result, actual_corners } = req.body;
  try {
    await pool.query(
      "UPDATE corner_tips SET result=$1, actual_corners=$2 WHERE event_id=$3",
      [result, actual_corners, event_id]
    );
    res.json({ ok: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
