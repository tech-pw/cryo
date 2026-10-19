import { RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";

import type { TableSettingRead } from "../../../api/types";
import { cn } from "../../../lib/cn";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { MultiSelect } from "../../ui/multi-select";
import { Select } from "../../ui/select";
import { Switch } from "../../ui/switch";
import { Textarea } from "../../ui/textarea";
import {
  formatValue,
  isNumericType,
  parseIntOrNull,
  pickUnit,
  rangeHint,
  unitsFor,
  type UnitOption
} from "./tableConfig";

export interface SettingEditorProps {
  setting: TableSettingRead;
  /** Raw value the editor opens on: the staged value if any, else the value in force. */
  initialValue: string | null;
  columns: string[];
  onStage: (value: string | null) => void;
  onCancel: () => void;
}

const optionLabel = (setting: TableSettingRead, option: string): string => {
  if (setting.kind === "format_version") return `v${option}`;
  return option;
};

export const SettingEditor = ({ setting, initialValue, columns, onStage, onCancel }: SettingEditorProps) => {
  const type = setting.editor.value_type;
  const units = useMemo(() => unitsFor(setting), [setting]);
  const initialNumber = parseIntOrNull(initialValue);
  const [unit, setUnit] = useState<UnitOption>(() => pickUnit(units, initialNumber));
  const [amount, setAmount] = useState<string>(() =>
    initialNumber == null ? "" : String(initialNumber / pickUnit(units, initialNumber).factor)
  );
  const [text, setText] = useState<string>(initialValue ?? "");
  const [selected, setSelected] = useState<string[]>(() =>
    (initialValue ?? "").split(",").map((c) => c.trim()).filter(Boolean)
  );

  const numericDraft = (): { value: string | null; error: string | null } => {
    if (amount.trim() === "") return { value: null, error: "Enter a value" };
    const parsed = Number(amount);
    if (!Number.isFinite(parsed)) return { value: null, error: "Enter a number" };
    const base = Math.round(parsed * unit.factor);
    if (Math.abs(parsed * unit.factor - base) > 1e-6) return { value: null, error: "Must be a whole number" };
    const { minimum, maximum } = setting.editor;
    const fmt = (n: number) => formatValue({ ...setting, default_label: null }, String(n));
    if (minimum != null && base < minimum) return { value: null, error: `Must be at least ${fmt(minimum)}` };
    if (maximum != null && base > maximum) return { value: null, error: `Must be at most ${fmt(maximum)}` };
    return { value: String(base), error: null };
  };

  const draft: { value: string | null; error: string | null } = isNumericType(type)
    ? numericDraft()
    : type === "columns"
      ? { value: [...selected].sort().join(","), error: null }
      : { value: text.trim() === "" ? null : text, error: null };

  const unchanged = draft.value === (initialValue ?? (type === "columns" ? "" : null));

  const setFromBase = (raw: string) => {
    const base = parseIntOrNull(raw);
    if (isNumericType(type) && base != null) {
      const next = pickUnit(units, base);
      setUnit(next);
      setAmount(String(base / next.factor));
      return;
    }
    if (type === "columns") {
      setSelected(raw.split(",").filter(Boolean));
      return;
    }
    setText(raw);
  };

  const quickPicks: Array<{ label: string; value: string }> = [];
  if (setting.recommended_value != null && setting.recommended_value !== draft.value) {
    quickPicks.push({ label: `Recommended · ${formatValue(setting, setting.recommended_value)}`, value: setting.recommended_value });
  }
  if (
    setting.default_value != null &&
    setting.default_value !== draft.value &&
    setting.default_value !== setting.recommended_value &&
    type !== "bool" &&
    type !== "enum"
  ) {
    quickPicks.push({ label: `Default · ${formatValue(setting, setting.default_value)}`, value: setting.default_value });
  }

  const canReset =
    setting.editor.allow_unset &&
    (setting.current_source === "table" || setting.current_source === "cryo_override");

  const field = (() => {
    if (isNumericType(type)) {
      const single = units.length <= 1;
      return (
        <div className={cn("tc-editor-number", single && "tc-editor-number--single")}>
          <Input
            type="number"
            inputMode="numeric"
            aria-label={`${setting.label} value`}
            value={amount}
            min={0}
            onChange={(event) => setAmount(event.target.value)}
            autoFocus
          />
          {single ? (
            units[0]?.label ? <span className="tc-editor-unit">{units[0].label}</span> : null
          ) : (
            <Select
              aria-label={`${setting.label} unit`}
              value={unit.id}
              options={units.map((u) => ({ value: u.id, label: u.label }))}
              onChange={(id) => {
                const next = units.find((u) => u.id === id);
                if (!next) return;
                const parsed = Number(amount);
                if (amount.trim() !== "" && Number.isFinite(parsed)) {
                  const converted = (parsed * unit.factor) / next.factor;
                  setAmount(String(Number(converted.toFixed(4))));
                }
                setUnit(next);
              }}
            />
          )}
        </div>
      );
    }
    if (type === "bool") {
      const on = (text || "").toLowerCase() === "true";
      return (
        <label className="tc-editor-row">
          <Switch
            checked={on}
            aria-label={setting.label}
            onCheckedChange={(checked) => setText(checked ? "true" : "false")}
          />
          <span className="tc-value">{on ? "Enabled" : "Disabled"}</span>
        </label>
      );
    }
    if (type === "enum") {
      return (
        <Select
          aria-label={setting.label}
          value={text || undefined}
          placeholder={text ? undefined : "Choose a value"}
          options={(setting.editor.options ?? []).map((option) => ({ value: option, label: optionLabel(setting, option) }))}
          onChange={(next) => setText(next ?? "")}
          className="min-w-[12rem]"
        />
      );
    }
    if (type === "columns") {
      return (
        <MultiSelect
          aria-label={setting.label}
          placeholder="Columns"
          value={selected}
          options={columns.map((name) => ({ value: name, label: name }))}
          onChange={setSelected}
          className="min-w-[16rem]"
        />
      );
    }
    return (
      <Textarea
        aria-label={setting.label}
        value={text}
        maxLength={2000}
        rows={2}
        onChange={(event) => setText(event.target.value)}
        className="min-w-[20rem]"
      />
    );
  })();

  const hint = isNumericType(type) ? rangeHint(setting) : null;
  const bloomWarning = type === "columns" && selected.length > 5;

  return (
    <div className="tc-editor" role="group" aria-label={`Edit ${setting.label}`}>
      <div className="tc-editor-row">
        {field}
        {hint ? <span className="tc-editor-hint">Allowed: {hint}</span> : null}
      </div>
      {draft.error && amount.trim() !== "" ? <span className="tc-editor-error">{draft.error}</span> : null}
      {bloomWarning ? (
        <span className="tc-editor-hint">
          Each bloom filter adds to every data file's footer; more than five columns rarely pays off.
        </span>
      ) : null}
      {quickPicks.length ? (
        <div className="tc-quick">
          {quickPicks.map((pick) => (
            <button key={pick.label} type="button" onClick={() => setFromBase(pick.value)}>
              {pick.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="tc-editor-foot">
        <span className="tc-editor-hint">{setting.takes_effect}</span>
        <div className="tc-editor-buttons">
          {canReset ? (
            <Button size="sm" variant="ghost" onClick={() => onStage(null)}>
              <RotateCcw aria-hidden="true" />
              {setting.kind === "cryo_param" ? "Remove override" : "Reset to default"}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={Boolean(draft.error) || unchanged}
            onClick={() => onStage(draft.value)}
          >
            Stage change
          </Button>
        </div>
      </div>
    </div>
  );
};
