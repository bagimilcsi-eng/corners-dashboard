import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatOdds(odds: number | string | null): string {
  if (odds === null || odds === undefined) return "-";
  return Number(odds).toFixed(2);
}

export function formatPercentage(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function formatROI(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}
