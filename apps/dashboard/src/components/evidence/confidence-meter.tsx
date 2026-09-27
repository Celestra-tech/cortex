import { formatConfidence } from "@/lib/evidence";

export function ConfidenceMeter({
  value,
  label = "Confidence",
  className = "w-24",
}: {
  value: number;
  label?: string;
  className?: string;
}) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className={`bg-muted relative h-1.5 overflow-hidden rounded-full ${className}`}
      >
        <span
          className="bg-foreground absolute inset-y-0 left-0"
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="font-mono text-xs tabular-nums">{formatConfidence(value)}</span>
    </span>
  );
}
