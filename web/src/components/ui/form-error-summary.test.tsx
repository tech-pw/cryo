import { fireEvent, render, screen } from "@testing-library/react";
import { z } from "zod";
import { describe, expect, it } from "vitest";
import { FormField, FormProvider, useZodForm } from "./form";
import { collectFormErrors, FormErrorSummary } from "./form-error-summary";
import { Input } from "./input";

describe("collectFormErrors", () => {
  it("flattens flat field errors", () => {
    const errors = {
      name: { type: "required", message: "Name is required" },
      source_id: { type: "required", message: "Source is required" }
    } as unknown as Parameters<typeof collectFormErrors>[0];
    expect(collectFormErrors(errors)).toEqual([
      { path: "name", message: "Name is required" },
      { path: "source_id", message: "Source is required" }
    ]);
  });

  it("recurses into nested field-array errors", () => {
    const errors = {
      coordinators: [undefined, { host: { type: "required", message: "Host is required" } }]
    } as unknown as Parameters<typeof collectFormErrors>[0];
    expect(collectFormErrors(errors)).toEqual([{ path: "coordinators.1.host", message: "Host is required" }]);
  });

  it("skips RHF metadata keys like root", () => {
    const errors = {
      root: { type: "manual", message: "Should be ignored" },
      name: { type: "required", message: "Name is required" }
    } as unknown as Parameters<typeof collectFormErrors>[0];
    expect(collectFormErrors(errors)).toEqual([{ path: "name", message: "Name is required" }]);
  });

  it("returns an empty list when there are no errors", () => {
    expect(collectFormErrors({})).toEqual([]);
  });
});

const schema = z.object({
  name: z.string().min(1, "Name is required"),
  source_id: z.string().min(1, "Source is required")
});

type Values = z.infer<typeof schema>;

const HarnessForm = () => {
  const form = useZodForm(schema, { defaultValues: { name: "", source_id: "" } });
  return (
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit(() => undefined)}>
        <FormErrorSummary<Values> labels={{ name: "Name", source_id: "Source" }} />
        <FormField<Values> name="name" label="Name">
          {(field) => <Input {...field} value={field.value ?? ""} />}
        </FormField>
        <FormField<Values> name="source_id" label="Source">
          {(field) => <Input {...field} value={field.value ?? ""} />}
        </FormField>
        <button type="submit">Submit</button>
      </form>
    </FormProvider>
  );
};

describe("FormField + FormErrorSummary integration", () => {
  it("gives the rendered control a stable id matching the label and links error summary entries to it", async () => {
    render(<HarnessForm />);

    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    const nameInput = await screen.findByLabelText("Name");
    expect(nameInput).toHaveAttribute("id", "field-name");
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
    expect(nameInput.getAttribute("aria-describedby")).toContain("field-name-error");

    const summary = screen.getByText("Fix the following before continuing").closest('[role="alert"]');
    expect(summary).not.toBeNull();
    expect(summary).toHaveTextContent("Name: Name is required");
    expect(summary).toHaveTextContent("Source: Source is required");

    fireEvent.click(screen.getByRole("button", { name: "Name: Name is required" }));
    expect(nameInput).toHaveFocus();
  });

  it("renders nothing once the form is valid", () => {
    render(<HarnessForm />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
