import { useCornerTips, type CornerTip } from "@/hooks/use-corner-tips";
import { formatDistanceToNow } from "date-fns";
import { hu } from "date-fns/locale";

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="bg-white rounded-2xl shadow p-5 flex flex-col gap-1">
      <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">
        {label}
      </span>
      <span
        className={`text-3xl font-bold ${color ?? "text-gray-800"}`}
      >
        {value}
      </span>
      {sub && <span className="text-sm text-gray-400">{sub}</span>}
    </div>
  );
}

function getStrength(expected: number): { icon: string; label: string; color: string } {
  const margin = Math.abs(expected - 9.5);
  if (margin >= 2.5) return { icon: "⚡⚡⚡", label: "Nagyon erős", color: "text-red-600" };
  if (margin >= 1.5) return { icon: "⚡⚡", label: "Erős", color: "text-orange-500" };
  return { icon: "⚡", label: "Mérsékelt", color: "text-yellow-500" };
}

function TipRow({ tip }: { tip: CornerTip }) {
  const startDt = new Date(tip.start_timestamp * 1000);
  const timeStr = startDt.toLocaleTimeString("hu-HU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Budapest",
  });
  const dateStr = startDt.toLocaleDateString("hu-HU", {
    month: "numeric",
    day: "numeric",
    timeZone: "Europe/Budapest",
  });

  const tipIcon = tip.tip === "over" ? "⬆️" : "⬇️";
  const tipLabel = tip.tip === "over" ? "OVER" : "UNDER";

  let resultBadge = null;
  if (tip.result === "win") {
    resultBadge = (
      <span className="px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold">
        ✅ NYERT
      </span>
    );
  } else if (tip.result === "loss") {
    resultBadge = (
      <span className="px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-bold">
        ❌ VESZETT
      </span>
    );
  } else {
    resultBadge = (
      <span className="px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700 text-xs font-bold">
        ⏳ Folyamatban
      </span>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-sm p-4 flex flex-col gap-2 border border-gray-100">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs text-gray-400 font-medium">
          🏆 {tip.league}
        </span>
        {resultBadge}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold text-gray-800">
          {tip.home} vs {tip.away}
        </span>
        <span className="text-xs text-gray-400">
          🕐 {dateStr} {timeStr}
        </span>
      </div>
      <div className="flex items-center gap-4 flex-wrap text-sm">
        <span className="font-bold text-blue-700">
          {tipIcon} {tipLabel} {tip.line}
        </span>
        <span className="text-gray-500">
          Várható: <span className="font-semibold text-gray-700">{Number(tip.expected_corners).toFixed(1)}</span> szöglet
        </span>
        {tip.home_avg != null && tip.away_avg != null && (
          <span className="text-gray-400 text-xs">
            (hazai: {Number(tip.home_avg).toFixed(1)} | vendég: {Number(tip.away_avg).toFixed(1)})
          </span>
        )}
        {tip.result != null && tip.actual_corners != null && (
          <span className="text-gray-500">
            Valós: <span className="font-semibold">{tip.actual_corners}</span>
          </span>
        )}
      </div>
      <div className="text-xs">
        {(() => {
          const s = getStrength(Number(tip.expected_corners));
          return (
            <span className={`font-semibold ${s.color}`}>
              {s.icon} {s.label}
            </span>
          );
        })()}
      </div>
    </div>
  );
}

function LeagueBreakdown({ tips }: { tips: CornerTip[] }) {
  const settled = tips.filter((t) => t.result != null);
  const map: Record<string, { wins: number; total: number; pending: number }> =
    {};

  for (const t of tips) {
    if (!map[t.league]) map[t.league] = { wins: 0, total: 0, pending: 0 };
    if (t.result === "win") map[t.league].wins++;
    if (t.result != null) map[t.league].total++;
    else map[t.league].pending++;
  }

  const entries = Object.entries(map).sort((a, b) => b[1].total - a[1].total);
  if (entries.length === 0) return null;

  return (
    <div className="bg-white rounded-2xl shadow p-5">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Bajnokságonként
      </h2>
      <div className="flex flex-col gap-2">
        {entries.map(([league, s]) => {
          const pct = s.total > 0 ? Math.round((s.wins / s.total) * 100) : null;
          return (
            <div
              key={league}
              className="flex items-center justify-between text-sm"
            >
              <span className="text-gray-700 truncate max-w-[200px]">
                🏆 {league}
              </span>
              <span className="text-gray-500 shrink-0">
                {s.wins}W / {s.total - s.wins}L
                {pct != null ? ` (${pct}%)` : ""}
                {s.pending > 0 && (
                  <span className="text-yellow-500 ml-1">⏳{s.pending}</span>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function CornersDashboard() {
  const { data: tips = [], isLoading, error } = useCornerTips();

  const settled = tips.filter((t) => t.result != null);
  const pending = tips.filter((t) => t.result == null);
  const wins = settled.filter((t) => t.result === "win").length;
  const losses = settled.length - wins;
  const winRate =
    settled.length > 0 ? ((wins / settled.length) * 100).toFixed(1) : "—";

  const overTips = settled.filter((t) => t.tip === "over");
  const underTips = settled.filter((t) => t.tip === "under");
  const overWins = overTips.filter((t) => t.result === "win").length;
  const underWins = underTips.filter((t) => t.result === "win").length;

  const oddsSettled = settled.filter((t) => t.odds != null);
  const avgOdds =
    oddsSettled.length > 0
      ? (oddsSettled.reduce((s, t) => s + Number(t.odds), 0) / oddsSettled.length).toFixed(2)
      : null;

  let roiSum = 0;
  for (const t of settled) {
    const o = t.odds != null ? Number(t.odds) : 1.62;
    roiSum += t.result === "win" ? o - 1 : -1;
  }
  const roi = settled.length > 0 ? ((roiSum / settled.length) * 100).toFixed(1) : null;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400">
        Betöltés...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400">
        Hiba: {String(error)}
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">⚽ Szöglet Bot Statisztika</h1>
          <p className="text-xs text-gray-400 mt-0.5">Over/Under 9.5 szöglet tippek</p>
        </div>
        <a
          href={import.meta.env.BASE_URL || "/"}
          className="text-sm text-blue-600 hover:underline"
        >
          🏓 Asztalitenisz →
        </a>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6 flex flex-col gap-6">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          <StatCard
            label="Összes tipp"
            value={tips.length}
            sub={`${settled.length} lezárt, ${pending.length} folyamatban`}
          />
          <StatCard
            label="Nyerési arány"
            value={settled.length > 0 ? `${winRate}%` : "—"}
            sub={`${wins}W / ${losses}L`}
            color={
              settled.length === 0
                ? "text-gray-400"
                : wins / settled.length >= 0.5
                ? "text-green-600"
                : "text-red-500"
            }
          />
          <StatCard
            label="ROI"
            value={roi != null ? `${roi}%` : "—"}
            sub="lezárt tippek alapján"
            color={
              roi == null
                ? "text-gray-400"
                : Number(roi) >= 0
                ? "text-green-600"
                : "text-red-500"
            }
          />
          <StatCard
            label="Átlag szorzó"
            value={avgOdds != null ? avgOdds : "—"}
            sub="lezárt tippek"
            color="text-purple-600"
          />
          <StatCard
            label="Over tippek"
            value={overTips.length}
            sub={`${overWins} nyert`}
            color="text-blue-600"
          />
          <StatCard
            label="Under tippek"
            value={underTips.length}
            sub={`${underWins} nyert`}
            color="text-purple-600"
          />
        </div>

        <LeagueBreakdown tips={tips} />

        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Tippek ({tips.length})
          </h2>
          {tips.length === 0 ? (
            <div className="text-center text-gray-400 py-12">
              Még nincs szöglet tipp. A bot automatikusan keresi a meccseket.
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {tips.map((t) => (
                <TipRow key={t.event_id} tip={t} />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
