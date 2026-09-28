import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { LineageGraphRead, LineageNodeRead } from "../../api/client";
import { Button } from "../ui/button";
import { EmptyState } from "../ui/empty-state";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle
} from "../ui/sheet";

const LINEAGE_DOCS_URL =
  "https://github.com/shelf-project/Cryo/blob/main/docs/architecture.md#26-gnn-based-joinlineage-discovery-omnimatchautogjointdeepjoin";

const NODE_WIDTH = 148;
const NODE_HEIGHT = 44;
const COLUMN_GAP = 120;
const ROW_GAP = 16;
const PADDING = 32;

interface GraphNode {
  id: string;
  label: string;
  role: "root" | "upstream" | "downstream";
  nodeType?: string;
  depth?: number;
}

interface GraphEdge {
  from: string;
  to: string;
}

interface LayoutResult {
  nodes: Array<GraphNode & { x: number; y: number }>;
  edges: GraphEdge[];
  width: number;
  height: number;
}

function layoutLineageGraph(data: LineageGraphRead): LayoutResult {
  const upstream = data.upstream ?? [];
  const downstream = data.downstream ?? [];
  const rootId = data.root_table_id;

  const nodes: GraphNode[] = [
    { id: rootId, label: data.root_table_name, role: "root" },
    ...upstream.map((node) => toGraphNode(node, "upstream")),
    ...downstream.map((node) => toGraphNode(node, "downstream"))
  ];

  const upstreamCount = upstream.length;
  const downstreamCount = downstream.length;
  const maxRows = Math.max(1, upstreamCount, downstreamCount);

  const height = PADDING * 2 + maxRows * NODE_HEIGHT + Math.max(0, maxRows - 1) * ROW_GAP;
  const width = PADDING * 2 + NODE_WIDTH * 3 + COLUMN_GAP * 2;

  const centerY = height / 2 - NODE_HEIGHT / 2;
  const upstreamX = PADDING;
  const rootX = PADDING + NODE_WIDTH + COLUMN_GAP;
  const downstreamX = rootX + NODE_WIDTH + COLUMN_GAP;

  const positioned = nodes.map((node) => {
    if (node.role === "root") {
      return { ...node, x: rootX, y: centerY };
    }
    if (node.role === "upstream") {
      const index = upstream.findIndex((n) => n.table_id === node.id);
      const blockHeight = upstreamCount * NODE_HEIGHT + Math.max(0, upstreamCount - 1) * ROW_GAP;
      const startY = (height - blockHeight) / 2;
      return { ...node, x: upstreamX, y: startY + index * (NODE_HEIGHT + ROW_GAP) };
    }
    const index = downstream.findIndex((n) => n.table_id === node.id);
    const blockHeight = downstreamCount * NODE_HEIGHT + Math.max(0, downstreamCount - 1) * ROW_GAP;
    const startY = (height - blockHeight) / 2;
    return { ...node, x: downstreamX, y: startY + index * (NODE_HEIGHT + ROW_GAP) };
  });

  const edges: GraphEdge[] = [
    ...upstream.map((node) => ({ from: node.table_id, to: rootId })),
    ...downstream.map((node) => ({ from: rootId, to: node.table_id }))
  ];

  return { nodes: positioned, edges, width, height };
}

function LineageList({ title, nodes }: { title: string; nodes: LineageNodeRead[] }) {
  return (
    <section aria-label={title}>
      <h3 className="mb-1 text-sm font-medium">{title}</h3>
      {nodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">None</p>
      ) : (
        <ul className="m-0 list-none space-y-1 p-0 text-sm">
          {nodes.map((node) => (
            <li key={node.table_id}>
              <Link to={`/tables/${node.table_id}`}>{node.table_name}</Link>
              {node.depth != null ? <span className="text-muted-foreground"> · depth {node.depth}</span> : null}
              {node.node_type ? <span className="text-muted-foreground"> · {node.node_type}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function toGraphNode(node: LineageNodeRead, role: "upstream" | "downstream"): GraphNode {
  return {
    id: node.table_id,
    label: node.table_name,
    role,
    nodeType: node.node_type,
    depth: node.depth
  };
}

function edgePath(
  from: { x: number; y: number },
  to: { x: number; y: number },
  direction: "left-to-right" | "right-from-left"
): string {
  const startX = direction === "left-to-right" ? from.x + NODE_WIDTH : from.x;
  const endX = direction === "left-to-right" ? to.x : to.x + NODE_WIDTH;
  const startY = from.y + NODE_HEIGHT / 2;
  const endY = to.y + NODE_HEIGHT / 2;
  const midX = (startX + endX) / 2;
  return `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`;
}

export interface LineageGraphProps {
  data: LineageGraphRead | null | undefined;
  isLoading?: boolean;
  error?: Error | null;
}

export const LineageGraph = ({ data, isLoading, error }: LineageGraphProps) => {
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const layout = useMemo(() => (data ? layoutLineageGraph(data) : null), [data]);
  const nodeById = useMemo(() => {
    const map = new Map<string, GraphNode & { x: number; y: number }>();
    layout?.nodes.forEach((node) => map.set(node.id, node));
    return map;
  }, [layout]);

  if (isLoading) {
    return <EmptyState title="Loading lineage…" description="Tracing upstream and downstream tables." />;
  }

  if (error) {
    return (
      <EmptyState
        title="Failed to load lineage"
        description={error.message || "The lineage service returned an error."}
      />
    );
  }

  if (!data) {
    return (
      <EmptyState
        title="Lineage unavailable"
        description="Lineage is built from query-log ingestion. Connect telemetry and run workloads to populate edges."
        action={
          <Button variant="outline" asChild>
            <a href={LINEAGE_DOCS_URL} target="_blank" rel="noreferrer">
              Read lineage docs
            </a>
          </Button>
        }
      />
    );
  }

  const hasEdges = (data.upstream?.length ?? 0) > 0 || (data.downstream?.length ?? 0) > 0;

  if (!hasEdges) {
    return (
      <EmptyState
        title="No lineage edges yet"
        description={`No upstream or downstream tables are linked to ${data.root_table_name}. Cryo records lineage from Trino and Spark query logs.`}
        action={
          <Button variant="outline" asChild>
            <a href={LINEAGE_DOCS_URL} target="_blank" rel="noreferrer">
              How lineage is collected
            </a>
          </Button>
        }
      />
    );
  }

  if (!layout) return null;

  return (
    <>
      <div className="lineage-graph-shell">
        <div className="lineage-graph-legend" aria-hidden>
          <span className="lineage-legend-item">
            <span className="lineage-legend-swatch lineage-legend-swatch--upstream" />
            Upstream
          </span>
          <span className="lineage-legend-item">
            <span className="lineage-legend-swatch lineage-legend-swatch--root" />
            Current table
          </span>
          <span className="lineage-legend-item">
            <span className="lineage-legend-swatch lineage-legend-swatch--downstream" />
            Downstream
          </span>
        </div>

        <svg
          className="lineage-graph-canvas"
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="img"
          aria-label={`Lineage graph for ${data.root_table_name}`}
        >
          <defs>
            <marker
              id="lineage-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
              markerUnits="strokeWidth"
            >
              <path d="M0,0 L8,4 L0,8 Z" fill="var(--color-muted-foreground, #8b95a5)" />
            </marker>
          </defs>

          {layout.edges.map((edge) => {
            const from = nodeById.get(edge.from);
            const to = nodeById.get(edge.to);
            if (!from || !to) return null;
            const direction = from.x < to.x ? "left-to-right" : "right-from-left";
            return (
              <path
                key={`${edge.from}-${edge.to}`}
                d={edgePath(from, to, direction)}
                className="lineage-graph-edge"
                markerEnd="url(#lineage-arrow)"
                fill="none"
              />
            );
          })}

          {layout.nodes.map((node) => (
            <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
              <foreignObject width={NODE_WIDTH} height={NODE_HEIGHT}>
                <button
                  type="button"
                  className={`lineage-graph-node lineage-graph-node--${node.role}`}
                  onClick={() => setSelectedNode(node)}
                  aria-label={`${node.label}, ${node.role}`}
                >
                  <span className="lineage-graph-node__label">{node.label}</span>
                  {node.nodeType ? (
                    <span className="lineage-graph-node__meta">{node.nodeType}</span>
                  ) : null}
                </button>
              </foreignObject>
            </g>
          ))}
        </svg>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <LineageList title="Upstream" nodes={data.upstream ?? []} />
          <LineageList title="Downstream" nodes={data.downstream ?? []} />
        </div>
      </div>

      <Sheet open={selectedNode !== null} onOpenChange={(open) => !open && setSelectedNode(null)}>
        <SheetContent side="right" className="sm:max-w-md">
          {selectedNode ? (
            <>
              <SheetHeader>
                <SheetTitle>{selectedNode.label}</SheetTitle>
                <SheetDescription>
                  {selectedNode.role === "root"
                    ? "Current table"
                    : selectedNode.role === "upstream"
                      ? "Upstream dependency"
                      : "Downstream consumer"}
                </SheetDescription>
              </SheetHeader>
              <dl className="lineage-node-sheet-meta">
                {selectedNode.nodeType ? (
                  <>
                    <dt>Edge type</dt>
                    <dd>{selectedNode.nodeType}</dd>
                  </>
                ) : null}
                {selectedNode.depth != null ? (
                  <>
                    <dt>Depth</dt>
                    <dd>{selectedNode.depth}</dd>
                  </>
                ) : null}
              </dl>
              <div className="mt-6">
                <Button variant="primary" asChild>
                  <Link to={`/tables/${selectedNode.id}`}>Open table detail</Link>
                </Button>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  );
};

