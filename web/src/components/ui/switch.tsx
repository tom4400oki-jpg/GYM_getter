import { cn } from "@/lib/utils";

interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  id?: string;
}

export function Switch({ checked, onCheckedChange, id }: SwitchProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-4 w-7 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        checked ? "bg-emerald-500" : "bg-slate-300"
      )}
    >
      <span
        className={cn(
          "inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-3.5" : "translate-x-0.5"
        )}
      />
    </button>
  );
}
