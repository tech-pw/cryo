import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  createAIProvider,
  createDestination,
  createSlackConfig,
  createSource,
  deleteAIProvider,
  deleteSlackConfig,
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

export const useConnectionsPage = () => {
  const { api, token, role } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [sourceDrawer, setSourceDrawer] = useState<SourceRead | "new" | null>(null);
  const [destinationDrawer, setDestinationDrawer] = useState<DestinationRead | "new" | null>(null);
  const [aiProviderDrawer, setAiProviderDrawer] = useState<AIProviderRead | "new" | null>(null);
  const [slackDrawer, setSlackDrawer] = useState<SlackConfigRead | "new" | null>(null);
  const [latencySource, setLatencySource] = useState<SourceRead | null>(null);
  const [dailyReportResult, setDailyReportResult] = useState<Awaited<ReturnType<typeof triggerDailyReport>> | null>(null);
  const [sendReportToSlack, setSendReportToSlack] = useState(false);
  const [reportUseAi, setReportUseAi] = useState(true);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [pendingScanVersion, setPendingScanVersion] = useState(0);

  const redirectToScanner = (notice: ConnectionScanNotice) => {
    markConnectionNeedsScan(notice);
    setPendingScanVersion((value) => value + 1);
    navigate("/scans", { state: { connectionNotice: notice } });
  };

  const sources = useQuery({ queryKey: ["sources", token], queryFn: () => listSources(api) });
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
    onSuccess: setTestResult,
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
