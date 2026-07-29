import { Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { PageHeader } from "../../components/common";
import { Card, CardContent } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";

export type WorkflowTabKey = "overview" | "grid" | "graph" | "runs" | "config";

interface WorkflowDetailShellProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  hiddenTabs?: WorkflowTabKey[];
  overview: ReactNode;
  grid: ReactNode;
  graph: ReactNode;
  runs: ReactNode;
  config: ReactNode;
  defaultTab?: WorkflowTabKey;
}

const LazyPane = ({ children }: { children: ReactNode }) => (
  <Suspense
    fallback={
      <div className="py-6">
        <Skeleton className="mx-auto h-8 w-32" />
      </div>
    }
  >
    {children}
  </Suspense>
);

export const WorkflowDetailShell = ({
  title,
  subtitle,
  actions,
  hiddenTabs = [],
  overview,
  grid,
  graph,
  runs,
  config,
  defaultTab = "overview"
}: WorkflowDetailShellProps) => {
  const [activeTab, setActiveTab] = useState<WorkflowTabKey>(defaultTab);

  useEffect(() => {
    setActiveTab(defaultTab);
  }, [defaultTab]);

  const items = useMemo(() => {
    const all: Array<{ key: WorkflowTabKey; label: string; children: ReactNode }> = [
      { key: "overview", label: "Overview", children: overview },
      { key: "grid", label: "Grid", children: <LazyPane>{grid}</LazyPane> },
      { key: "graph", label: "Graph", children: <LazyPane>{graph}</LazyPane> },
      { key: "runs", label: "Runs", children: <LazyPane>{runs}</LazyPane> },
      { key: "config", label: "Config", children: <LazyPane>{config}</LazyPane> }
    ];
    return all.filter((item) => !hiddenTabs.includes(item.key));
  }, [config, graph, grid, hiddenTabs, overview, runs]);

  return (
    <div>
      <PageHeader title={title} subtitle={subtitle} actions={actions} rootCrumb={{ label: "Operations", to: "/operations" }} />
      <Card>
        <CardContent className="pt-4">
          <Tabs value={activeTab} onValueChange={(key) => setActiveTab(key as WorkflowTabKey)}>
            <TabsList>
              {items.map((item) => (
                <TabsTrigger key={item.key} value={item.key}>
                  {item.label}
                </TabsTrigger>
              ))}
            </TabsList>
            {items.map((item) => (
              <TabsContent key={item.key} value={item.key}>
                {item.children}
              </TabsContent>
            ))}
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
};
