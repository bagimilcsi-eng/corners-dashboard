import { Router } from "express";
import fs from "fs";
import path from "path";

const router = Router();

const TIPS_FILE = path.resolve(process.cwd(), "../../tips_history.json");

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

function loadTips(): Tip[] {
  try {
    const raw = fs.readFileSync(TIPS_FILE, "utf-8");
    return JSON.parse(raw) as Tip[];
  } catch {
    return [];
  }
}

router.get("/tips", (_req, res) => {
  const tips = loadTips();
  res.json({ tips });
});

router.get("/tips/stats", (_req, res) => {
  const tips = loadTips();

  const settled = tips.filter((t) => t.result !== null);
  const wins = settled.filter((t) => t.result === "win");
  const losses = settled.filter((t) => t.result === "loss");
  const pending = tips.filter((t) => t.result === null);

  const winRate = settled.length > 0 ? (wins.length / settled.length) * 100 : 0;

  // ROI calculation (1-unit stake)
  let roiSum = 0;
  let roiCount = 0;
  for (const t of settled) {
    if (t.odds) {
      roiSum += t.result === "win" ? t.odds - 1 : -1;
      roiCount++;
    }
  }
  const roi = roiCount > 0 ? (roiSum / roiCount) * 100 : 0;

  // Per-league breakdown
  const leagueMap: Record<string, { wins: number; losses: number; pending: number }> = {};
  for (const t of tips) {
    if (!leagueMap[t.league]) leagueMap[t.league] = { wins: 0, losses: 0, pending: 0 };
    if (t.result === "win") leagueMap[t.league].wins++;
    else if (t.result === "loss") leagueMap[t.league].losses++;
    else leagueMap[t.league].pending++;
  }

  // Last 20 tips sorted by time desc
  const recent = [...tips]
    .sort((a, b) => (b.sent_at ?? 0) - (a.sent_at ?? 0))
    .slice(0, 20);

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
