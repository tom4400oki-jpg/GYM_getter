export type StatusMeta = {
  sym: string;
  label: string;
  variant: "available" | "partial" | "open" | "occupied" | "muted";
};

export const STATUS_META: Record<string, StatusMeta> = {
  available: { sym: "○", label: "空き", variant: "available" },
  partial: { sym: "△", label: "一部空き", variant: "partial" },
  open: { sym: "◎", label: "一般開放", variant: "open" },
  occupied: { sym: "×", label: "予約あり", variant: "occupied" },
  closed: { sym: "保", label: "休館・保守", variant: "muted" },
  rain: { sym: "雨", label: "雨天", variant: "muted" },
  cancelling: { sym: "取", label: "取消中", variant: "muted" },
  planned: { sym: "予", label: "開放予定", variant: "muted" },
  out_of_period: { sym: "−", label: "受付期間外", variant: "muted" },
  unknown: { sym: "−", label: "−", variant: "muted" },
};

export function meta(status: string): StatusMeta {
  return STATUS_META[status] ?? STATUS_META.unknown;
}
