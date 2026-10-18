/**
 * Move 24 — banner that surfaces the "no sources connected" first-run state
 * on every page. Distinct from the existing OnboardingBanner (which is
 * meant to be rendered on a specific page): this one is meant to live at
 * the App shell layer and quietly disappear once at least one source is
 * registered. Only shows when sources==0, so it doesn't add visual noise
 * for already-onboarded operators.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listSources } from "../api/client";
import { useAuth } from "../lib/auth";
import { Alert } from "./ui/alert";
import { Button } from "./ui/button";

export const NoSourcesBanner = () => {
  const { api, token } = useAuth();
  // Same key the Connections page uses, so saving a lakehouse (which invalidates
  // ["sources"]) clears this too. Under its own key the banner outlived the first
  // connected catalog -- nothing invalidated it, and staleTime kept serving the
  // cached "zero sources" answer, so the UI told operators to connect a catalog
  // they had just connected.
  const sourcesQuery = useQuery({
    queryKey: ["sources", token],
    queryFn: () => listSources(api),
    refetchOnWindowFocus: false,
    staleTime: 30_000
  });

  const isFirstRun = sourcesQuery.isSuccess && (sourcesQuery.data?.length ?? 0) === 0;

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
