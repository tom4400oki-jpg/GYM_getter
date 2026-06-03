import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium leading-none whitespace-nowrap",
  {
    variants: {
      variant: {
        available: "bg-emerald-100 text-emerald-700",
        partial: "bg-amber-100 text-amber-700",
        open: "bg-sky-100 text-sky-700",
        occupied: "bg-slate-200 text-slate-600",
        muted: "bg-slate-100 text-slate-400",
      },
    },
    defaultVariants: {
      variant: "muted",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
