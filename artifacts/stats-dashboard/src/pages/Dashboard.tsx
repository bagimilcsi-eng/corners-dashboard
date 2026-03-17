import React from "react";
import { useTipStats } from "@/hooks/use-tips";
import { format } from "date-fns";
import { hu } from "date-fns/locale";
import { motion } from "framer-motion";
import { 
  Trophy, 
  TrendingUp, 
  Target, 
  Clock, 
  Activity,
  CheckCircle2,
  XCircle,
  AlertCircle,
  RefreshCcw,
  BarChart3,
  TableProperties
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn, formatOdds, formatPercentage, formatROI } from "@/lib/utils";

const containerVariants = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1
    }
  }
};

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } }
};

export default function Dashboard() {
  const { data, isLoading, isError, refetch, isFetching } = useTipStats();

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

  if (isError || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card className="max-w-md w-full border-destructive/50 bg-destructive/5">
          <CardContent className="pt-6 flex flex-col items-center text-center space-y-4">
            <XCircle className="w-12 h-12 text-destructive" />
            <div className="space-y-2">
              <h3 className="text-xl font-bold">Hiba történt</h3>
              <p className="text-muted-foreground">Nem sikerült betölteni a statisztikákat. Kérjük, próbálja újra később.</p>
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

  const { total, settled, wins, losses, pending, winRate, roi, leagueStats, recentTips } = data;

  const statCards = [
    { title: "Összes tipp", value: total, icon: Target, color: "text-blue-500", bg: "bg-blue-500/10" },
    { title: "Nyerési arány", value: formatPercentage(winRate), icon: Trophy, color: "text-yellow-500", bg: "bg-yellow-500/10" },
    { title: "ROI", value: formatROI(roi), icon: TrendingUp, color: roi >= 0 ? "text-success" : "text-destructive", bg: roi >= 0 ? "bg-success/10" : "bg-destructive/10" },
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
            Asztalitenisz Tipp Statisztikák
          </h1>
          <p className="text-muted-foreground mt-1 flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            Élő adatok a SofaScore alapján
          </p>
        </div>
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

      {total === 0 ? (
        <Card className="border-dashed border-2 bg-transparent">
          <CardContent className="flex flex-col items-center justify-center py-24 text-center space-y-4">
            <div className="w-16 h-16 rounded-full bg-secondary flex items-center justify-center">
              <BarChart3 className="w-8 h-8 text-muted-foreground" />
            </div>
            <h3 className="text-xl font-bold">Még nincsenek tippek</h3>
            <p className="text-muted-foreground max-w-md">
              A bot még nem küldött egyetlen tippet sem. Amint új tippek érkeznek a feltételeknek megfelelően, itt fognak megjelenni.
            </p>
          </CardContent>
        </Card>
      ) : (
        <motion.div 
          variants={containerVariants}
          initial="hidden"
          animate="show"
          className="space-y-8"
        >
          {/* Top Stats Grid */}
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
            {statCards.map((stat, idx) => (
              <motion.div key={idx} variants={itemVariants}>
                <Card className="glass-card hover:-translate-y-1 transition-transform duration-300">
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
              </motion.div>
            ))}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
            {/* Recent Tips Table */}
            <motion.div variants={itemVariants} className="xl:col-span-2">
              <Card className="glass-card h-full flex flex-col">
                <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <TableProperties className="w-5 h-5 text-primary" />
                    <CardTitle>Legutóbbi Tippek</CardTitle>
                  </div>
                  <Badge variant="secondary" className="font-mono">{recentTips.length} utolsó</Badge>
                </CardHeader>
                <CardContent className="p-0 flex-1 overflow-auto">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                      <thead className="text-xs text-muted-foreground bg-secondary/50 uppercase border-b border-border/50">
                        <tr>
                          <th className="px-6 py-4 font-medium">Mérkőzés / Liga</th>
                          <th className="px-6 py-4 font-medium">Tippelt</th>
                          <th className="px-6 py-4 font-medium">Odds</th>
                          <th className="px-6 py-4 font-medium">Időpont</th>
                          <th className="px-6 py-4 font-medium text-right">Eredmény</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/50">
                        {recentTips.map((tip) => (
                          <tr key={tip.event_id} className="hover:bg-secondary/30 transition-colors group">
                            <td className="px-6 py-4">
                              <div className="font-medium text-foreground">{tip.home} <span className="text-muted-foreground font-normal mx-1">vs</span> {tip.away}</div>
                              <div className="text-xs text-muted-foreground mt-1">{tip.league}</div>
                            </td>
                            <td className="px-6 py-4">
                              <div className="font-medium">{tip.predicted_name}</div>
                              <div className="text-xs text-muted-foreground uppercase tracking-wider">{tip.predicted}</div>
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
                              {tip.result === 'win' ? (
                                <Badge variant="success" className="shadow-lg shadow-success/20">Nyertes</Badge>
                              ) : tip.result === 'loss' ? (
                                <Badge variant="destructive" className="shadow-lg shadow-destructive/20">Vesztes</Badge>
                              ) : (
                                <Badge variant="warning" className="shadow-lg shadow-warning/20">Folyamatban</Badge>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </motion.div>

            {/* League Breakdown */}
            <motion.div variants={itemVariants} className="xl:col-span-1">
              <Card className="glass-card h-full flex flex-col">
                <CardHeader className="pb-4 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <BarChart3 className="w-5 h-5 text-primary" />
                    <CardTitle>Liga Statisztikák</CardTitle>
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="divide-y divide-border/50">
                    {Object.entries(leagueStats)
                      .sort(([, a], [, b]) => (b.wins + b.losses) - (a.wins + a.losses))
                      .map(([league, stats]) => {
                        const totalLeague = stats.wins + stats.losses;
                        const lgWinRate = totalLeague > 0 ? (stats.wins / totalLeague) * 100 : 0;
                        
                        return (
                          <div key={league} className="p-6 hover:bg-secondary/30 transition-colors">
                            <div className="flex justify-between items-center mb-4">
                              <h4 className="font-semibold text-lg">{league}</h4>
                              <div className="text-right">
                                <span className={cn(
                                  "text-xl font-bold font-display",
                                  lgWinRate >= 50 ? "text-success" : (lgWinRate > 0 ? "text-warning" : "text-muted-foreground")
                                )}>
                                  {totalLeague > 0 ? formatPercentage(lgWinRate) : "-"}
                                </span>
                                <p className="text-xs text-muted-foreground">Nyerési arány</p>
                              </div>
                            </div>
                            
                            <div className="flex gap-2 h-2 rounded-full overflow-hidden bg-muted">
                              {stats.wins > 0 && <div style={{ width: `${(stats.wins / (totalLeague || 1)) * 100}%` }} className="bg-success transition-all duration-1000" />}
                              {stats.losses > 0 && <div style={{ width: `${(stats.losses / (totalLeague || 1)) * 100}%` }} className="bg-destructive transition-all duration-1000" />}
                            </div>
                            
                            <div className="flex justify-between mt-3 text-sm">
                              <div className="flex items-center gap-1.5 text-success">
                                <CheckCircle2 className="w-4 h-4" />
                                <span>{stats.wins}</span>
                              </div>
                              <div className="flex items-center gap-1.5 text-destructive">
                                <XCircle className="w-4 h-4" />
                                <span>{stats.losses}</span>
                              </div>
                              <div className="flex items-center gap-1.5 text-warning">
                                <Clock className="w-4 h-4" />
                                <span>{stats.pending}</span>
                              </div>
                            </div>
                          </div>
                        );
                    })}
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        </motion.div>
      )}
    </div>
  );
}
