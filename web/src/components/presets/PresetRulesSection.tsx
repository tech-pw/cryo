import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { createPresetRule, deletePresetRule, listPresetRules } from "../../api/client";
import type { UUID } from "../../api/types";
import { useAuth } from "../../lib/auth";
import { tableFiltersToPredicate, type TableFilterState } from "../../lib/predicate";
import { ConfirmAction } from "../confirm";
import { FilterBar } from "../FilterBar";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { DataTable, type DataTableColumnDef } from "../ui/data-table";

type PresetRuleRow = { id: UUID; enabled: boolean; predicate: unknown };

/** Rules save the moment they are added or deleted; they are not part of the preset form. */
export const PresetRulesSection = ({ presetId }: { presetId: UUID }) => {
  const { api, role } = useAuth();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<TableFilterState>({ activeOnly: true });
  const rules = useQuery({
    queryKey: ["preset-rules", presetId],
    queryFn: () => listPresetRules(api, presetId)
  });
  const refresh = () => {
    void rules.refetch();
    void queryClient.invalidateQueries({ queryKey: ["preset-simulation", presetId] });
  };
  const addRule = useMutation({
    mutationFn: () => {
      const predicate = tableFiltersToPredicate(filters);
      if (!predicate) throw new Error("Choose at least one filter.");
      const onlyActive =
        "field" in predicate && predicate.field === "is_active" && predicate.op === "=" && predicate.value === true;
      if (onlyActive) {
        throw new Error("Choose at least one filter. Active tables alone matches the whole fleet.");
      }
      return createPresetRule(api, presetId, { predicate, enabled: true });
    },
    onSuccess: () => {
      toast.success("Rule added");
      refresh();
    },
    onError: (error) => toast.error((error as Error).message)
  });
  const removeRule = useMutation({
    mutationFn: (ruleId: UUID) => deletePresetRule(api, presetId, ruleId),
    onSuccess: () => {
      toast.success("Rule deleted");
      refresh();
    },
    onError: (error) => toast.error((error as Error).message)
  });

  const columns: DataTableColumnDef<PresetRuleRow, unknown>[] = [
    { id: "enabled", header: "Enabled", cell: ({ row }) => String(row.original.enabled) },
    {
      id: "predicate",
      header: "Predicate",
      cell: ({ row }) => <pre className="inline-json">{JSON.stringify(row.original.predicate)}</pre>
    },
    ...(role === "admin"
      ? [
          {
            id: "actions",
            header: "Actions",
            cell: ({ row }: { row: { original: PresetRuleRow } }) => (
              <ConfirmAction title="Delete this rule?" onConfirm={() => removeRule.mutate(row.original.id)}>
                <Button size="sm" variant="danger" loading={removeRule.isPending}>
                  Delete
                </Button>
              </ConfirmAction>
            )
          } as DataTableColumnDef<PresetRuleRow, unknown>
        ]
      : [])
  ];

  return (
    <Card aria-labelledby="preset-rules-heading">
      <CardHeader className="pb-2">
        <CardTitle id="preset-rules-heading">Assignment rules</CardTitle>
        <CardDescription>
          Tables matching a rule use this preset unless they are pinned elsewhere. Adding or deleting a rule
          takes effect immediately and is separate from Save preset.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <FilterBar value={filters} onChange={setFilters} />
        <div>
          <Button loading={addRule.isPending} disabled={role !== "admin"} onClick={() => addRule.mutate()}>
            Add rule
          </Button>
        </div>
        <DataTable
          columns={columns}
          data={(rules.data ?? []) as PresetRuleRow[]}
          density="compact"
          loading={rules.isLoading}
          getRowId={(row) => row.id}
          emptyTitle="No assignment rules"
        />
      </CardContent>
    </Card>
  );
};
