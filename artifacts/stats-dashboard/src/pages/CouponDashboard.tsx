import { useState } from "react";
import { useAllCoupons, type Coupon, type CouponPick } from "@/hooks/use-coupons";
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
  Ticket,
  Activity,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MonthPicker, buildMonthKeys, isInMonth, type MonthKey } from "@/components/ui/month-picker";
import { cn, formatPercentage, formatROI } from "@/lib/utils";

const SPORT_EMOJI: Record<string, string> = {
  soccer: "⚽",
  basketball: "🏀",
  americanfootball: "🏈",
  tennis: "🎾",
  icehockey: "🏒",
  baseball: "⚾",
  rugby: "🏉",
  mma: "🥊",
};

function computeCouponStats(coupons: Coupon[]) {
  const settled = coupons.filter((c) => c.result !== null);
  const wins = settled.filter((c) => c.result === "win").length;
  const losses = settled.length - wins;
  const pending = coupons.filter((c) => c.result === null).length;
  const winRate = settled.length > 0 ? (wins / settled.length) * 100 : 0;

  let roiSum = 0, oddsSum = 0, oddsCount = 0;
  for (const c of settled) {
    const o = Number(c.combined_odds);
    roiSum += c.result === "win" ? o - 1 : -1;
    oddsSum += o;
    oddsCount++;
  }
  const roi = settled.length > 0 ? (roiSum / settled.length) * 100 : 0;
  const avgOdds = oddsCount > 0 ? oddsSum / oddsCount : null;

  return { total: coupons.length, settled: settled.length, wins, losses, pending, winRate, roi, avgOdds };
}

function PickRow({ pick }: { pick: CouponPick }) {
  const emoji = SPORT_EMOJI[pick.sport] ?? "🏅";
  const dt = format(new Date(pick.start_timestamp * 1000), "MMM d. HH:mm", { locale: hu });

  return (
    <div className="flex items-center justify-between gap-2 py-2 border-b border-border/30 last:border-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-base">{emoji}</span>
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            <span className="font-semibold text-sm text-foreground truncate">
              {pick.pick_name}
            </span>
            {pick.sofa_confirmed && (
              <span className="text-xs text-success">✔</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground truncate">
            {pick.home} vs {pick.away} · {dt}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="font-mono text-sm font-bold text-primary bg-primary/10 px-2 py-0.5 rounded-md">
          @{Number(pick.odds).toFixed(2)}
        </span>
        {pick.result === "win" ? (
          <Badge variant="success" className="text-xs">Nyert</Badge>
        ) : pick.result === "loss" ? (
          <Badge variant="destructive" className="text-xs">Veszett</Badge>
        ) : (
          <Badge variant="warning" className="text-xs">⏳</Badge>
        )}
      </div>
    </div>
  );
}

function CouponCard({ coupon }: { coupon: Coupon }) {
  const sentDt = format(new Date(coupon.sent_at * 1000), "MMM d. HH:mm", { locale: hu });

  return (
    <Card className="glass-card hover:-translate-y-0.5 transition-transform duration-300">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Ticket className="w-4 h-4 text-primary" />
            <span className="font-bold text-foreground">
              #{String(coupon.coupon_number).padStart(3, "0")}
            </span>
            <span className="text-xs text-muted-foreground">{sentDt}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono font-bold text-primary text-lg">
              {Number(coupon.combined_odds).toFixed(2)}x
            </span>
            {coupon.result === "win" ? (
              <Badge variant="success" className="shadow-lg shadow-success/20">✅ Nyertes</Badge>
            ) : coupon.result === "loss" ? (
              <Badge variant="destructive" className="shadow-lg shadow-destructive/20">❌ Vesztes</Badge>
            ) : (
              <Badge variant="warning" className="shadow-lg shadow-warning/20">⏳ Folyamatban</Badge>
            )}
          </div>
        </div>
        <div className="flex flex-col">
          {coupon.picks.map((p, i) => (
            <PickRow key={i} pick={p} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function SportBreakdown({ coupons }: { coupons: Coupon[] }) {
  const map: Record<string, { wins: number; total: number; pending: number }> = {};

  for (const c of coupons) {
    for (const p of c.picks) {
      const sport = p.sport;
      if (!map[sport]) map[sport] = { wins: 0, total: 0, pending: 0 };
      if (c.result === "win") map[sport].wins++;
      if (c.result != null) map[sport].total++;
      else map[sport].pending++;
    }
  }

  const entries = Object.entries(map).sort((a, b) => (b[1].total + b[1].pending) - (a[1].total + a[1].pending));
  if (entries.length === 0) return null;

  return (
    <Card className="glass-card h-full flex flex-col">
      <CardHeader className="pb-4 border-b border-border/50">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-primary" />
          <CardTitle>Sportág bontás</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="divide-y divide-border/50">
          {entries.map(([sport, s]) => {
            const emoji = SPORT_EMOJI[sport] ?? "🏅";
            const pct = s.total > 0 ? (s.wins / s.total) * 100 : 0;
            return (
              <div key={sport} className="p-5 hover:bg-secondary/30 transition-colors">
                <div className="flex justify-between items-center mb-3">
                  <h4 className="font-semibold capitalize flex items-center gap-1">
                    {emoji} {sport}
                  </h4>
                  <div className="text-right">
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

export default function CouponDashboard() {
  const { data: allCoupons = [], isLoading, isError, refetch, isFetching } = useAllCoupons();
  const [selectedMonth, setSelectedMonth] = useState<MonthKey>("all");

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

  if (isError) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="max-w-md w-full border-destructive/50 bg-destructive/5">
          <CardContent className="pt-6 flex flex-col items-center text-center space-y-4">
            <XCircle className="w-12 h-12 text-destructive" />
            <div className="space-y-2">
              <h3 className="text-xl font-bold">Hiba történt</h3>
              <p className="text-muted-foreground">Nem sikerült betölteni a statisztikákat.</p>
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

  const months = buildMonthKeys(allCoupons.map((c) => c.sent_at));
  const filtered = allCoupons.filter((c) => isInMonth(c.sent_at, selectedMonth));
  const { total, settled, wins, losses, pending, winRate, roi, avgOdds } = computeCouponStats(filtered);

  const statCards = [
    { title: "Összes szelvény", value: total, icon: Target, color: "text-blue-500", bg: "bg-blue-500/10" },
    { title: "Nyerési arány", value: settled > 0 ? formatPercentage(winRate) : "—", icon: Trophy, color: "text-yellow-500", bg: "bg-yellow-500/10" },
    { title: "ROI", value: settled > 0 ? formatROI(roi) : "—", icon: TrendingUp, color: roi >= 0 ? "text-success" : "text-destructive", bg: roi >= 0 ? "bg-success/10" : "bg-destructive/10" },
    { title: "Átlag szorzó", value: avgOdds != null ? avgOdds.toFixed(2) : "—", icon: BarChart3, color: "text-purple-400", bg: "bg-purple-400/10" },
    { title: "Nyertes", value: wins, icon: CheckCircle2, color: "text-success", bg: "bg-success/10" },
    { title: "Vesztes", value: losses, icon: XCircle, color: "text-destructive", bg: "bg-destructive/10" },
    { title: "Folyamatban", value: pending, icon: Clock, color: "text-warning", bg: "bg-warning/10" },
  ];

  return (
    <div className="min-h-screen p-4 md:p-8 max-w-[1600px] mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-bold font-display bg-gradient-to-r from-white to-white/60 bg-clip-text text-transparent">
            Szelvény Bot Statisztikák
          </h1>
          <p className="text-muted-foreground mt-1 flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            ~2.0x kombinált szorzó · 2-3 meccs/szelvény
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
            href={`${import.meta.env.BASE_URL || "/"}corners`}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-card-border hover:border-primary/50 hover:bg-secondary transition-all shadow-sm text-sm font-medium"
          >
            ⚽ Szöglet →
          </a>
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

      {allCoupons.length === 0 ? (
        <Card className="border-dashed border-2 bg-transparent">
          <CardContent className="flex flex-col items-center justify-center py-24 text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center">
              <Ticket className="w-8 h-8 text-muted-foreground" />
            </div>
            <h3 className="text-xl font-bold">Még nincsenek szelvények</h3>
            <p className="text-muted-foreground max-w-md">
              A bot még nem küldött szelvényt. Küldj <span className="font-mono text-primary">/szelveny</span> parancsot Telegramon, vagy várj az automatikus keresésre.
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

          {total === 0 ? (
            <Card className="border-dashed border-2 bg-transparent">
              <CardContent className="flex flex-col items-center justify-center py-16 text-center space-y-3">
                <div className="w-12 h-12 rounded-full bg-secondary flex items-center justify-center">
                  <Ticket className="w-6 h-6 text-muted-foreground" />
                </div>
                <h3 className="text-lg font-bold">Ebben a hónapban nincs szelvény</h3>
                <p className="text-muted-foreground text-sm">Válassz másik hónapot, vagy az "Összes" nézetet.</p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
              {/* Coupon list */}
              <div className="xl:col-span-2 flex flex-col gap-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Ticket className="w-5 h-5 text-primary" />
                    <h2 className="font-bold text-lg">Szelvények ({total})</h2>
                  </div>
                  <Badge variant="secondary" className="font-mono">{total} db</Badge>
                </div>
                {filtered.map((c) => (
                  <CouponCard key={c.id} coupon={c} />
                ))}
              </div>

              {/* Sport breakdown */}
              <div className="xl:col-span-1">
                <SportBreakdown coupons={filtered} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
