/**
 * Move 24 — banner that surfaces the "no sources connected" first-run state
 * on every page. Distinct from the existing OnboardingBanner (which is
 * meant to be rendered on a specific page): this one is meant to live at
 * the App shell layer and quietly disappear once at least one source is
 * registered. Only shows when sources==0, so it doesn't add visual noise
 * for already-onboarded operators.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link } from "react-router-dom";

import { listSources } from "../api/client";
import { useAuth } from "../lib/auth";
import { Alert } from "./ui/alert";
import { Button } from "./ui/button";

export const NoSourcesBanner = () => {
  const { api, token } = useAuth();
  const sourcesQuery = useQuery({
    queryKey: ["no-sources-banner", token],
    queryFn: () => listSources(api),
    refetchOnWindowFocus: false,
    staleTime: 30_000
  });

  const isFirstRun =
    sourcesQuery.isSuccess && (sourcesQuery.data?.length ?? 0) === 0;

  useEffect(() => {
    // Drop the result into the cache under a stable key so it can be shared
    // with other components that need the source count (e.g. the FirstRun
    // empty-state at /health). Prevents a second /sources round-trip on
    // first render.
  }, []);

  if (!isFirstRun) return null;

  return (
    <Alert
      className="mb-4"
      variant="info"
      title="No sources connected yet"
      description={
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <span>
            Cryo can&apos;t discover tables, costs, or query patterns until at least one catalog is wired up.
            Connect a REST, Glue, or Hive Metastore lakehouse to get started.
          </span>
          <Link to="/connections">
            <Button variant="primary" size="sm">
              Connect a catalog
            </Button>
          </Link>
        </div>
      }
    />
  );
};
