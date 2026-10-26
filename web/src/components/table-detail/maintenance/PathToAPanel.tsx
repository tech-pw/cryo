import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { applyPathStep, getPathToA, type PathStepKind, type PathStepRead, type PathToARead } from "../../../api/pathToA";
import { useAuth } from "../../../lib/auth";
import { tableKeys } from "../../../pages/table-detail/tableKeys";
import { QueryErrorAlert } from "../../common";
import { ConfirmAction } from "../../confirm";
import { Badge, type BadgeProps } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";

const KIND_HEADINGS: Record<PathStepKind, { title: string; lede: string }> = {
  automatic: { title: "Cryo does this", lede: "Routine maintenance. Steps in the workflow run on schedule; you can also run them now." },
  approval: { title: "Needs your approval", lede: "Cryo can do these, but they change behaviour someone may rely on." },
  design: { title: "Layout changes", lede: "Admin only. They apply to new writes at once and to existing data as it is rewritten." },
  external: { title: "Outside Cryo", lede: "These need a change to the writer or a decision about the table." }
};

const KIND_ORDER: PathStepKind[] = ["automatic", "approval", "design", "external"];

const ROLE_RANK: Record<string, number> = { viewer: 0, operator: 1, admin: 2 };

const GRADE_VARIANT: Record<string, BadgeProps["variant"]> = {
  A: "gradeA",
  B: "gradeB",
  C: "gradeC",
  D: "gradeD",
  F: "gradeF"
};

const CONFIDENCE_LABEL: Record<PathStepRead["confidence"], string> = {
  measured: "Measured",
  estimated: "Estimated",
  assumed: "Assumed"
};

const actionLabel = (step: PathStepRead): string => {
  switch (step.action?.type) {
    case "run_operations":
      return "Run now";
    case "set_overrides":
      return "Save for this table";
    case "apply_properties":
      return "Apply properties";
    case "partition_spec":
      return "Change partition spec";
    case "sort_order":
      return "Set sort order";
    default:
      return "Apply";
  }
};

const GradeBadge = ({ grade, score }: { grade: string; score: number }) => (
  <Badge variant={GRADE_VARIANT[grade] ?? "secondary"} className="[font-variant-numeric:tabular-nums]">
    {grade} · {score}
  </Badge>
);

const StepAction = ({ step, tableId }: { step: PathStepRead; tableId: string }) => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState("");
  const apply = useMutation({
    mutationFn: () => applyPathStep(api, tableId, { step_id: step.id, confirm: step.confirm_text ? typed : undefined }),
    onSuccess: (result) => {
      toast.success(result.message);
      setTyped("");
      void queryClient.invalidateQueries({ queryKey: tableKeys.pathToA(tableId, token) });
      void queryClient.invalidateQueries({ queryKey: tableKeys.detail(tableId, token) });
    },
    onError: (error: unknown) => toast.error(error instanceof Error ? error.message : "Couldn't apply this step")
  });
  if (!step.action || !step.required_role) return null;
  const allowed = (ROLE_RANK[role] ?? 0) >= (ROLE_RANK[step.required_role] ?? 2);
  const label = actionLabel(step);
  if (!allowed) {
    return (
      <p className="text-xs text-muted-foreground">
        Needs the {step.required_role} role to {label.toLowerCase()}.
      </p>
    );
  }
  const typedOk = !step.confirm_text || typed === step.confirm_text;
  return (
    <ConfirmAction
      title={`${step.title}?`}
      description={
        <>
          <span className="block">{step.detail}</span>
          {step.confirm_text ? (
            <span className="mt-3 block">
              <span className="block text-foreground">
                Type <strong>{step.confirm_text}</strong> to confirm.
              </span>
              <Input
                aria-label={`Type ${step.confirm_text} to confirm`}
                className="mt-2"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                autoComplete="off"
              />
            </span>
          ) : null}
        </>
      }
      okText={label}
      okButtonProps={{ disabled: !typedOk, danger: step.kind === "design" }}
      onConfirm={async () => {
        await apply.mutateAsync();
      }}
      onCancel={() => setTyped("")}
    >
      <Button size="sm" variant={step.kind === "automatic" ? "secondary" : "primary"} disabled={apply.isPending}>
        {label}
      </Button>
    </ConfirmAction>
  );
};

const StepRow = ({ step, tableId }: { step: PathStepRead; tableId: string }) => (
  <li className="rounded-[var(--radius-default)] border border-border p-3" data-step={step.id}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{step.title}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {step.fixes.length ? `Clears: ${step.fixes.join(", ")}` : "Clears no deduction on its own"}
          {" · "}
          {CONFIDENCE_LABEL[step.confidence]}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">After this</span>
        <GradeBadge grade={step.projected_grade} score={step.projected_score} />
      </div>
    </div>
    <p className="mt-2 text-sm text-muted-foreground">{step.detail}</p>
    {step.evidence.length ? (
      <details className="mt-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer">How this was estimated</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {step.evidence.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </details>
    ) : null}
    <div className="mt-3">
      <StepAction step={step} tableId={tableId} />
    </div>
  </li>
);

const Summary = ({ path }: { path: PathToARead }) => {
  if (path.steps.length === 0) {
    return <p className="td-mt-lede">No deductions. This table is already at its best grade.</p>;
  }
  const external = path.reachable_score > path.cryo_score;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-foreground">
      <span>Now</span>
      <GradeBadge grade={path.grade} score={path.score} />
      <span>→ after Cryo&apos;s steps</span>
      <GradeBadge grade={path.cryo_grade} score={path.cryo_score} />
      {external ? (
        <>
          <span>→ with the changes outside Cryo</span>
          <GradeBadge grade={path.reachable_grade} score={path.reachable_score} />
        </>
      ) : null}
    </div>
  );
};

export const PathToAPanel = ({ tableId }: { tableId: string }) => {
  const { api, token } = useAuth();
  const path = useQuery({
    queryKey: tableKeys.pathToA(tableId, token),
    queryFn: () => getPathToA(api, tableId),
    enabled: tableId.length > 0,
    retry: false
  });
  if (path.isLoading) return <p className="td-mt-lede">Working out the path to grade A…</p>;
  if (path.isError) return <QueryErrorAlert error={path.error} title="Couldn't work out the path to grade A" />;
  if (!path.data) return null;
  const data = path.data;
  return (
    <section aria-label="Path to grade A" className="space-y-4">
      <Summary path={data} />
      {data.notes.map((note) => (
        <p key={note} className="text-xs text-muted-foreground">
          {note}
        </p>
      ))}
      {data.design_findings && data.design_findings.length > 0 ? (
        <div aria-label="Design findings">
          <h4 className="td-mt-subhead">Design findings</h4>
          <p className="mb-2 text-xs text-muted-foreground">
            Table layout issues. They don&apos;t lower the grade, and maintenance can&apos;t fix them.
          </p>
          <ul className="space-y-1 text-sm">
            {data.design_findings.map((finding) => (
              <li key={finding.label}>
                <span className="font-medium">{finding.label}</span>
                <span className="text-muted-foreground"> ({finding.severity}): {finding.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {KIND_ORDER.map((kind) => {
        const steps = data.steps.filter((step) => step.kind === kind);
        if (steps.length === 0) return null;
        return (
          <div key={kind}>
            <h4 className="td-mt-subhead">{KIND_HEADINGS[kind].title}</h4>
            <p className="mb-2 text-xs text-muted-foreground">{KIND_HEADINGS[kind].lede}</p>
            <ol className="space-y-3">
              {steps.map((step) => (
                <StepRow key={step.id} step={step} tableId={tableId} />
              ))}
            </ol>
          </div>
        );
      })}
    </section>
  );
};
