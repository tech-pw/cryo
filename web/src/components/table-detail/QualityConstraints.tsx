/**
 * Declarative quality rules checked against each scan's stored metadata (Deequ-style
 * constraints, Zero-Scan DQ §5.4). A rule the metadata can't decide shows Unknown with the
 * reason; missing statistics never count as a pass.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import {
  createQualityConstraint,
  deleteQualityConstraint,
  evaluateQualityConstraints,
  listQualityConstraints,
  updateQualityConstraint
} from "../../api/client";
import type { QualityConstraintCreate, QualityConstraintRead } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { useWriteControl } from "../../lib/useWriteControl";
import { useTableDetailContext } from "../../pages/table-detail/TableDetailContext";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { Select } from "../ui/select";
import { SimpleTable, type SimpleTableColumn } from "../ui/simple-table";
import { Switch } from "../ui/switch";
import { formatTime } from "../../lib/format";

type Kind = QualityConstraintCreate["kind"];

const kindLabels: Record<Kind, string> = {
  size: "Row count",
  completeness: "Completeness",
  range: "Value range",
  size_change: "Row-count change",
  freshness: "Freshness"
};

const columnKinds = new Set<Kind>(["completeness", "range"]);

const statusVariant = { passed: "success", failed: "danger", unknown: "secondary" } as const;

const pct = (value: unknown) => `${(Number(value) * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
const shown = (value: unknown, fallback: string) =>
  value === null || value === undefined || value === "" ? fallback : String(value);

export const describeConstraint = (c: Pick<QualityConstraintRead, "kind" | "params" | "column_name">) => {
  const p = c.params as Record<string, unknown>;
  switch (c.kind) {
    case "size":
      return `Live rows between ${shown(p.min_rows, "0")} and ${shown(p.max_rows, "∞")}`;
    case "completeness":
      return `${c.column_name} at least ${pct(p.min)} non-null`;
    case "range":
      return `${c.column_name} within [${shown(p.min, "−∞")}, ${shown(p.max, "∞")}]`;
    case "size_change": {
      const parts = [];
      if (p.max_decrease !== undefined && p.max_decrease !== null) parts.push(`drop at most ${pct(p.max_decrease)}`);
      if (p.max_increase !== undefined && p.max_increase !== null) parts.push(`grow at most ${pct(p.max_increase)}`);
      return `Between snapshots, live rows ${parts.join(" and ")}`;
    }
    case "freshness":
      return `New data committed within ${shown(p.max_hours, "?")}h of each scan`;
  }
};

const num = (value: string) => (value.trim() === "" ? undefined : Number(value));

export const buildParams = (kind: Kind, form: Record<string, string>): Record<string, unknown> => {
  const params: Record<string, unknown> = {};
  const put = (key: string, value: unknown) => {
    if (value !== undefined && value !== "" && !Number.isNaN(value)) params[key] = value;
  };
  if (kind === "size") {
    put("min_rows", num(form.min_rows ?? ""));
    put("max_rows", num(form.max_rows ?? ""));
  } else if (kind === "completeness") {
    const min = num(form.min_pct ?? "");
    put("min", min === undefined ? undefined : min / 100);
  } else if (kind === "range") {
    put("min", form.min?.trim());
    put("max", form.max?.trim());
  } else if (kind === "size_change") {
    const dec = num(form.dec_pct ?? "");
    const inc = num(form.inc_pct ?? "");
    put("max_decrease", dec === undefined ? undefined : dec / 100);
    put("max_increase", inc === undefined ? undefined : inc / 100);
  } else {
    put("max_hours", num(form.max_hours ?? ""));
  }
  return params;
};

const paramFields: Record<Kind, { key: string; label: string; placeholder: string }[]> = {
  size: [
    { key: "min_rows", label: "Min rows", placeholder: "1" },
    { key: "max_rows", label: "Max rows", placeholder: "optional" }
  ],
  completeness: [{ key: "min_pct", label: "Min non-null %", placeholder: "99.9" }],
  range: [
    { key: "min", label: "Min", placeholder: "0 or 2026-01-01" },
    { key: "max", label: "Max", placeholder: "optional" }
  ],
  size_change: [
    { key: "dec_pct", label: "Max drop %", placeholder: "30" },
    { key: "inc_pct", label: "Max growth %", placeholder: "optional" }
  ],
  freshness: [{ key: "max_hours", label: "Max hours", placeholder: "24" }]
};

const AddRuleForm = ({
  columns,
  onSubmit,
  pending,
  lockedReason = null
}: {
  columns: { field_id: number; name: string; type?: string | null; kinds: Kind[] }[];
  onSubmit: (body: QualityConstraintCreate) => void;
  pending: boolean;
  lockedReason?: string | null;
}) => {
  const [kind, setKind] = useState<Kind>("size");
  const [name, setName] = useState("");
  const [fieldId, setFieldId] = useState<string | undefined>();
  const [severity, setSeverity] = useState<"error" | "warn">("error");
  const [values, setValues] = useState<Record<string, string>>({});
  const eligible = useMemo(() => columns.filter((c) => c.kinds.includes(kind)), [columns, kind]);
  const needsColumn = columnKinds.has(kind);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const invalid = paramFields[kind].find((field) => {
      const raw = values[field.key] ?? "";
      if (!raw.trim()) return field.placeholder !== "optional";
      return !Number.isFinite(Number(raw)) || /[% ,]/.test(raw);
    });
    if (invalid) {
      toast.error(`${invalid.label} must be a plain number`);
      return;
    }
    onSubmit({
      name: name.trim() || kindLabels[kind],
      kind,
      field_id: needsColumn && fieldId ? Number(fieldId) : null,
      params: buildParams(kind, values),
      severity,
      enabled: true
    });
  };

  return (
    <form className="flex flex-wrap items-end gap-3" onSubmit={submit} aria-label="Add quality rule">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Rule
        <Select
          value={kind}
          onChange={(value) => {
            setKind((value as Kind) ?? "size");
            setValues({});
            setFieldId(undefined);
          }}
          options={(Object.keys(kindLabels) as Kind[]).map((k) => ({ value: k, label: kindLabels[k] }))}
        />
      </label>
      {needsColumn ? (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Column
          <Select
            value={fieldId}
            onChange={setFieldId}
            placeholder={eligible.length ? "Choose a column" : "No eligible columns"}
            options={eligible.map((c) => ({ value: String(c.field_id), label: `${c.name} (${c.type ?? "?"})` }))}
          />
        </label>
      ) : null}
      {paramFields[kind].map((field) => (
        <label key={field.key} className="flex flex-col gap-1 text-xs text-muted-foreground">
          {field.label}
          <Input
            className="w-32"
            value={values[field.key] ?? ""}
            placeholder={field.placeholder}
            onChange={(event) => setValues((prev) => ({ ...prev, [field.key]: event.target.value }))}
          />
        </label>
      ))}
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Name
        <Input className="w-44" value={name} placeholder={kindLabels[kind]} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        On failure
        <Select
          value={severity}
          onChange={(value) => setSeverity(value === "warn" ? "warn" : "error")}
          options={[
            { value: "error", label: "Alert" },
            { value: "warn", label: "Show only" }
          ]}
        />
      </label>
      <Button
        type="submit"
        size="sm"
        variant="primary"
        loading={pending}
        disabled={Boolean(lockedReason) || (needsColumn && !fieldId)}
        title={lockedReason ?? undefined}
      >
        Add rule
      </Button>
      {lockedReason ? <span className="text-[11px] text-muted-foreground">{lockedReason}</span> : null}
    </form>
  );
};

export const QualityConstraints = () => {
  const { tableId } = useTableDetailContext();
  const constraintAccess = useWriteControl("qualityConstraint");
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const key = ["quality-constraints", tableId];
  const [adding, setAdding] = useState(false);

  const rules = useQuery({
    queryKey: key,
    queryFn: () => listQualityConstraints(api, tableId),
    enabled: Boolean(tableId)
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: key });
  const onError = (error: Error) => toast.error(error.message);

  const create = useMutation({
    mutationFn: (body: QualityConstraintCreate) => createQualityConstraint(api, tableId, body),
    onSuccess: (rule) => {
      toast.success(`Added "${rule.name}": ${rule.latest?.status ?? "not evaluated"}`);
      setAdding(false);
      refresh();
    },
    onError
  });
  const toggle = useMutation({
    mutationFn: (rule: QualityConstraintRead) => updateQualityConstraint(api, rule.id, { enabled: !rule.enabled }),
    onSuccess: (updated) => {
      queryClient.setQueryData(key, (current: { constraints: QualityConstraintRead[] } | undefined) => {
        if (!current) return current;
        return {
          ...current,
          constraints: current.constraints.map((row) =>
            row.id === updated.id ? { ...row, enabled: updated.enabled } : row
          )
        };
      });
      refresh();
    },
    onError
  });
  const remove = useMutation({
    mutationFn: (rule: QualityConstraintRead) => deleteQualityConstraint(api, rule.id),
    onSuccess: refresh,
    onError
  });
  const evaluate = useMutation({
    mutationFn: () => evaluateQualityConstraints(api, tableId),
    onSuccess: (data) => queryClient.setQueryData(key, data),
    onError
  });

  const columns: SimpleTableColumn<QualityConstraintRead>[] = [
    {
      title: "Rule",
      key: "rule",
      render: (_, rule) => (
        <div className="flex flex-col">
          <span className="font-medium">{rule.name}</span>
          <span className="text-xs text-muted-foreground">{describeConstraint(rule)}</span>
        </div>
      )
    },
    {
      title: "Status",
      key: "status",
      render: (_, rule) =>
        !rule.enabled ? (
          <Badge variant="outline">Disabled</Badge>
        ) : rule.latest ? (
          <div className="flex max-w-sm flex-col gap-1">
            <Badge variant={statusVariant[rule.latest.status]} className="w-fit capitalize">
              {rule.latest.status}
            </Badge>
            {rule.latest.reason ? <span className="text-xs text-muted-foreground">{rule.latest.reason}</span> : null}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">Not evaluated</span>
        )
    },
    {
      title: "As of scan",
      key: "scanned",
      render: (_, rule) => (
        <span className="text-xs tabular-nums">{rule.latest?.scanned_at ? formatTime(rule.latest.scanned_at) : "—"}</span>
      )
    },
    {
      title: "On failure",
      key: "severity",
      render: (_, rule) => (rule.severity === "error" ? "Alert" : "Show only")
    },
    {
      title: "",
      key: "actions",
      align: "right",
      render: (_, rule) => (
        <div className="flex items-center justify-end gap-2">
            <Switch
              checked={rule.enabled}
              disabled={!constraintAccess.allowed || (toggle.isPending && toggle.variables?.id === rule.id)}
              aria-label={rule.enabled ? `Disable ${rule.name}` : `Enable ${rule.name}`}
              title={constraintAccess.reason ?? undefined}
              onCheckedChange={() => {
                if (!constraintAccess.allowed) return;
                toggle.mutate(rule);
              }}
            />
            <Button
              size="icon"
              variant="ghost"
              aria-label={`Delete ${rule.name}`}
              disabled={!constraintAccess.allowed}
              title={constraintAccess.reason ?? undefined}
              onClick={() => {
                if (!constraintAccess.allowed) return;
                remove.mutate(rule);
              }}
            >
            <Trash2 aria-hidden="true" className="size-4" />
          </Button>
          {constraintAccess.reason ? (
            <span className="max-w-40 text-[11px] text-muted-foreground">{constraintAccess.reason}</span>
          ) : null}
        </div>
      )
    }
  ];

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ListChecks aria-hidden="true" className="size-4" />
          Quality rules
          <Badge variant="default">Zero scan</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Checked against the metadata each scan already collects, so no data is read. When the metadata
          can&apos;t decide a rule, it shows Unknown and says why.
        </p>
        {rules.isError ? (
          <p className="text-sm text-destructive">
            Couldn't load quality rules.{" "}
            <button type="button" className="underline" onClick={() => void rules.refetch()}>
              Retry
            </button>
          </p>
        ) : (
        <SimpleTable
          size="small"
          columns={columns}
          dataSource={rules.data?.constraints ?? []}
          rowKey={(rule) => rule.id}
          loading={rules.isLoading}
          emptyTitle="No rules yet"
          emptyDescription="Add a rule for row counts, null rates, value ranges or freshness."
        />
        )}
        {adding ? (
          <AddRuleForm
            columns={(rules.data?.columns ?? []) as { field_id: number; name: string; type?: string | null; kinds: Kind[] }[]}
            onSubmit={(body) => {
              if (!constraintAccess.allowed) return;
              create.mutate(body);
            }}
            pending={create.isPending}
            lockedReason={constraintAccess.reason}
          />
        ) : null}
        <div className="flex gap-2">
          <Button
            size="sm"
            variant={adding ? "ghost" : "secondary"}
            disabled={!adding && !constraintAccess.allowed}
            title={!adding ? constraintAccess.reason ?? undefined : undefined}
            onClick={() => {
              if (!constraintAccess.allowed && !adding) return;
              setAdding((v) => !v);
            }}
          >
            {adding ? "Cancel" : "Add rule"}
          </Button>
          {!adding && constraintAccess.reason ? (
            <span className="text-[11px] text-muted-foreground">{constraintAccess.reason}</span>
          ) : null}
          {rules.data?.constraints.length ? (
            <Button
              size="sm"
              variant="ghost"
              loading={evaluate.isPending}
              disabled={!constraintAccess.allowed}
              title={constraintAccess.reason ?? undefined}
              onClick={() => {
                if (!constraintAccess.allowed) return;
                evaluate.mutate();
              }}
            >
              Re-check against latest scan
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
};
