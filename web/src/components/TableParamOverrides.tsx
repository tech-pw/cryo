import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, SlidersHorizontal, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { getEffectiveParams, patchTableParamOverrides } from "../api/client";
import type { UUID } from "../api/types";
import { EmptyState, QueryErrorAlert } from "./common";
import { useAuth } from "../lib/auth";
import { Autocomplete } from "./ui/autocomplete";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";
import { SimpleTable } from "./ui/simple-table";

interface Props {
  tableId: UUID;
}

const SUGGESTED_PARAM_KEYS = [
  "retention_days",
  "retain_last",
  "orphan_retention_days",
  "file_size_mb",
  "min_small_files",
  "delete_ratio_trigger",
  "max_partitions_per_chunk",
  "group_concurrency"
];

function parseOverrideValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed !== "" && !Number.isNaN(Number(trimmed))) return Number(trimmed);
  return raw;
}

export const TableParamOverrides = ({ tableId }: Props) => {
  const { api, token, role } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = role === "admin";
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");

  const effectiveParams = useQuery({
    queryKey: ["effective-params", tableId, token],
    queryFn: () => getEffectiveParams(api, tableId),
    retry: false
  });

  const patch = useMutation({
    mutationFn: (overrides: Record<string, unknown>) => patchTableParamOverrides(api, tableId, overrides),
    onSuccess: () => {
      toast.success("Parameter overrides updated");
      setNewKey("");
      setNewValue("");
      void queryClient.invalidateQueries({ queryKey: ["effective-params", tableId] });
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const overrides = effectiveParams.data?.param_overrides ?? {};
  const presetParams = effectiveParams.data?.preset_params ?? {};
  const overrideKeys = Object.keys(overrides);

  const keyOptions = useMemo(
    () =>
      SUGGESTED_PARAM_KEYS.filter((key) => !overrideKeys.includes(key)).map((key) => ({
        value: key,
        label: key
      })),
    [overrideKeys]
  );

  const handleAdd = () => {
    const key = newKey.trim();
    if (!key || newValue.trim() === "") return;
    patch.mutate({ ...overrides, [key]: parseOverrideValue(newValue) });
  };

  const handleRemove = (key: string) => {
    const next = { ...overrides };
    delete next[key];
    patch.mutate(next);
  };

  if (effectiveParams.isLoading) {
    return null;
  }

  if (effectiveParams.error) {
    return (
      <Card className="mt-3">
        <CardContent className="pt-4">
          <QueryErrorAlert error={effectiveParams.error} title="Failed to load effective parameters" />
        </CardContent>
      </Card>
    );
  }

  // Per-table overrides layer on top of an effective preset (cryo/api/routes.py's
  // resolve_effective_preset) -- with no preset assigned there's nothing to override, so this
  // used to silently render nothing at all here. That read as "this feature is missing" rather
  // than "assign a preset first" -- say so instead of vanishing.
  if (!effectiveParams.data?.preset_id) {
    return (
      <Card className="mt-3">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <SlidersHorizontal className="size-4" />
            Table parameter overrides
          </CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState
            title="No preset assigned"
            description="Assign a preset (in the header above) to configure per-table parameter overrides on top of it."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-3">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <SlidersHorizontal className="size-4" />
          Table parameter overrides
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Tweak maintenance params for this table only, layered on top of{" "}
          <strong>{effectiveParams.data.preset_name}</strong> without changing the preset for other tables assigned to
          it.
        </p>

        {overrideKeys.length > 0 ? (
          <SimpleTable
            rowKey={(row) => row.key}
            dataSource={overrideKeys.map((key) => ({
              key,
              presetValue: presetParams[key],
              overrideValue: overrides[key]
            }))}
            columns={[
              { title: "Param", dataIndex: "key", width: 220 },
              {
                title: "Preset default",
                dataIndex: "presetValue",
                render: (value) =>
                  value === undefined ? <span className="text-muted-foreground">—</span> : String(value)
              },
              {
                title: "Override",
                dataIndex: "overrideValue",
                render: (value) => <Badge variant="default">{String(value)}</Badge>
              },
              ...(canEdit
                ? [
                    {
                      title: "",
                      key: "actions",
                      width: 40,
                      render: (_: unknown, row: { key: string }) => (
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Remove ${row.key}`}
                          onClick={() => handleRemove(row.key)}
                          disabled={patch.isPending}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      )
                    }
                  ]
                : [])
            ]}
          />
        ) : (
          <p className="text-xs text-muted-foreground">
            No overrides — this table uses {effectiveParams.data.preset_name}&rsquo;s params as-is.
          </p>
        )}

        {canEdit ? (
          <div className="flex flex-wrap items-center gap-2">
            <Autocomplete
              value={newKey}
              onChange={setNewKey}
              options={keyOptions}
              placeholder="Param name"
              className="w-[200px]"
            />
            <Input
              placeholder="Value"
              className="w-[140px]"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") handleAdd();
              }}
            />
            <Button
              size="sm"
              onClick={handleAdd}
              disabled={!newKey.trim() || newValue.trim() === "" || patch.isPending}
              loading={patch.isPending}
            >
              <Plus className="size-3.5" />
              Add override
            </Button>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
};
