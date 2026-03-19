import { cn } from "@/lib/utils";
import { format } from "date-fns";
import { hu } from "date-fns/locale";
import { ChevronLeft, ChevronRight, CalendarDays } from "lucide-react";
import { useRef } from "react";

export type MonthKey = "all" | string;

export function buildMonthKeys(timestamps: number[]): string[] {
  const set = new Set<string>();
  for (const ts of timestamps) {
    const d = new Date(ts * 1000);
    set.add(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
  }
  return [...set].sort((a, b) => b.localeCompare(a));
}

export function monthLabel(key: string): string {
  const [year, month] = key.split("-").map(Number);
  const d = new Date(year, month - 1, 1);
  return format(d, "yyyy. MMMM", { locale: hu });
}

export function isInMonth(ts: number, key: MonthKey): boolean {
  if (key === "all") return true;
  const d = new Date(ts * 1000);
  const m = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  return m === key;
}

interface MonthPickerProps {
  months: string[];
  selected: MonthKey;
  onChange: (key: MonthKey) => void;
}

export function MonthPicker({ months, selected, onChange }: MonthPickerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  if (months.length === 0) return null;

  const scroll = (dir: "left" | "right") => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollBy({ left: dir === "left" ? -160 : 160, behavior: "smooth" });
  };

  return (
    <div className="flex items-center gap-2 w-full">
      <CalendarDays className="w-4 h-4 text-muted-foreground shrink-0" />
      {months.length > 3 && (
        <button
          onClick={() => scroll("left")}
          className="p-1.5 rounded-lg hover:bg-secondary transition-colors shrink-0"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      )}
      <div
        ref={scrollRef}
        className="flex gap-2 overflow-x-auto scrollbar-hide flex-1"
        style={{ scrollbarWidth: "none" }}
      >
        <button
          onClick={() => onChange("all")}
          className={cn(
            "shrink-0 px-3 py-1.5 rounded-lg text-sm font-medium transition-all border",
            selected === "all"
              ? "bg-primary text-primary-foreground border-primary shadow-lg shadow-primary/20"
              : "bg-card border-card-border hover:border-primary/50 hover:bg-secondary text-muted-foreground"
          )}
        >
          Összes
        </button>
        {months.map((m) => (
          <button
            key={m}
            onClick={() => onChange(m)}
            className={cn(
              "shrink-0 px-3 py-1.5 rounded-lg text-sm font-medium transition-all border whitespace-nowrap",
              selected === m
                ? "bg-primary text-primary-foreground border-primary shadow-lg shadow-primary/20"
                : "bg-card border-card-border hover:border-primary/50 hover:bg-secondary text-muted-foreground"
            )}
          >
            {monthLabel(m)}
          </button>
        ))}
      </div>
      {months.length > 3 && (
        <button
          onClick={() => scroll("right")}
          className="p-1.5 rounded-lg hover:bg-secondary transition-colors shrink-0"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
