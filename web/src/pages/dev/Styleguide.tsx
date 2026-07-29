import { type ColumnDef } from "@tanstack/react-table";
import { Moon, Sun } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { AsyncBoundary } from "../../components/AsyncBoundary";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger
} from "../../components/ui/alert-dialog";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { ChartPanel } from "../../components/ui/chart-panel";
import { Checkbox } from "../../components/ui/checkbox";
import { DataTable, type TableDensity } from "../../components/ui/data-table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from "../../components/ui/dialog";
import { EmptyState } from "../../components/ui/empty-state";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { MetricGrid, MetricTile } from "../../components/ui/metric-grid";
import { PageHeader } from "../../components/ui/page-header";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Separator } from "../../components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger
} from "../../components/ui/sheet";
import { Skeleton } from "../../components/ui/skeleton";
import { Switch } from "../../components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Textarea } from "../../components/ui/textarea";
import { CryoTooltipProvider, Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      {children}
    </section>
  );
}

type DemoTableRow = {
  id: string;
  table: string;
  grade: string;
  smallFiles: number;
};

const demoChartData = [
  { day: "Mon", score: 82 },
  { day: "Tue", score: 79 },
  { day: "Wed", score: 84 },
  { day: "Thu", score: 81 },
  { day: "Fri", score: 86 }
];

const demoTableRows: DemoTableRow[] = [
  { id: "1", table: "analytics.events", grade: "C", smallFiles: 1284 },
  { id: "2", table: "core.users", grade: "B", smallFiles: 412 },
  { id: "3", table: "billing.invoices", grade: "F", smallFiles: 3921 },
  { id: "4", table: "ops.job_runs", grade: "A", smallFiles: 88 }
];

export function Styleguide() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [asyncDemo, setAsyncDemo] = useState<"loading" | "error" | "empty" | "ready">("ready");
  const [switchOn, setSwitchOn] = useState(true);
  const [checked, setChecked] = useState(true);
  const [tableDensity, setTableDensity] = useState<TableDensity>("default");
  const [tablePage, setTablePage] = useState(0);
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});

  const tableColumns = useMemo<ColumnDef<DemoTableRow, unknown>[]>(
    () => [
      {
        accessorKey: "table",
        header: "Table",
        cell: ({ row }) => <span className="font-medium">{row.original.table}</span>
      },
      {
        accessorKey: "grade",
        header: "Grade",
        cell: ({ row }) => <Badge variant={`grade${row.original.grade}` as "gradeA"}>{row.original.grade}</Badge>
      },
      {
        accessorKey: "smallFiles",
        header: "Small files",
        cell: ({ row }) => row.original.smallFiles.toLocaleString()
      }
    ],
    []
  );

  return (
    <div data-theme={theme} className="min-h-screen bg-[var(--color-layout-bg)] p-6 text-foreground">
      <div className="mx-auto flex max-w-6xl flex-col gap-10">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Cryo design system</p>
            <h1 className="mt-1 text-2xl font-semibold">Phase 0 styleguide</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Radix + Tailwind primitives, tokens, and async patterns for the frontend revamp.
            </p>
          </div>
          <Button
            variant="secondary"
            onClick={() => setTheme((current) => (current === "light" ? "dark" : "light"))}
          >
            {theme === "light" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
            {theme === "light" ? "Switch to dark" : "Switch to light"}
          </Button>
        </header>

        <CryoTooltipProvider>
          <div className="grid gap-10">
            <Section title="Buttons">
              <div className="flex flex-wrap gap-3">
                <Button variant="primary">Assign preset</Button>
                <Button variant="secondary">Preview plan</Button>
                <Button variant="ghost">Cancel</Button>
                <Button variant="danger">Delete preset</Button>
                <Button variant="primary" loading>
                  Running
                </Button>
              </div>
              <div className="flex flex-wrap gap-3">
                <Button size="sm" variant="secondary">
                  Small
                </Button>
                <Button size="default" variant="secondary">
                  Default
                </Button>
                <Button size="lg" variant="secondary">
                  Large
                </Button>
              </div>
            </Section>

            <Section title="Form controls">
              <div className="grid max-w-xl gap-4">
                <div className="space-y-2">
                  <Label htmlFor="source-name">Source name</Label>
                  <Input id="source-name" placeholder="prod-iceberg" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="notes">Notes</Label>
                  <Textarea id="notes" placeholder="Connection notes" />
                </div>
                <div className="flex items-center gap-6">
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={checked} onCheckedChange={(value) => setChecked(value === true)} />
                    Enable auto-scan
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Switch checked={switchOn} onCheckedChange={setSwitchOn} />
                    Off-peak only
                  </label>
                </div>
              </div>
            </Section>

            <Section title="Badges">
              <div className="flex flex-wrap gap-2">
                <Badge>Primary</Badge>
                <Badge variant="secondary">Metadata</Badge>
                <Badge variant="outline">Outline</Badge>
                <Badge variant="success">Succeeded</Badge>
                <Badge variant="warning">Deferred</Badge>
                <Badge variant="danger">Failed</Badge>
                <Badge variant="gradeA">A</Badge>
                <Badge variant="gradeC">C</Badge>
                <Badge variant="gradeF">F</Badge>
              </div>
            </Section>

            <Section title="Cards & metrics">
              <MetricGrid columns={4}>
                <MetricTile label="Tables scanned" value="1,284" delta={{ value: "+3.2% vs 7d", tone: "good" }} />
                <MetricTile label="Small-file pressure" value="18.4%" suffix="of files" delta={{ value: "+1.1% vs 7d", tone: "bad" }} />
                <MetricTile label="Est. monthly cost" value="$4,820" emphasis />
                <MetricTile label="Reclaimable" value="126" suffix="GB" loading />
              </MetricGrid>
              <Card>
                <CardHeader>
                  <CardTitle>Maintenance posture</CardTitle>
                  <CardDescription>Compact panel using shared card primitive.</CardDescription>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground">
                  Operator surfaces stay neutral; color is reserved for status and grades.
                </CardContent>
              </Card>
            </Section>

            <Section title="Page header">
              <PageHeader
                title="Catalogs"
                subtitle="Find tables matching a maintenance need and assign work."
                actions={
                  <>
                    <Button variant="secondary">Export</Button>
                    <Button variant="primary">Run scan</Button>
                  </>
                }
              />
            </Section>

            <Section title="Chart panel">
              <div className="grid gap-4 lg:grid-cols-2">
                <ChartPanel
                  title="Fleet health trend"
                  description="30-day average score"
                  footer="View tables at grade D/F →"
                >
                  <BarChart data={demoChartData}>
                    <CartesianGrid vertical={false} stroke="var(--color-chart-grid)" />
                    <XAxis dataKey="day" tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} />
                    <YAxis tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} width={28} />
                    <Bar dataKey="score" fill="var(--color-chart-primary)" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ChartPanel>
                <ChartPanel
                  title="No scan history yet"
                  description="Trend available after your next scan"
                  empty
                  emptyTitle="Trend unavailable"
                  emptyDescription="Run at least two fleet scans to plot health over time."
                />
              </div>
            </Section>

            <Section title="Data table">
              <DataTable
                columns={tableColumns}
                data={demoTableRows}
                density={tableDensity}
                onDensityChange={setTableDensity}
                showDensityToggle
                stickyFirstColumn
                enableRowSelection
                rowSelection={rowSelection}
                onRowSelectionChange={setRowSelection}
                pagination={{
                  pageIndex: tablePage,
                  pageSize: 25,
                  totalRows: 1284,
                  onPageChange: setTablePage,
                  onPageSizeChange: () => undefined
                }}
                toolbar={<p className="text-sm text-muted-foreground">1,284 tables match current filters</p>}
              />
            </Section>

            <Section title="Overlays">
              <div className="flex flex-wrap gap-3">
                <Dialog>
                  <DialogTrigger asChild>
                    <Button variant="secondary">Open dialog</Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Preview maintenance plan</DialogTitle>
                      <DialogDescription>Estimated load and affected partitions for the current selection.</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                      <Button variant="secondary">Close</Button>
                      <Button variant="primary">Run maintenance</Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>

                <Sheet>
                  <SheetTrigger asChild>
                    <Button variant="secondary">Open sheet</Button>
                  </SheetTrigger>
                  <SheetContent>
                    <SheetHeader>
                      <SheetTitle>Run logs</SheetTitle>
                      <SheetDescription>Inspection drawer preserves table context.</SheetDescription>
                    </SheetHeader>
                  </SheetContent>
                </Sheet>

                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="secondary">Open popover</Button>
                  </PopoverTrigger>
                  <PopoverContent>
                    <p className="text-sm">Filter chips and quick actions can live here.</p>
                  </PopoverContent>
                </Popover>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost">Hover tooltip</Button>
                  </TooltipTrigger>
                  <TooltipContent>Deferred by cooldown</TooltipContent>
                </Tooltip>

                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="danger">Confirm action</Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Run maintenance on 42 tables?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This will enqueue compaction for all tables matching the current filter.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction>Run now</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            </Section>

            <Section title="Tabs">
              <Tabs defaultValue="overview">
                <TabsList>
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="activity">Activity</TabsTrigger>
                  <TabsTrigger value="cost">Cost</TabsTrigger>
                </TabsList>
                <TabsContent value="overview" className="text-sm text-muted-foreground">
                  Overview tab content with cross-fade transition.
                </TabsContent>
                <TabsContent value="activity" className="text-sm text-muted-foreground">
                  Activity tab content.
                </TabsContent>
                <TabsContent value="cost" className="text-sm text-muted-foreground">
                  Cost tab content.
                </TabsContent>
              </Tabs>
            </Section>

            <Section title="Async states">
              <div className="flex flex-wrap gap-2">
                {(["loading", "error", "empty", "ready"] as const).map((state) => (
                  <Button
                    key={state}
                    size="sm"
                    variant={asyncDemo === state ? "primary" : "secondary"}
                    onClick={() => setAsyncDemo(state)}
                  >
                    {state}
                  </Button>
                ))}
              </div>
              <Separator />
              <AsyncBoundary
                isLoading={asyncDemo === "loading"}
                isError={asyncDemo === "error"}
                error={new Error("Engine at capacity")}
                isEmpty={asyncDemo === "empty"}
                emptyTitle="No tables match this filter"
                emptyDescription="Adjust source, schema, or health grade filters to widen the result set."
                emptyAction={<Button variant="primary">Clear filters</Button>}
                onRetry={() => setAsyncDemo("ready")}
              >
                <Card>
                  <CardHeader>
                    <CardTitle>Loaded content</CardTitle>
                    <CardDescription>Skeleton cross-fades into this panel via AsyncBoundary.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Skeleton className="h-4 w-full" />
                  </CardContent>
                </Card>
              </AsyncBoundary>
            </Section>

            <Section title="Empty state">
              <EmptyState
                title="Connect a source to begin"
                description="Cryo needs at least one Iceberg catalog before scans and maintenance can run."
                action={<Button variant="primary">Open connections</Button>}
              />
            </Section>

            <Section title="Sidebar tokens (fixed dark)">
              <div
                className="rounded-[var(--radius-default)] p-4"
                style={{
                  background: "var(--sidebar-bg)",
                  color: "var(--sidebar-text)"
                }}
              >
                <p className="text-sm">Sidebar navigation rail</p>
                <p className="mt-2 text-sm" style={{ color: "var(--sidebar-text-active)" }}>
                  Active item
                </p>
              </div>
            </Section>
          </div>
        </CryoTooltipProvider>
      </div>
    </div>
  );
}

export default Styleguide;
