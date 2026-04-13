import { useState } from "react";
import { useBasketballStats, type BballTip } from "@/hooks/use-basketball";
import { format } from "date-fns";
import { hu } from "date-fns/locale";
import {
  Trophy, TrendingUp, Target, Clock, CheckCircle2, XCircle,
  RefreshCcw, BarChart3, TableProperties, Activity,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MonthPicker, buildMonthKeys, isInMonth, type MonthKey } from "@/components/ui/month-picker";
import { cn, formatPercentage, formatROI, formatOdds } from "@/lib/utils";

function computeStats(tips: BballTip[]) {
  const settled = tips.filter((t) => t.result === "win" || t.result === "loss");
  const wins    = settled.filter((t) => t.result === "win").length;
  const losses  = settled.filter((t) => t.result === "loss").length;
  const pending = tips.filter((t) => t.result === null).length;
  const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0;

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
    if (t.result === "win")       leagueMap[t.league].wins++;
    else if (t.result === "loss") leagueMap[t.league].losses++;
    else                          leagueMap[t.league].pending++;
  }

  return { total: tips.length, settled: settled.length, wins, losses, pending, winRate, roi, avgOdds, leagueMap };
}

const NAV_LINK = "flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm text-sm font-medium";

export default function BasketballDashboard() {
  const { data, isLoading, isError, refetch, isFetching } = useBasketballStats();
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
            <h3 className="text-xl font-bold">Hiba történt</h3>
            <button onClick={() => refetch()} className="px-4 py-2 bg-background border border-border rounded-lg hover:bg-muted transition-colors flex items-center gap-2">
              <RefreshCcw className="w-4 h-4" /> Újrapróbálkozás
            </button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const allTips: BballTip[] = data.allTips ?? [];
  const months   = buildMonthKeys(allTips.map((t) => t.start_timestamp));
  const filtered = allTips.filter((t) => isInMonth(t.start_timestamp, selectedMonth));
  const { total, settled, wins, losses, pending, winRate, roi, avgOdds, leagueMap } = computeStats(filtered);

  const statCards = [
    { title: "Összes tipp",    value: total,                                        icon: Target,       color: "text-blue-500",       bg: "bg-blue-500/10" },
    { title: "Nyerési arány",  value: settled > 0 ? formatPercentage(winRate) : "—", icon: Trophy,      color: "text-yellow-500",     bg: "bg-yellow-500/10" },
    { title: "ROI",            value: settled > 0 ? formatROI(roi) : "—",            icon: TrendingUp,  color: roi >= 0 ? "text-success" : "text-destructive", bg: roi >= 0 ? "bg-success/10" : "bg-destructive/10" },
    { title: "Átlag szorzó",   value: avgOdds != null ? avgOdds.toFixed(2) : "—",    icon: BarChart3,   color: "text-purple-400",     bg: "bg-purple-400/10" },
    { title: "Nyertes",        value: wins,                                           icon: CheckCircle2, color: "text-success",       bg: "bg-success/10" },
    { title: "Vesztes",        value: losses,                                         icon: XCircle,     color: "text-destructive",   bg: "bg-destructive/10" },
    { title: "Folyamatban",    value: pending,                                        icon: Clock,       color: "text-warning",        bg: "bg-warning/10" },
  ];

  return (
    <div className="min-h-screen p-4 md:p-8 max-w-[1600px] mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-bold font-display bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
            🏀 Kosárlabda Over/Under
          </h1>
          <p className="text-muted-foreground mt-1 flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            Poisson-eloszlás · Pace · Off/Def Rating · H2H
          </p>
        </div>
        <div className="flex items-center gap-3">
          <a href={`${import.meta.env.BASE_URL || "/"}`}             className={NAV_LINK}>🏓 TT Bot →</a>
          <a href={`${import.meta.env.BASE_URL || "/"}corners`}      className={NAV_LINK}>⚽ Szöglet →</a>
          <a href={`${import.meta.env.BASE_URL || "/"}football25`}    className={NAV_LINK}>⚽ Foci 2.5 →</a>
          <a href={`${import.meta.env.BASE_URL || "/"}multi-sport`}  className={NAV_LINK}>🏒🤾🏐 Multi →</a>
          <a href={`${import.meta.env.BASE_URL || "/"}btts`}         className={NAV_LINK}>⚽⚽ BTTS →</a>
          <button onClick={() => refetch()} disabled={isFetching}
            className="group flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm">
            <RefreshCcw className={cn("w-4 h-4 text-primary", isFetching && "animate-spin")} />
            <span className="font-medium text-sm">{isFetching ? "Frissítés..." : "Frissítés"}</span>
          </button>
        </div>
      </div>

      {months.length > 0 && (
        <MonthPicker months={months} selected={selectedMonth} onChange={setSelectedMonth} />
      )}

      {total === 0 ? (
        <Card className="border-dashed border-2 bg-transparent">
          <CardContent className="flex flex-col items-center justify-center py-24 text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center">
              <BarChart3 className="w-8 h-8 text-muted-foreground" />
            </div>
            <h3 className="text-xl font-bold">
              {selectedMonth === "all" ? "Még nincsenek tippek" : "Ebben a hónapban nincs tipp"}
            </h3>
            <p className="text-muted-foreground max-w-md">A bot 3 óránként keres tippeket NBA, Euroleague és más ligákban.</p>
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
                    <p className={cn("text-2xl font-bold font-display tracking-tight", stat.color)}>{stat.value}</p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
            {/* Tips table */}
            <div className="xl:col-span-2">
              <Card className="glass-card h-full flex flex-col">
                <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <TableProperties className="w-5 h-5 text-primary" />
                    <CardTitle>Tippek</CardTitle>
                  </div>
                  <Badge variant="secondary" className="font-mono">{filtered.length} db</Badge>
                </CardHeader>
                <CardContent className="p-0 flex-1 overflow-auto">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                      <thead className="text-xs text-muted-foreground bg-secondary/50 uppercase border-b border-border/50">
                        <tr>
                          <th className="px-6 py-4 font-medium">Mérkőzés / Liga</th>
                          <th className="px-6 py-4 font-medium">Tipp</th>
                          <th className="px-6 py-4 font-medium">Várható</th>
                          <th className="px-6 py-4 font-medium">Szorzó</th>
                          <th className="px-6 py-4 font-medium">Időpont</th>
                          <th className="px-6 py-4 font-medium text-right">Eredmény</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/50">
                        {filtered.map((tip) => (
                          <tr key={tip.event_id} className="hover:bg-secondary/30 transition-colors">
                            <td className="px-6 py-4">
                              <div className="font-medium">{tip.home} <span className="text-muted-foreground font-normal mx-1">vs</span> {tip.away}</div>
                              <div className="text-xs text-muted-foreground mt-1">{tip.league}</div>
                            </td>
                            <td className="px-6 py-4">
                              <div className="font-medium">{tip.tip}</div>
                              {tip.confidence_score && (
                                <div className="text-xs text-muted-foreground mt-1">{tip.confidence_score}/100 konfidencia</div>
                              )}
                            </td>
                            <td className="px-6 py-4 font-mono text-primary">
                              {tip.expected_total}
                              {tip.actual_total != null && (
                                <span className="text-muted-foreground ml-1">→ {tip.actual_total}</span>
                              )}
                            </td>
                            <td className="px-6 py-4">
                              <span className="font-mono font-medium text-primary bg-primary/10 px-2 py-1 rounded-md">
                                {formatOdds(tip.odds)}
                              </span>
                            </td>
                            <td className="px-6 py-4 text-muted-foreground whitespace-nowrap">
                              {format(new Date(tip.start_timestamp * 1000), "MMM d. HH:mm", { locale: hu })}
                            </td>
                            <td className="px-6 py-4 text-right">
                              {tip.result === "win"  ? <Badge variant="success"     className="shadow-lg shadow-success/20">Nyertes</Badge>
                              : tip.result === "loss" ? <Badge variant="destructive" className="shadow-lg shadow-destructive/20">Vesztes</Badge>
                              :                         <Badge variant="warning"     className="shadow-lg shadow-warning/20">Folyamatban</Badge>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* League breakdown */}
            <div className="xl:col-span-1">
              <Card className="glass-card h-full flex flex-col">
                <CardHeader className="pb-4 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <BarChart3 className="w-5 h-5 text-primary" />
                    <CardTitle>Liga Statisztikák</CardTitle>
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="divide-y divide-border/50">
                    {Object.entries(leagueMap)
                      .sort(([, a], [, b]) => (b.wins + b.losses) - (a.wins + a.losses))
                      .map(([league, stats]) => {
                        const totalLeague = stats.wins + stats.losses;
                        const lgWinRate = totalLeague > 0 ? (stats.wins / totalLeague) * 100 : 0;
                        return (
                          <div key={league} className="p-5 hover:bg-secondary/30 transition-colors">
                            <div className="flex justify-between items-center mb-3">
                              <h4 className="font-semibold text-sm truncate max-w-[160px]">{league}</h4>
                              <div className="text-right shrink-0">
                                <span className={cn("text-xl font-bold font-display", lgWinRate >= 50 ? "text-success" : (lgWinRate > 0 ? "text-warning" : "text-muted-foreground"))}>
                                  {totalLeague > 0 ? formatPercentage(lgWinRate) : "—"}
                                </span>
                                <p className="text-xs text-muted-foreground">Nyerési arány</p>
                              </div>
                            </div>
                            <div className="flex gap-2 h-2 rounded-full overflow-hidden bg-muted">
                              {stats.wins > 0   && <div style={{ width: `${(stats.wins   / (totalLeague || 1)) * 100}%` }} className="bg-success" />}
                              {stats.losses > 0 && <div style={{ width: `${(stats.losses / (totalLeague || 1)) * 100}%` }} className="bg-destructive" />}
                            </div>
                            <div className="flex justify-between mt-2 text-sm">
                              <div className="flex items-center gap-1 text-success"><CheckCircle2 className="w-4 h-4" />{stats.wins}</div>
                              <div className="flex items-center gap-1 text-destructive"><XCircle className="w-4 h-4" />{stats.losses}</div>
                              <div className="flex items-center gap-1 text-warning"><Clock className="w-4 h-4" />{stats.pending}</div>
                            </div>
                          </div>
                        );
                      })}
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
