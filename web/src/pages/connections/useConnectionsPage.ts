import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  createAIProvider,
  createDestination,
  createSlackConfig,
  createSource,
  deleteAIProvider,
  deleteSlackConfig,
  getCapabilities,
  getCatalogLatency,
  listAIProviders,
  listAIProviderTypes,
  listDestinations,
  listSlackConfigs,
  listSources,
  patchAIProvider,
  patchDestination,
  patchSlackConfig,
  patchSource,
  purgeDestination,
  purgeSource,
  testAIProvider,
  testDestination,
  testSlackConfig,
  triggerDailyReport,
  ApiError,
  type AIProviderRead,
  type AIProviderWrite,
  type SlackConfigRead,
  type SlackConfigWrite
} from "../../api/client";
import type { DestinationRead, DestinationWrite, SourceRead, SourceWrite } from "../../api/types";
import { useAwsSourceStatuses } from "../../components/AwsStatusBadge";
import { useAuth } from "../../lib/auth";
import {
  markConnectionNeedsScan,
  type ConnectionScanNotice
} from "../../lib/connectionScanPrompt";
import type { DestinationCapacityTestResult } from "./helpers";

/** URL-backed edit context: `?edit=<kind>&editId=<id-or-new>` so a drawer link can be shared/reloaded. */
type EditKind = "source" | "destination" | "ai-provider" | "slack";
const EDIT_KINDS: readonly EditKind[] = ["source", "destination", "ai-provider", "slack"];
const isEditKind = (value: string | null): value is EditKind =>
  value !== null && (EDIT_KINDS as readonly string[]).includes(value);

export const useConnectionsPage = () => {
  const { api, token, role } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [sourceDrawer, setSourceDrawerState] = useState<SourceRead | "new" | null>(null);
  const [destinationDrawer, setDestinationDrawerState] = useState<DestinationRead | "new" | null>(null);
  const [aiProviderDrawer, setAiProviderDrawerState] = useState<AIProviderRead | "new" | null>(null);
  const [slackDrawer, setSlackDrawerState] = useState<SlackConfigRead | "new" | null>(null);
  const [latencySource, setLatencySource] = useState<SourceRead | null>(null);
  const [dailyReportResult, setDailyReportResult] = useState<Awaited<ReturnType<typeof triggerDailyReport>> | null>(null);
  const [sendReportToSlack, setSendReportToSlack] = useState(false);
  const [reportUseAi, setReportUseAi] = useState(true);
  const [testResult, setTestResult] = useState<DestinationCapacityTestResult | null>(null);
  const [pendingScanVersion, setPendingScanVersion] = useState(0);
  const [urlEditHydrated, setUrlEditHydrated] = useState(false);

  const syncEditUrl = useCallback(
    (kind: EditKind, id: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (id === null) {
        // Only clear the URL if it's currently pointing at this same drawer kind -- opening
        // drawer B then closing it shouldn't wipe drawer A's still-open edit context.
        if (next.get("edit") === kind) {
          next.delete("edit");
          next.delete("editId");
        }
      } else {
        next.set("edit", kind);
        next.set("editId", id);
      }
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams]
  );

  const setSourceDrawer = useCallback(
    (value: SourceRead | "new" | null) => {
      setSourceDrawerState(value);
      syncEditUrl("source", value === null ? null : value === "new" ? "new" : value.id);
    },
    [syncEditUrl]
  );
  const setDestinationDrawer = useCallback(
    (value: DestinationRead | "new" | null) => {
      setDestinationDrawerState(value);
      syncEditUrl("destination", value === null ? null : value === "new" ? "new" : value.id);
    },
    [syncEditUrl]
  );
  const setAiProviderDrawer = useCallback(
    (value: AIProviderRead | "new" | null) => {
      setAiProviderDrawerState(value);
      syncEditUrl("ai-provider", value === null ? null : value === "new" ? "new" : value.id);
    },
    [syncEditUrl]
  );
  const setSlackDrawer = useCallback(
    (value: SlackConfigRead | "new" | null) => {
      setSlackDrawerState(value);
      syncEditUrl("slack", value === null ? null : value === "new" ? "new" : value.id);
    },
    [syncEditUrl]
  );

  const redirectToScanner = (notice: ConnectionScanNotice) => {
    markConnectionNeedsScan(notice);
    setPendingScanVersion((value) => value + 1);
    navigate("/scans", { state: { connectionNotice: notice } });
  };

  const sources = useQuery({ queryKey: ["sources", token], queryFn: () => listSources(api) });
  const capabilities = useQuery({
    queryKey: ["capabilities", token],
    queryFn: () => getCapabilities(api),
    staleTime: Number.POSITIVE_INFINITY
  });
  const awsStatuses = useAwsSourceStatuses((sources.data ?? []).map((s) => s.id));
  const catalogLatency = useQuery({
    queryKey: ["catalog-latency", token, latencySource?.id],
    queryFn: () => getCatalogLatency(api, latencySource!.id),
    enabled: Boolean(latencySource)
  });
  const destinations = useQuery({ queryKey: ["destinations", token], queryFn: () => listDestinations(api) });
  const aiProviders = useQuery({
    queryKey: ["ai-providers", token],
    queryFn: () => listAIProviders(api)
  });
  const aiProviderTypes = useQuery({
    queryKey: ["ai-provider-types", token],
    queryFn: () => listAIProviderTypes(api)
  });
  const slackConfigs = useQuery({
    queryKey: ["slack-configs", token],
    queryFn: () => listSlackConfigs(api)
  });

  // One-time hydration of a drawer from `?edit=<kind>&editId=<id-or-new>` -- e.g. a bookmarked or
  // shared link to a specific source's edit drawer. Waits for the relevant list query to load
  // before resolving an id to a row, then clears the params if the id no longer exists.
  useEffect(() => {
    if (urlEditHydrated) return;
    const editKind = searchParams.get("edit");
    const editId = searchParams.get("editId");
    if (!isEditKind(editKind) || !editId) {
      setUrlEditHydrated(true);
      return;
    }
    if (editId === "new") {
      if (editKind === "source") setSourceDrawerState("new");
      else if (editKind === "destination") setDestinationDrawerState("new");
      else if (editKind === "ai-provider") setAiProviderDrawerState("new");
      else setSlackDrawerState("new");
      setUrlEditHydrated(true);
      return;
    }
    const queryForKind =
      editKind === "source"
        ? sources
        : editKind === "destination"
          ? destinations
          : editKind === "ai-provider"
            ? aiProviders
            : slackConfigs;
    if (queryForKind.isLoading) return;
    const row = (queryForKind.data as Array<{ id: string }> | undefined ?? []).find((item) => item.id === editId);
    if (row) {
      if (editKind === "source") setSourceDrawerState(row as SourceRead);
      else if (editKind === "destination") setDestinationDrawerState(row as DestinationRead);
      else if (editKind === "ai-provider") setAiProviderDrawerState(row as AIProviderRead);
      else setSlackDrawerState(row as SlackConfigRead);
    } else {
      const next = new URLSearchParams(searchParams);
      next.delete("edit");
      next.delete("editId");
      setSearchParams(next, { replace: true });
    }
    setUrlEditHydrated(true);
  }, [urlEditHydrated, searchParams, setSearchParams, sources, destinations, aiProviders, slackConfigs]);

  const saveSource = useMutation({
    mutationFn: (body: SourceWrite) =>
      sourceDrawer && sourceDrawer !== "new" ? patchSource(api, sourceDrawer.id, body) : createSource(api, body),
    onSuccess: (saved) => {
      const isNew = sourceDrawer === "new";
      toast.success(isNew ? "Lakehouse connected" : "Lakehouse saved");
      setSourceDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
      if (isNew) {
        redirectToScanner({ kind: "lakehouse", id: saved.id, name: saved.name });
      }
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const saveDestination = useMutation({
    mutationFn: (body: DestinationWrite) =>
      destinationDrawer && destinationDrawer !== "new"
        ? patchDestination(api, destinationDrawer.id, body)
        : createDestination(api, body),
    onSuccess: (saved) => {
      const isNew = destinationDrawer === "new";
      toast.success(isNew ? "Engine connected" : "Engine saved");
      setDestinationDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["destinations"] });
      if (isNew) {
        redirectToScanner({ kind: "engine", id: saved.id, name: saved.name });
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 403 || error.status === 401)) {
        toast.error("Admin role required to create or edit engines. Sign in with role admin.");
        return;
      }
      toast.error((error as Error).message);
    }
  });

  const probe = useMutation({
    mutationFn: (destination: DestinationRead) => testDestination(api, destination.id),
    onSuccess: (data) => setTestResult(data as unknown as DestinationCapacityTestResult),
    onError: (error) => toast.error((error as Error).message)
  });

  const purgeSourceMutation = useMutation({
    mutationFn: (id: string) => purgeSource(api, id),
    onSuccess: () => {
      toast.success("Lakehouse deleted");
      void queryClient.invalidateQueries({ queryKey: ["sources"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const purgeDestinationMutation = useMutation({
    mutationFn: (id: string) => purgeDestination(api, id),
    onSuccess: () => {
      toast.success("Engine deleted");
      void queryClient.invalidateQueries({ queryKey: ["destinations"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const saveAiProvider = useMutation({
    mutationFn: async (body: AIProviderWrite & { id?: string }) => {
      if (body.id) {
        return patchAIProvider(api, body.id, body);
      }
      return createAIProvider(api, body);
    },
    onSuccess: () => {
      toast.success("AI provider saved");
      setAiProviderDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["ai-providers"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const deleteAiProviderMutation = useMutation({
    mutationFn: (id: string) => deleteAIProvider(api, id),
    onSuccess: () => {
      toast.success("AI provider deleted");
      void queryClient.invalidateQueries({ queryKey: ["ai-providers"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const testAiProviderMutation = useMutation({
    mutationFn: (id: string) => testAIProvider(api, id),
    onSuccess: (data) => {
      if (data.success) {
        toast.success(`AI responded: "${data.response}"`);
      } else {
        toast.error(`Test failed: ${data.error}`);
      }
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const saveSlackConfig = useMutation({
    mutationFn: async (body: SlackConfigWrite & { id?: string }) => {
      if (body.id) {
        return patchSlackConfig(api, body.id, body);
      }
      return createSlackConfig(api, body);
    },
    onSuccess: () => {
      toast.success("Slack configuration saved");
      setSlackDrawer(null);
      void queryClient.invalidateQueries({ queryKey: ["slack-configs"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const deleteSlackConfigMutation = useMutation({
    mutationFn: (id: string) => deleteSlackConfig(api, id),
    onSuccess: () => {
      toast.success("Slack configuration deleted");
      void queryClient.invalidateQueries({ queryKey: ["slack-configs"] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const testSlackMutation = useMutation({
    mutationFn: (id: string) => testSlackConfig(api, id),
    onSuccess: () => {
      toast.success("Test message sent to Slack!");
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const dailyReport = useMutation({
    mutationFn: () =>
      triggerDailyReport(api, { send_to_slack: sendReportToSlack, use_ai: reportUseAi }),
    onSuccess: (result) => {
      setDailyReportResult(result);
      toast.success(result.slack_sent ? "Report generated and sent to Slack" : "Report generated");
    },
    onError: (error) => toast.error((error as Error).message)
  });

  return {
    api,
    role,
    sources,
    capabilities,
    awsStatuses,
    destinations,
    aiProviders,
    aiProviderTypes,
    slackConfigs,
    catalogLatency,
    sourceDrawer,
    setSourceDrawer,
    destinationDrawer,
    setDestinationDrawer,
    aiProviderDrawer,
    setAiProviderDrawer,
    slackDrawer,
    setSlackDrawer,
    latencySource,
    setLatencySource,
    dailyReportResult,
    sendReportToSlack,
    setSendReportToSlack,
    reportUseAi,
    setReportUseAi,
    testResult,
    setTestResult,
    pendingScanVersion,
    saveSource,
    saveDestination,
    probe,
    purgeSourceMutation,
    purgeDestinationMutation,
    saveAiProvider,
    deleteAiProviderMutation,
    testAiProviderMutation,
    saveSlackConfig,
    deleteSlackConfigMutation,
    testSlackMutation,
    dailyReport,
    queryClient
  };
};

export type ConnectionsPageState = ReturnType<typeof useConnectionsPage>;
