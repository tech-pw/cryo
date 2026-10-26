import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "../lib/auth";
import { setDisplayTimeZone } from "../lib/format";
import { getRuntimeFlags } from "../lib/runtimeFlags";

/**
 * Publishes the install's display time zone (CRYO_DISPLAY_TIMEZONE, #183).
 *
 * The backend schedules and reports in one zone; the UI must render instants in the same
 * one, or the same page shows two clocks side by side. The zone is published once, on the
 * first runtime response, and every date formatter reads it from `lib/format`.
 */
export const DisplayTimeZoneSync = () => {
  const { api, token } = useAuth();

  const runtime = useQuery({
    queryKey: ["runtime-flags", token],
    queryFn: () => getRuntimeFlags(api),
    staleTime: 60_000,
    retry: false
  });

  const zone = runtime.data?.display_timezone;
  useEffect(() => {
    if (zone) setDisplayTimeZone(zone);
  }, [zone]);

  return null;
};
