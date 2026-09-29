import { useQuery } from "@tanstack/react-query";

import { getEngineLoad } from "../../api/engineLoad";
import { useAuth } from "../../lib/auth";

export const EngineLoadStrip = () => {
  const { api, token } = useAuth();
  const load = useQuery({
    queryKey: ["engine-load", token],
    queryFn: () => getEngineLoad(api),
    retry: false
  });
  const engines = load.data?.engines ?? [];

  return (
    <section className="mb-4 rounded-md border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2">
      <h2 className="text-sm font-medium">Engine load</h2>
      <p className="text-xs text-muted-foreground">
        Occupying jobs versus the configured cap. A live engine probe is not recorded.
      </p>
      {load.isError ? (
        <p className="mt-2 text-sm">Engine admission unavailable. Retry from Runs.</p>
      ) : engines.length === 0 ? (
        <p className="mt-2 text-sm">No enabled engines.</p>
      ) : (
        <ul className="mt-2 flex flex-wrap gap-2">
          {engines.map((row) => (
            <li key={row.destination_id} className="rounded-full border px-2 py-1 text-xs">
              {row.name}: {row.slots_used} / {row.slots_limit} · {row.admission}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
