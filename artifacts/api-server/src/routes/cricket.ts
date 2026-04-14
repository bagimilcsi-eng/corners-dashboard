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

router.get("/cricket-tips", async (_req, res) => {
  try {
    const { rows } = await pool.query(
      "SELECT * FROM cricket_tips ORDER BY match_time DESC"
    );
    res.json(rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
