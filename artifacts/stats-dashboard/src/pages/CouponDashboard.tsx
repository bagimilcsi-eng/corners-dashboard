import { useCouponStats, type Coupon, type CouponPick } from "@/hooks/use-coupons";
import { format } from "date-fns";
import { hu } from "date-fns/locale";

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

function StatCard({ label, value, sub, color }: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="bg-white rounded-2xl shadow p-5 flex flex-col gap-1">
      <span className="text-xs text-gray-500 font-medium uppercase tracking-wide">{label}</span>
      <span className={`text-3xl font-bold ${color ?? "text-gray-800"}`}>{value}</span>
      {sub && <span className="text-sm text-gray-400">{sub}</span>}
    </div>
  );
}

function PickRow({ pick }: { pick: CouponPick }) {
  const emoji = SPORT_EMOJI[pick.sport] ?? "🏅";
  const dt = format(new Date(pick.start_timestamp * 1000), "MM.dd HH:mm", { locale: hu });
  let badge = null;
  if (pick.result === "win") badge = <span className="px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold">✅ NYE</span>;
  else if (pick.result === "loss") badge = <span className="px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-bold">❌ VES</span>;
  else badge = <span className="px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700 text-xs font-bold">⏳</span>;

  return (
    <div className="flex items-center justify-between gap-2 py-1.5 border-b border-gray-50 last:border-0">
      <div className="flex items-center gap-2 min-w-0">
        <span>{emoji}</span>
        <div className="min-w-0">
          <span className="font-semibold text-gray-800 text-sm">{pick.pick_name}</span>
          {pick.sofa_confirmed && <span className="ml-1 text-xs text-green-600">✔</span>}
          <p className="text-xs text-gray-400 truncate">{pick.home} vs {pick.away} · {dt}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="font-mono text-sm font-bold text-blue-700">@{Number(pick.odds).toFixed(2)}</span>
        {badge}
      </div>
    </div>
  );
}

function CouponCard({ coupon }: { coupon: Coupon }) {
  const sentDt = format(new Date(coupon.sent_at * 1000), "MM.dd HH:mm", { locale: hu });
  let resultBadge = null;
  if (coupon.result === "win") resultBadge = <span className="px-3 py-1 rounded-full bg-green-100 text-green-700 font-bold text-sm">✅ NYERT</span>;
  else if (coupon.result === "loss") resultBadge = <span className="px-3 py-1 rounded-full bg-red-100 text-red-700 font-bold text-sm">❌ VESZETT</span>;
  else resultBadge = <span className="px-3 py-1 rounded-full bg-yellow-100 text-yellow-700 font-bold text-sm">⏳ Folyamatban</span>;

  return (
    <div className="bg-white rounded-2xl shadow p-5 flex flex-col gap-3 border border-gray-100">
      <div className="flex items-center justify-between">
        <div>
          <span className="font-bold text-gray-700 text-lg">#{String(coupon.coupon_number).padStart(3, "0")}</span>
          <span className="ml-2 text-xs text-gray-400">{sentDt}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono font-bold text-blue-700 text-lg">
            {Number(coupon.combined_odds).toFixed(2)}x
          </span>
          {resultBadge}
        </div>
      </div>
      <div className="flex flex-col">
        {coupon.picks.map((p, i) => <PickRow key={i} pick={p} />)}
      </div>
    </div>
  );
}

export default function CouponDashboard() {
  const { data, isLoading, error } = useCouponStats();

  if (isLoading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Betöltés...</div>;
  }
  if (error || !data) {
    return <div className="flex items-center justify-center h-64 text-red-400">Hiba: {String(error)}</div>;
  }

  const { total, settled, wins, losses, pending, winRate, roi, avgOdds, recentCoupons } = data;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">🎯 Szelvény Bot Statisztika</h1>
          <p className="text-xs text-gray-400 mt-0.5">~2.0x kombinált szorzó · 2-3 meccs/szelvény</p>
        </div>
        <div className="flex gap-3 text-sm">
          <a href={`${import.meta.env.BASE_URL || "/"}corners`} className="text-blue-600 hover:underline">⚽ Szöglet →</a>
          <a href={import.meta.env.BASE_URL || "/"} className="text-blue-600 hover:underline">🏓 Asztalitenisz →</a>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6 flex flex-col gap-6">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
          <StatCard label="Összes" value={total} sub={`${pending} folyamatban`} />
          <StatCard
            label="Nyerési arány"
            value={settled > 0 ? `${winRate}%` : "—"}
            sub={`${wins}W / ${losses}L`}
            color={settled === 0 ? "text-gray-400" : wins / settled >= 0.5 ? "text-green-600" : "text-red-500"}
          />
          <StatCard
            label="ROI"
            value={settled > 0 ? `${roi}%` : "—"}
            sub="lezárt szelvények"
            color={settled === 0 ? "text-gray-400" : roi >= 0 ? "text-green-600" : "text-red-500"}
          />
          <StatCard
            label="Átlag szorzó"
            value={avgOdds != null ? avgOdds.toFixed(2) : "—"}
            sub="kombinált"
            color="text-purple-600"
          />
          <StatCard label="Nyertes" value={wins} color="text-green-600" />
          <StatCard label="Vesztes" value={losses} color="text-red-500" />
        </div>

        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Szelvények ({total})
          </h2>
          {recentCoupons.length === 0 ? (
            <div className="text-center text-gray-400 py-16">
              Még nincs szelvény. A bot automatikusan keresi a megfelelő meccseket.
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {recentCoupons.map((c) => (
                <CouponCard key={c.id} coupon={c} />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
