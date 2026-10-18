import { ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "../lib/cn";

type MetricLine =
  | { kind: "blank" }
  | { kind: "comment"; text: string }
  | { kind: "meta"; directive: "HELP" | "TYPE"; name: string; rest: string }
  | { kind: "sample"; name: string; labels: string; value: string };

export const parseMetricLine = (line: string): MetricLine => {
  if (line.trim() === "") return { kind: "blank" };
  const help = line.match(/^#\s+(HELP)\s+(\S+)\s*(.*)$/);
  const type = line.match(/^#\s+(TYPE)\s+(\S+)\s*(.*)$/);
  const meta = help ?? type;
  if (meta) {
    return {
      kind: "meta",
      directive: meta[1] as "HELP" | "TYPE",
      name: meta[2] ?? "",
      rest: meta[3] ?? ""
    };
  }
  if (line.startsWith("#")) return { kind: "comment", text: line };
  const sample = line.match(/^([^{\s]+)(\{[^}]*\})?\s+(\S+)(?:\s+\d+)?$/);
  if (!sample) return { kind: "comment", text: line };
  return {
    kind: "sample",
    name: sample[1] ?? line,
    labels: sample[2] ?? "",
    value: sample[3] ?? ""
  };
};

const MetricRow = ({ line }: { line: MetricLine }) => {
  if (line.kind === "blank") return <div className="h-2" aria-hidden="true" />;
  if (line.kind === "comment") {
    return <div className="text-[var(--color-text-tertiary)]">{line.text}</div>;
  }
  if (line.kind === "meta") {
    return (
      <div>
        <span className="text-[var(--color-text-tertiary)]"># {line.directive} </span>
        <span className="text-[var(--color-primary)]">{line.name}</span>
        {line.rest ? <span className="text-[var(--color-text-secondary)]"> {line.rest}</span> : null}
      </div>
    );
  }
  return (
    <div className="flex items-baseline gap-3">
      <span className="min-w-0 flex-1 break-all">
        <span className="text-foreground">{line.name}</span>
        {line.labels ? <span className="text-[var(--color-text-secondary)]">{line.labels}</span> : null}
      </span>
      <span className="shrink-0 tabular-nums text-[var(--color-primary)]">{line.value}</span>
    </div>
  );
};

export const PrometheusPreview = ({ text }: { text: string }) => {
  const [open, setOpen] = useState(true);
  const lines = useMemo(() => text.replace(/\n$/, "").split("\n").map(parseMetricLine), [text]);
  const sampleCount = lines.filter((line) => line.kind === "sample").length;

  return (
    <div className="mt-3 overflow-hidden rounded-[var(--radius-lg)] border border-border bg-[var(--color-surface-sunken)]">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-foreground"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <ChevronDown
          className={cn(
            "size-3.5 text-muted-foreground transition-transform duration-[160ms] ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:transition-none",
            open && "rotate-180"
          )}
          aria-hidden="true"
        />
        Preview
        <span className="font-normal text-muted-foreground">
          {sampleCount} {sampleCount === 1 ? "sample" : "samples"}
        </span>
      </button>
      {open ? (
        <div className="max-h-80 overflow-auto border-t border-border px-3 py-2 font-mono text-xs leading-relaxed">
          {lines.map((line, index) => (
            <MetricRow key={index} line={line} />
          ))}
        </div>
      ) : null}
    </div>
  );
};
