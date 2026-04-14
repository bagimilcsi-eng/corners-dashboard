import { useState } from "react";
import { useCricketTips, type CricketTip } from "@/hooks/use-cricket-tips";
import { format } from "date-fns";
import { hu } from "date-fns/locale";
import {
  Trophy, TrendingUp, Target, Clock, CheckCircle2, XCircle,
  RefreshCcw, BarChart3, Activity,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MonthPicker, buildMonthKeys, isInMonth, type MonthKey } from "@/components/ui/month-picker";
import { cn, formatPercentage, formatROI } from "@/lib/utils";

const NAV_LINK = "flex items-center gap-1 px-3 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all text-sm font-medium";
const base = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");

function computeStats(tips: CricketTip[]) {
  const settled = tips.filter((t) => t.result === "win" || t.result === "loss");
  const wins = settled.filter((t) => t.result === "win").length;
  const losses = settled.filter((t) => t.result === "loss").length;
  const pending = tips.filter((t) => t.result === null).length;
  const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0;

  let roiSum = 0, roiCount = 0, oddsSum = 0;
  for (const t of settled) {
    roiSum += t.result === "win" ? Number(t.dog_odds) - 1 : -1;
    roiCount++;
    oddsSum += Number(t.dog_odds);
  }
  const roi = roiCount > 0 ? (roiSum / roiCount) * 100 : 0;
  const avgOdds = roiCount > 0 ? oddsSum / roiCount : null;

  return { total: tips.length, settled: settled.length, wins, losses, pending, winRate, roi, avgOdds };
}

function TipCard({ tip }: { tip: CricketTip }) {
  const dt = format(new Date(tip.match_time), "MMM d. HH:mm", { locale: hu });
  const favTeam = tip.home_odds <= tip.away_odds ? tip.home : tip.away;
  const favOdds = Math.min(tip.home_odds, tip.away_odds);

  return (
    <Card className="glass-card hover:-translate-y-0.5 transition-transform duration-300">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">🏏 {tip.league}</span>
            <span className="text-xs text-muted-foreground">· {dt}</span>
          </div>
          <div className="flex items-center gap-2">
            {tip.result === "win" ? (
              <Badge variant="success" className="shadow-sm shadow-success/20">✅ Nyert</Badge>
            ) : tip.result === "loss" ? (
              <Badge variant="destructive" className="shadow-sm shadow-destructive/20">❌ Veszett</Badge>
            ) : (
              <Badge variant="warning" className="shadow-sm shadow-warning/20">⏳ Folyamatban</Badge>
            )}
          </div>
        </div>

        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-foreground">
              {tip.home} <span className="text-muted-foreground font-normal">vs</span> {tip.away}
            </p>
            <div className="flex items-center gap-3 mt-1 text-sm flex-wrap">
              <span className="font-bold text-blue-400">🏏 Tipp: {tip.tip_team}</span>
              <span className="font-bold text-green-400 bg-green-400/10 px-2 py-0.5 rounded-md">
                @{Number(tip.dog_odds).toFixed(2)}
              </span>
              <span className="text-xs text-muted-foreground">
                Away-Dog stratégia
              </span>
            </div>
            <div className="mt-2 flex gap-4 text-xs text-muted-foreground flex-wrap">
              <span>Favorit: <span className="text-foreground font-medium">{favTeam} @{favOdds.toFixed(2)}</span></span>
              <span>
                {tip.home} @{tip.home_odds.toFixed(2)} |{" "}
                {tip.away} @{tip.away_odds.toFixed(2)}
              </span>
            </div>
          </div>
          {tip.result != null && tip.actual_winner && (
            <div className="text-right shrink-0">
              <p className="text-xs text-muted-foreground">Győztes</p>
              <p className="font-bold text-sm">
                {tip.actual_winner === "home" ? tip.home : tip.away}
              </p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function CricketDashboard() {
  const { data, isLoading, isError, refetch, isFetching } = useCricketTips();
  const [selectedMonth, setSelectedMonth] = useState<MonthKey>("all");

  if (isLoading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center space-y-4">
        <div className="relative w-16 h-16">
          <div className="absolute inset-0 border-4 border-primary/20 rounded-full" />
          <div className="absolute inset-0 border-4 border-primary rounded-full border-t-transparent animate-spin" />
        </div>
        <p className="text-muted-foreground font-medium animate-pulse">Adatok betöltése...</p>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="max-w-md w-full border-destructive/50 bg-destructive/5">
          <CardContent className="pt-6 flex flex-col items-center text-center space-y-4">
            <XCircle className="w-12 h-12 text-destructive" />
            <div>
              <h3 className="text-xl font-bold">Hiba történt</h3>
              <p className="text-muted-foreground">Nem sikerült betölteni a cricket tippeket.</p>
            </div>
            <button onClick={() => refetch()} className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm">
              Újrapróbálás
            </button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const monthKeys = buildMonthKeys(
    data.map((t) => new Date(t.match_time).getTime() / 1000)
  );

  const filtered = selectedMonth === "all"
    ? data
    : data.filter((t) => isInMonth(new Date(t.match_time).getTime() / 1000, selectedMonth));

  const stats = computeStats(filtered);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">

        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <a href={`${base}/`} className="text-muted-foreground hover:text-foreground transition-colors text-sm">← Vissza</a>
            </div>
            <h1 className="text-3xl font-bold tracking-tight">🏏 IPL Cricket Statisztikák</h1>
            <p className="text-muted-foreground mt-1">Away-Dog stratégia · Backtest: +17.2% ROI</p>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className={cn("flex items-center gap-2 px-4 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 transition-all text-sm font-medium", isFetching && "opacity-50")}
          >
            <RefreshCcw className={cn("w-4 h-4", isFetching && "animate-spin")} />
            Frissítés
          </button>
        </div>

        {/* Nav */}
        <div className="flex flex-wrap gap-2">
          <a href={`${base}/corners`}     className={NAV_LINK}>📐 Szöglet</a>
          <a href={`${base}/football25`}  className={NAV_LINK}>⚽ Foci 2.5</a>
          <a href={`${base}/basketball`}  className={NAV_LINK}>🏀 Kosár</a>
          <a href={`${base}/btts`}        className={NAV_LINK}>⚽⚽ BTTS</a>
          <a href={`${base}/multi-sport`} className={NAV_LINK}>🏒 Multi</a>
        </div>

        {/* Month picker */}
        {monthKeys.length > 1 && (
          <MonthPicker months={monthKeys} selected={selectedMonth} onChange={setSelectedMonth} />
        )}

        {/* Stats cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Card className="glass-card">
            <CardHeader className="pb-2 pt-4 px-5">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Target className="w-3.5 h-3.5" /> Összes tipp
              </CardTitle>
            </CardHeader>
            <CardContent className="px-5 pb-4">
              <div className="text-3xl font-bold">{stats.total}</div>
              <p className="text-xs text-muted-foreground mt-1">{stats.pending} folyamatban</p>
            </CardContent>
          </Card>

          <Card className="glass-card">
            <CardHeader className="pb-2 pt-4 px-5">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <Trophy className="w-3.5 h-3.5" /> Találati arány
              </CardTitle>
            </CardHeader>
            <CardContent className="px-5 pb-4">
              <div className={cn("text-3xl font-bold", stats.winRate >= 55 ? "text-success" : stats.winRate >= 45 ? "text-warning" : "text-destructive")}>
                {stats.settled > 0 ? formatPercentage(stats.winRate) : "—"}
              </div>
              <p className="text-xs text-muted-foreground mt-1">{stats.wins}N / {stats.losses}V</p>
            </CardContent>
          </Card>

          <Card className="glass-card">
            <CardHeader className="pb-2 pt-4 px-5">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5" /> ROI
              </CardTitle>
            </CardHeader>
            <CardContent className="px-5 pb-4">
              <div className={cn("text-3xl font-bold", stats.roi > 0 ? "text-success" : stats.settled === 0 ? "text-muted-foreground" : "text-destructive")}>
                {stats.settled > 0 ? formatROI(stats.roi) : "—"}
              </div>
              <p className="text-xs text-muted-foreground mt-1">{stats.settled} lezárt</p>
            </CardContent>
          </Card>

          <Card className="glass-card">
            <CardHeader className="pb-2 pt-4 px-5">
              <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                <BarChart3 className="w-3.5 h-3.5" /> Átlag szorzó
              </CardTitle>
            </CardHeader>
            <CardContent className="px-5 pb-4">
              <div className="text-3xl font-bold">
                {stats.avgOdds != null ? stats.avgOdds.toFixed(2) : "—"}
              </div>
              <p className="text-xs text-muted-foreground mt-1">IPL Away-Dog</p>
            </CardContent>
          </Card>
        </div>

        {/* Backtest info */}
        <Card className="glass-card border-blue-500/20 bg-blue-500/5">
          <CardContent className="p-5">
            <p className="text-sm font-semibold text-blue-400 mb-1">📊 Backtest eredmény (87 meccs, 2 szezon)</p>
            <div className="flex flex-wrap gap-6 text-sm text-muted-foreground">
              <span>IPL 2025: <span className="text-foreground font-medium">+10.2% ROI</span></span>
              <span>IPL 2026: <span className="text-foreground font-medium">+14.2% ROI</span></span>
              <span>Összesített: <span className="text-success font-bold">+17.2% ROI</span></span>
              <span>Stratégia: <span className="text-foreground font-medium">Vendég underdog @1.80–2.50</span></span>
            </div>
          </CardContent>
        </Card>

        {/* Tip list */}
        <div className="space-y-3">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Activity className="w-5 h-5 text-primary" /> Tippek
            <span className="text-sm font-normal text-muted-foreground">({filtered.length})</span>
          </h2>
          {filtered.length === 0 ? (
            <Card className="glass-card">
              <CardContent className="py-12 flex flex-col items-center justify-center text-center gap-3">
                <Clock className="w-10 h-10 text-muted-foreground/40" />
                <p className="text-muted-foreground">Még nincsenek cricket tippek.</p>
                <p className="text-xs text-muted-foreground/60">A bot naponta 1-2 IPL tippet küld.</p>
              </CardContent>
            </Card>
          ) : (
            filtered.map((tip) => <TipCard key={tip.id} tip={tip} />)
          )}
        </div>
      </div>
    </div>
  );
}
