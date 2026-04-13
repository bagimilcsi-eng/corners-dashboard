import { useState } from "react";
import { useFootball25Tips, type Football25Tip } from "@/hooks/use-football25-tips";
import { format } from "date-fns";
import { hu } from "date-fns/locale";
import {
  Trophy, TrendingUp, Target, Clock, CheckCircle2, XCircle,
  RefreshCcw, BarChart3, Activity, ArrowUp, ArrowDown,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MonthPicker, buildMonthKeys, isInMonth, type MonthKey } from "@/components/ui/month-picker";
import { cn, formatPercentage, formatROI } from "@/lib/utils";

function TipCard({ tip }: { tip: Football25Tip }) {
  const dt     = format(new Date(tip.start_timestamp * 1000), "MMM d. HH:mm", { locale: hu });
  const isOver = tip.tip === "over";

  const h2h  = tip.h2h_over_rate  != null ? `${Math.round(tip.h2h_over_rate  * 100)}%` : "–";
  const home = tip.home_over_rate != null ? `${Math.round(tip.home_over_rate * 100)}%` : "–";
  const away = tip.away_over_rate != null ? `${Math.round(tip.away_over_rate * 100)}%` : "–";
  const ht   = tip.ht_goal_rate   != null ? `${Math.round(tip.ht_goal_rate   * 100)}%` : "–";
  const combined = tip.combined_score != null
    ? Math.round((isOver ? tip.combined_score : 1 - tip.combined_score) * 100)
    : null;

  return (
    <Card className="glass-card hover:-translate-y-0.5 transition-transform duration-300">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">⚽ {tip.league}</span>
            {tip.country && <span className="text-xs text-muted-foreground/60">· {tip.country}</span>}
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
            <p className="font-semibold text-foreground truncate">
              {tip.home} <span className="text-muted-foreground font-normal">vs</span> {tip.away}
            </p>
            <div className="flex items-center gap-3 mt-1 text-sm flex-wrap">
              <span className={cn("font-bold flex items-center gap-1", isOver ? "text-blue-400" : "text-purple-400")}>
                {isOver ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
                {isOver ? "OVER" : "UNDER"} {tip.line ?? 2.5}
              </span>
              {tip.odds != null && (
                <span className="font-bold text-green-400 bg-green-400/10 px-2 py-0.5 rounded-md">
                  @{Number(tip.odds).toFixed(2)}
                </span>
              )}
              {tip.bookmaker_count != null && (
                <span className="text-xs text-muted-foreground">{tip.bookmaker_count} iroda</span>
              )}
              {combined != null && (
                <span className="text-xs text-muted-foreground">
                  Jel: <span className="text-foreground font-medium">{combined}%</span>
                </span>
              )}
            </div>
            <div className="mt-2 flex gap-4 text-xs text-muted-foreground flex-wrap">
              <span>H2H: <span className="text-foreground font-medium">{h2h}</span></span>
              <span>Hazai: <span className="text-foreground font-medium">{home}</span></span>
              <span>Vendég: <span className="text-foreground font-medium">{away}</span></span>
              <span>HT gól: <span className="text-foreground font-medium">{ht}</span></span>
            </div>
          </div>
          <div className="text-right shrink-0">
            {tip.result != null && tip.actual_goals != null && (
              <div className="text-sm">
                <p className="text-xs text-muted-foreground">Gólok</p>
                <p className="font-bold text-lg text-foreground">{tip.actual_goals}</p>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function LeagueBreakdown({ tips }: { tips: Football25Tip[] }) {
  const map: Record<string, { wins: number; total: number; pending: number }> = {};
  for (const t of tips) {
    const key = t.league;
    if (!map[key]) map[key] = { wins: 0, total: 0, pending: 0 };
    if (t.result === "win")  map[key].wins++;
    if (t.result != null)    map[key].total++;
    else                     map[key].pending++;
  }
  const entries = Object.entries(map).sort(
    (a, b) => (b[1].total + b[1].pending) - (a[1].total + a[1].pending)
  );
  if (entries.length === 0) return null;

  return (
    <Card className="glass-card h-full flex flex-col">
      <CardHeader className="pb-4 border-b border-border/50">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-primary" />
          <CardTitle>Bajnokságonként</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="divide-y divide-border/50">
          {entries.map(([league, s]) => {
            const pct = s.total > 0 ? (s.wins / s.total) * 100 : 0;
            return (
              <div key={league} className="p-5 hover:bg-secondary/30 transition-colors">
                <div className="flex justify-between items-center mb-3">
                  <h4 className="font-semibold text-sm truncate max-w-[180px]">{league}</h4>
                  <div className="text-right shrink-0">
                    <span className={cn(
                      "text-xl font-bold font-display",
                      s.total === 0 ? "text-muted-foreground" : pct >= 50 ? "text-success" : "text-warning"
                    )}>
                      {s.total > 0 ? formatPercentage(pct) : "—"}
                    </span>
                    <p className="text-xs text-muted-foreground">Nyerési arány</p>
                  </div>
                </div>
                <div className="flex gap-2 h-2 rounded-full overflow-hidden bg-muted">
                  {s.wins > 0 && <div style={{ width: `${(s.wins / Math.max(s.total, 1)) * 100}%` }} className="bg-success" />}
                  {(s.total - s.wins) > 0 && <div style={{ width: `${((s.total - s.wins) / Math.max(s.total, 1)) * 100}%` }} className="bg-destructive" />}
                </div>
                <div className="flex justify-between mt-2 text-sm">
                  <div className="flex items-center gap-1 text-success"><CheckCircle2 className="w-4 h-4" />{s.wins}</div>
                  <div className="flex items-center gap-1 text-destructive"><XCircle className="w-4 h-4" />{s.total - s.wins}</div>
                  <div className="flex items-center gap-1 text-warning"><Clock className="w-4 h-4" />{s.pending}</div>
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

export default function Football25Dashboard() {
  const { data: allTips = [], isLoading, error, refetch, isFetching } = useFootball25Tips();
  const [selectedMonth, setSelectedMonth] = useState<MonthKey>("all");

  const months   = buildMonthKeys(allTips.map((t) => t.start_timestamp));
  const tips     = allTips.filter((t) => isInMonth(t.start_timestamp, selectedMonth));
  const settled  = tips.filter((t) => t.result != null);
  const pending  = tips.filter((t) => t.result == null);
  const wins     = settled.filter((t) => t.result === "win").length;
  const losses   = settled.length - wins;
  const winRate  = settled.length > 0 ? (wins / settled.length) * 100 : 0;

  const overTips   = tips.filter((t) => t.tip === "over");
  const underTips  = tips.filter((t) => t.tip === "under");
  const overWins   = overTips.filter((t)  => t.result === "win").length;
  const underWins  = underTips.filter((t) => t.result === "win").length;

  const oddsSettled = settled.filter((t) => t.odds != null);
  const avgOdds = oddsSettled.length > 0
    ? oddsSettled.reduce((s, t) => s + Number(t.odds), 0) / oddsSettled.length
    : null;

  let roiSum = 0;
  for (const t of settled) {
    const o = t.odds != null ? Number(t.odds) : 1.55;
    roiSum += t.result === "win" ? o - 1 : -1;
  }
  const roi = settled.length > 0 ? (roiSum / settled.length) * 100 : 0;

  const base = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");

  const statCards = [
    { title: "Összes tipp",   value: tips.length,                                    icon: Target,       color: "text-blue-500",     bg: "bg-blue-500/10" },
    { title: "Nyerési arány", value: settled.length > 0 ? formatPercentage(winRate) : "—", icon: Trophy, color: "text-yellow-500",  bg: "bg-yellow-500/10" },
    { title: "ROI",           value: settled.length > 0 ? formatROI(roi) : "—",       icon: TrendingUp,  color: roi >= 0 ? "text-success" : "text-destructive", bg: roi >= 0 ? "bg-success/10" : "bg-destructive/10" },
    { title: "Átlag szorzó",  value: avgOdds != null ? avgOdds.toFixed(2) : "—",      icon: BarChart3,   color: "text-purple-400",   bg: "bg-purple-400/10" },
    { title: "OVER tippek",   value: `${overTips.length} (${overWins}W)`,             icon: ArrowUp,     color: "text-blue-400",     bg: "bg-blue-400/10" },
    { title: "UNDER tippek",  value: `${underTips.length} (${underWins}W)`,           icon: ArrowDown,   color: "text-purple-400",   bg: "bg-purple-400/10" },
    { title: "Nyertes",       value: wins,                                             icon: CheckCircle2,color: "text-success",       bg: "bg-success/10" },
    { title: "Vesztes",       value: losses,                                           icon: XCircle,     color: "text-destructive",  bg: "bg-destructive/10" },
    { title: "Folyamatban",   value: pending.length,                                   icon: Clock,       color: "text-warning",      bg: "bg-warning/10" },
  ];

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

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="max-w-md w-full border-destructive/50 bg-destructive/5">
          <CardContent className="pt-6 flex flex-col items-center text-center space-y-4">
            <XCircle className="w-12 h-12 text-destructive" />
            <div className="space-y-2">
              <h3 className="text-xl font-bold">Hiba történt</h3>
              <p className="text-muted-foreground">Nem sikerült betölteni a tippeket.</p>
            </div>
            <button
              onClick={() => refetch()}
              className="px-4 py-2 bg-background border border-border rounded-lg hover:bg-muted transition-colors flex items-center gap-2"
            >
              <RefreshCcw className="w-4 h-4" />
              Újrapróbálkozás
            </button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-4 md:p-8 max-w-[1600px] mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-bold font-display bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
            Foci Over/Under 2.5
          </h1>
          <p className="text-muted-foreground mt-1 flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            Gól over/under 2.5 tippek · H2H + hazai/vendég forma + HT szűrő
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <a href={`${base}/`}                className="flex items-center gap-1 px-3 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all text-sm font-medium">🏓 Asztalitenisz</a>
          <a href={`${base}/corners`}         className="flex items-center gap-1 px-3 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all text-sm font-medium">📐 Szöglet</a>
          <a href={`${base}/basketball`}      className="flex items-center gap-1 px-3 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all text-sm font-medium">🏀 Kosár</a>
          <a href={`${base}/multi-sport`}     className="flex items-center gap-1 px-3 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all text-sm font-medium">🏒🤾🏐 Multi</a>
          <a href={`${base}/btts`}            className="flex items-center gap-1 px-3 py-2 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all text-sm font-medium">⚽⚽ BTTS</a>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="group flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm"
          >
            <RefreshCcw className={cn("w-4 h-4 text-primary", isFetching && "animate-spin")} />
            <span className="font-medium text-sm">{isFetching ? "Frissítés..." : "Frissítés"}</span>
          </button>
        </div>
      </div>

      {/* Month picker */}
      {months.length > 0 && (
        <MonthPicker months={months} selected={selectedMonth} onChange={setSelectedMonth} />
      )}

      {allTips.length === 0 ? (
        <Card className="border-dashed border-2 bg-transparent">
          <CardContent className="flex flex-col items-center justify-center py-24 text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center">
              <BarChart3 className="w-8 h-8 text-muted-foreground" />
            </div>
            <h3 className="text-xl font-bold">Még nincsenek foci tippek</h3>
            <p className="text-muted-foreground max-w-md">
              A bot 30 percenként keres Over/Under 2.5 tippeket. Amint megfelelő meccsek vannak, itt jelennek meg.
            </p>
          </CardContent>
        </Card>
      ) : tips.length === 0 ? (
        <Card className="border-dashed border-2 bg-transparent">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-secondary flex items-center justify-center">
              <BarChart3 className="w-6 h-6 text-muted-foreground" />
            </div>
            <h3 className="text-lg font-bold">Ebben a hónapban nincs tipp</h3>
            <p className="text-muted-foreground text-sm">Válassz másik hónapot, vagy az "Összes" nézetet.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-8">
          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-9 gap-4">
            {statCards.map((stat, idx) => (
              <Card key={idx} className="glass-card hover:-translate-y-1 transition-transform duration-300">
                <CardContent className="p-5 flex items-center gap-4">
                  <div className={cn("w-12 h-12 rounded-full flex items-center justify-center shrink-0", stat.bg)}>
                    <stat.icon className={cn("w-6 h-6", stat.color)} />
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground font-medium">{stat.title}</p>
                    <p className={cn("text-2xl font-bold font-display tracking-tight", stat.color)}>
                      {stat.value}
                    </p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
            {/* Tips list */}
            <div className="xl:col-span-2 flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Activity className="w-5 h-5 text-primary" />
                  <h2 className="font-bold text-lg">Tippek ({tips.length})</h2>
                </div>
                <Badge variant="secondary" className="font-mono">{tips.length} összes</Badge>
              </div>
              {tips.map((t) => (
                <TipCard key={t.fixture_id} tip={t} />
              ))}
            </div>

            {/* League breakdown */}
            <div className="xl:col-span-1">
              <LeagueBreakdown tips={tips} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
