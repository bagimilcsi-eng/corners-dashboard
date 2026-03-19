import { useCornerTips, type CornerTip } from "@/hooks/use-corner-tips";
import { format } from "date-fns";
import { hu } from "date-fns/locale";
import {
  Trophy,
  TrendingUp,
  Target,
  Clock,
  CheckCircle2,
  XCircle,
  RefreshCcw,
  BarChart3,
  Activity,
  ArrowUp,
  ArrowDown,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn, formatPercentage, formatROI } from "@/lib/utils";

function getStrength(expected: number): { label: string; color: string; dots: number } {
  const margin = Math.abs(expected - 9.5);
  if (margin >= 2.5) return { label: "Nagyon erős", color: "text-red-400", dots: 3 };
  if (margin >= 1.5) return { label: "Erős", color: "text-orange-400", dots: 2 };
  return { label: "Mérsékelt", color: "text-yellow-400", dots: 1 };
}

function TipCard({ tip }: { tip: CornerTip }) {
  const dt = format(new Date(tip.start_timestamp * 1000), "MMM d. HH:mm", { locale: hu });
  const isOver = tip.tip === "over";
  const strength = getStrength(Number(tip.expected_corners));

  return (
    <Card className="glass-card hover:-translate-y-0.5 transition-transform duration-300">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">🏆 {tip.league}</span>
            <span className="text-xs text-muted-foreground">· {dt}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn("font-bold text-sm flex items-center gap-1", strength.color)}>
              {"⚡".repeat(strength.dots)} {strength.label}
            </span>
            {tip.result === "win" ? (
              <Badge variant="success" className="shadow-sm shadow-success/20">✅ Nyert</Badge>
            ) : tip.result === "loss" ? (
              <Badge variant="destructive" className="shadow-sm shadow-destructive/20">❌ Veszett</Badge>
            ) : (
              <Badge variant="warning" className="shadow-sm shadow-warning/20">⏳ Folyamatban</Badge>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <p className="font-semibold text-foreground">{tip.home} <span className="text-muted-foreground font-normal">vs</span> {tip.away}</p>
            <div className="flex items-center gap-4 mt-1 text-sm">
              <span className={cn("font-bold flex items-center gap-1", isOver ? "text-blue-400" : "text-purple-400")}>
                {isOver ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
                {isOver ? "OVER" : "UNDER"} {tip.line}
              </span>
              <span className="text-muted-foreground">
                Várható: <span className="text-foreground font-medium">{Number(tip.expected_corners).toFixed(1)}</span> szöglet
              </span>
              {tip.home_avg != null && tip.away_avg != null && (
                <span className="text-muted-foreground text-xs">
                  ({tip.home_avg.toFixed(1)} / {tip.away_avg.toFixed(1)})
                </span>
              )}
            </div>
          </div>
          <div className="text-right shrink-0">
            {tip.result != null && tip.actual_corners != null && (
              <div className="text-sm">
                <p className="text-xs text-muted-foreground">Valós szöglet</p>
                <p className="font-bold text-lg text-foreground">{tip.actual_corners}</p>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function LeagueBreakdown({ tips }: { tips: CornerTip[] }) {
  const map: Record<string, { wins: number; total: number; pending: number }> = {};

  for (const t of tips) {
    if (!map[t.league]) map[t.league] = { wins: 0, total: 0, pending: 0 };
    if (t.result === "win") map[t.league].wins++;
    if (t.result != null) map[t.league].total++;
    else map[t.league].pending++;
  }

  const entries = Object.entries(map).sort((a, b) => (b[1].total + b[1].pending) - (a[1].total + a[1].pending));
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

export default function CornersDashboard() {
  const { data: tips = [], isLoading, error, refetch, isFetching } = useCornerTips();

  const settled = tips.filter((t) => t.result != null);
  const pending = tips.filter((t) => t.result == null);
  const wins = settled.filter((t) => t.result === "win").length;
  const losses = settled.length - wins;
  const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0;

  const overTips = tips.filter((t) => t.tip === "over");
  const underTips = tips.filter((t) => t.tip === "under");
  const overWins = overTips.filter((t) => t.result === "win").length;
  const underWins = underTips.filter((t) => t.result === "win").length;

  const oddsSettled = settled.filter((t) => t.odds != null);
  const avgOdds = oddsSettled.length > 0
    ? oddsSettled.reduce((s, t) => s + Number(t.odds), 0) / oddsSettled.length
    : null;

  let roiSum = 0;
  for (const t of settled) {
    const o = t.odds != null ? Number(t.odds) : 1.62;
    roiSum += t.result === "win" ? o - 1 : -1;
  }
  const roi = settled.length > 0 ? (roiSum / settled.length) * 100 : 0;

  const statCards = [
    { title: "Összes tipp", value: tips.length, icon: Target, color: "text-blue-500", bg: "bg-blue-500/10" },
    { title: "Nyerési arány", value: settled.length > 0 ? formatPercentage(winRate) : "—", icon: Trophy, color: "text-yellow-500", bg: "bg-yellow-500/10" },
    { title: "ROI", value: settled.length > 0 ? formatROI(roi) : "—", icon: TrendingUp, color: roi >= 0 ? "text-success" : "text-destructive", bg: roi >= 0 ? "bg-success/10" : "bg-destructive/10" },
    { title: "Átlag szorzó", value: avgOdds != null ? avgOdds.toFixed(2) : "—", icon: BarChart3, color: "text-purple-400", bg: "bg-purple-400/10" },
    { title: "Over tippek", value: `${overTips.length} (${overWins}W)`, icon: ArrowUp, color: "text-blue-400", bg: "bg-blue-400/10" },
    { title: "Under tippek", value: `${underTips.length} (${underWins}W)`, icon: ArrowDown, color: "text-purple-400", bg: "bg-purple-400/10" },
    { title: "Folyamatban", value: pending.length, icon: Clock, color: "text-warning", bg: "bg-warning/10" },
  ];

  if (isLoading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center space-y-4">
        <div className="relative w-16 h-16">
          <div className="absolute inset-0 border-4 border-primary/20 rounded-full"></div>
          <div className="absolute inset-0 border-4 border-primary rounded-full border-t-transparent animate-spin"></div>
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
              <p className="text-muted-foreground">Nem sikerült betölteni a szöglet tippeket.</p>
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
            Szöglet Bot Statisztikák
          </h1>
          <p className="text-muted-foreground mt-1 flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            Over/Under {tips[0]?.line ?? 9.5} szöglet tippek
          </p>
        </div>
        <div className="flex items-center gap-3">
          <a
            href={import.meta.env.BASE_URL || "/"}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm text-sm font-medium"
          >
            🏓 Asztalitenisz →
          </a>
          <a
            href={`${import.meta.env.BASE_URL || "/"}coupons`}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm text-sm font-medium"
          >
            🎯 Szelvény →
          </a>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="group flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm"
          >
            <RefreshCcw className={cn("w-4 h-4 text-primary", isFetching && "animate-spin")} />
            <span className="font-medium text-sm">
              {isFetching ? "Frissítés..." : "Frissítés"}
            </span>
          </button>
        </div>
      </div>

      {tips.length === 0 ? (
        <Card className="border-dashed border-2 bg-transparent">
          <CardContent className="flex flex-col items-center justify-center py-24 text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center">
              <BarChart3 className="w-8 h-8 text-muted-foreground" />
            </div>
            <h3 className="text-xl font-bold">Még nincsenek szöglet tippek</h3>
            <p className="text-muted-foreground max-w-md">
              A bot automatikusan keres over/under {9.5} szöglet tippeket. Amint megfelelő meccsek vannak, itt jelennek meg.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-8">
          {/* Stat cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-4">
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
                <TipCard key={t.event_id} tip={t} />
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
