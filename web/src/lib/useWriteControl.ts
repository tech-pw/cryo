import { useQuery } from "@tanstack/react-query";

import { useAuth } from "./auth";
import { writeAccess, type WriteAction, type WriteAccess } from "./permissions";
import { getRuntimeFlags } from "./runtimeFlags";

export const useWriteControl = (action: WriteAction): WriteAccess => {
  const { role, api, token } = useAuth();
  const runtime = useQuery({
    queryKey: ["runtime-flags", token],
    queryFn: () => getRuntimeFlags(api),
    staleTime: 60_000,
    retry: false
  });
  return writeAccess(role, action, runtime.data?.maintenance_writes_enabled ?? true);
};
