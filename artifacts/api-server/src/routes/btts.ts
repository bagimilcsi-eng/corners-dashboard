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

async function initBttsDb() {
  const client = await pool.connect();
  try {
    await client.query(`
      CREATE TABLE IF NOT EXISTS btts_tips (
        fixture_id        BIGINT PRIMARY KEY,
        home              TEXT,
        away              TEXT,
        league            TEXT,
        league_id         INTEGER,
        country           TEXT,
        match_time        TIMESTAMPTZ,
        odds              FLOAT NOT NULL,
        home_btts_rate    FLOAT,
        away_btts_rate    FLOAT,
        confidence        FLOAT,
        tip_type          TEXT DEFAULT 'YES',
        result            TEXT,
        actual_home_goals INTEGER,
        actual_away_goals INTEGER,
        sent_at           TIMESTAMPTZ DEFAULT NOW()
      )
    `);
  } finally {
    client.release();
  }
}

async function alterBttsDb() {
  const client = await pool.connect();
  try {
    await client.query(`ALTER TABLE btts_tips ADD COLUMN IF NOT EXISTS tip_type TEXT DEFAULT 'YES'`);
  } catch (_) {}
  finally { client.release(); }
}

initBttsDb().then(() => alterBttsDb()).catch((e) =>
  console.error("btts_tips tábla init hiba:", e.message)
);

router.get("/btts-tips", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM btts_tips ORDER BY match_time DESC"
    );
    res.json(rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
