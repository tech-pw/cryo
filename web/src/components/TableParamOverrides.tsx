import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DeleteOutlined, PlusOutlined, SlidersOutlined } from "@ant-design/icons";
import { AutoComplete, Button, Card, Input, Space, Table, Tag, Typography, message } from "antd";
import { useMemo, useState } from "react";
import { getEffectiveParams, patchTableParamOverrides } from "../api/client";
import type { UUID } from "../api/types";
import { QueryErrorAlert } from "./common";
import { useAuth } from "../lib/auth";

interface Props {
  tableId: UUID;
}

// Common tunables worth surfacing as autocomplete suggestions -- the override set itself is
// open-ended (cryo/planner/guardrails.py::validate_params only checks a handful of known
// keys), so free text is still accepted for anything not in this list.
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

// retention_days/retain_last/etc. must round-trip as real JSON numbers -- guardrails.py's
// _is_number() rejects numeric strings outright, so a value typed as "1" has to become 1,
// not stay "1", or the PATCH fails validation.
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
      message.success("Parameter overrides updated");
      setNewKey("");
      setNewValue("");
      void queryClient.invalidateQueries({ queryKey: ["effective-params", tableId] });
    },
    onError: (error) => message.error((error as Error).message)
  });

  const overrides = effectiveParams.data?.param_overrides ?? {};
  const presetParams = effectiveParams.data?.preset_params ?? {};
  const overrideKeys = Object.keys(overrides);

  const keyOptions = useMemo(
    () =>
      SUGGESTED_PARAM_KEYS.filter((key) => !overrideKeys.includes(key)).map((key) => ({ value: key })),
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

  if (effectiveParams.isLoading || !effectiveParams.data?.preset_id) {
    return null;
  }

  if (effectiveParams.error) {
    return (
      <Card size="small" style={{ marginTop: 12 }}>
        <QueryErrorAlert error={effectiveParams.error} title="Failed to load effective parameters" />
      </Card>
    );
  }

  const columns = [
    { title: "Param", dataIndex: "key", width: 220 },
    {
      title: "Preset default",
      dataIndex: "presetValue",
      render: (value: unknown) =>
        value === undefined ? <Typography.Text type="secondary">—</Typography.Text> : String(value)
    },
    {
      title: "Override",
      dataIndex: "overrideValue",
      render: (value: unknown) => <Tag color="blue">{String(value)}</Tag>
    },
    ...(canEdit
      ? [
          {
            title: "",
            key: "actions",
            width: 40,
            render: (_: unknown, row: { key: string }) => (
              <Button
                size="small"
                type="text"
                icon={<DeleteOutlined />}
                onClick={() => handleRemove(row.key)}
                disabled={patch.isPending}
              />
            )
          }
        ]
      : [])
  ];

  return (
    <Card
      size="small"
      style={{ marginTop: 12 }}
      title={
        <Space size={6}>
          <SlidersOutlined />
          <span>Table parameter overrides</span>
        </Space>
      }
    >
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Tweak maintenance params for this table only, layered on top of{" "}
        <strong>{effectiveParams.data.preset_name}</strong> without changing the preset for other tables
        assigned to it.
      </Typography.Text>

      {overrideKeys.length > 0 ? (
        <Table
          size="small"
          style={{ marginTop: 12 }}
          pagination={false}
          rowKey="key"
          dataSource={overrideKeys.map((key) => ({
            key,
            presetValue: presetParams[key],
            overrideValue: overrides[key]
          }))}
          columns={columns}
        />
      ) : (
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 12, fontSize: 12 }}>
          No overrides — this table uses {effectiveParams.data.preset_name}&rsquo;s params as-is.
        </Typography.Text>
      )}

      {canEdit ? (
        <Space style={{ marginTop: 12 }} wrap>
          <AutoComplete
            placeholder="Param name"
            style={{ width: 200 }}
            value={newKey}
            onChange={setNewKey}
            options={keyOptions}
            filterOption={(input, option) =>
              (option?.value as string)?.toLowerCase().includes(input.toLowerCase())
            }
          />
          <Input
            placeholder="Value"
            style={{ width: 140 }}
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            onPressEnter={handleAdd}
          />
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={handleAdd}
            disabled={!newKey.trim() || newValue.trim() === "" || patch.isPending}
            loading={patch.isPending}
          >
            Add override
          </Button>
        </Space>
      ) : null}
    </Card>
  );
};
