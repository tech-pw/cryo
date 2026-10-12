import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Database, HeartPulse, Radar } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getScannerStatus, listSources, triggerScan } from "../../api/client";
import { useAuth } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../ui/dialog";

const ONBOARDING_STORAGE_KEY = "cryo-onboarding-dismissed";

const STEPS = [
  {
    id: "connect",
    title: "Connect a catalog",
    description:
      "Wire up a REST, Glue, or Hive Metastore lakehouse so Cryo can discover Iceberg tables.",
    icon: Database
  },
  {
    id: "scan",
    title: "Run your first scan",
    description:
      "Scanning collects file stats, health grades, and cost signals across your fleet.",
    icon: Radar
  },
  {
    id: "health",
    title: "Review datalake health",
    description:
      "Open the Health dashboard to see grades, small-file pressure, and maintenance opportunities.",
    icon: HeartPulse
  }
] as const;

type StepId = (typeof STEPS)[number]["id"];

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function persistDismissed(): void {
  try {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "1");
  } catch {
    // localStorage unavailable
  }
}

export const FirstRunWizard = () => {
  const { api, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(readDismissed);

  const sources = useQuery({
    queryKey: ["first-run-sources", token],
    queryFn: () => listSources(api),
    refetchOnWindowFocus: true,
    staleTime: 15_000
  });

  const scanner = useQuery({
    queryKey: ["first-run-scanner", token],
    queryFn: () => getScannerStatus(api),
    enabled: (sources.data?.length ?? 0) > 0,
    refetchInterval: 15_000
  });

  const sourceCount = sources.data?.length ?? 0;
  const tablesWithStats = scanner.data?.tables_with_stats ?? 0;
  const isFirstRun = sources.isSuccess && sourceCount === 0;
  const needsScan = sourceCount > 0 && tablesWithStats === 0;

  const activeStep: StepId = useMemo(() => {
    if (sourceCount === 0) return "connect";
    if (tablesWithStats === 0) return "scan";
    return "health";
  }, [sourceCount, tablesWithStats]);

  const activeStepIndex = STEPS.findIndex((step) => step.id === activeStep);

  useEffect(() => {
    if (dismissed || sources.isLoading) return;
    if (isFirstRun || needsScan) {
      setOpen(true);
    } else if (tablesWithStats > 0) {
      setOpen(false);
    }
  }, [dismissed, isFirstRun, needsScan, sources.isLoading, tablesWithStats]);

  const scanMutation = useMutation({
    mutationFn: () => {
      const firstSource = sources.data?.[0];
      if (firstSource) {
        return triggerScan(api, {
          scope: "source",
          source_id: firstSource.id,
          full_scan: true,
          trigger: "first-run-wizard"
        });
      }
      return triggerScan(api, { scope: "all", full_scan: true, trigger: "first-run-wizard" });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["first-run-scanner"] });
      void queryClient.invalidateQueries({ queryKey: ["scanner-status"] });
    }
  });

  const handleDismiss = () => {
    persistDismissed();
    setDismissed(true);
    setOpen(false);
  };

  const handleFinish = () => {
    persistDismissed();
    setDismissed(true);
    setOpen(false);
    navigate("/health");
  };

  if (dismissed && !isFirstRun && !needsScan) {
    return null;
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Welcome to Cryo</DialogTitle>
          <DialogDescription className="text-foreground/80">
            Three quick steps to go from empty install to fleet health visibility.
          </DialogDescription>
        </DialogHeader>

        <ol className="first-run-steps">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            const status =
              index < activeStepIndex ? "complete" : index === activeStepIndex ? "active" : "pending";
            return (
              <li
                key={step.id}
                className={cn("first-run-step", `first-run-step--${status}`)}
                aria-current={status === "active" ? "step" : undefined}
              >
                <div className="first-run-step__icon">
                  {status === "complete" ? <CheckCircle2 className="h-5 w-5" /> : <Icon className="h-5 w-5" />}
                </div>
                <div className="first-run-step__body">
                  <p className="first-run-step__title">{step.title}</p>
                  <p className="first-run-step__description">{step.description}</p>
                </div>
              </li>
            );
          })}
        </ol>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            variant="ghost"
            className="border border-[var(--line-strong)] bg-[var(--panel-strong)] text-foreground hover:bg-[var(--color-surface-2)]"
            onClick={handleDismiss}
          >
            Skip for now
          </Button>
          <div className="flex flex-wrap gap-2">
            {activeStep === "connect" ? (
              <Button variant="primary" asChild>
                <Link to="/connections">Connect catalog</Link>
              </Button>
            ) : null}
            {activeStep === "scan" ? (
              <Button variant="primary" loading={scanMutation.isPending} onClick={() => scanMutation.mutate()}>
                Run scan
              </Button>
            ) : null}
            {activeStep === "health" ? (
              <Button variant="primary" onClick={handleFinish}>
                View health dashboard
              </Button>
            ) : null}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};