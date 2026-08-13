import { useEffect, useMemo, useRef, useState } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum
} from "d3-force";
import type { SimilarityClusterRead, SimilarityPairRead } from "../../api/types";
import { Badge } from "../ui/badge";
import { formatGb } from "../../lib/format";
import { cryoColors } from "../../lib/theme";

/**
 * Real force-directed layout for one cluster's member tables -- not a cosmetic scatter.
 * Node positions are driven by d3-force's spring simulation, with edge (link) strength
 * proportional to each pair's real similarity_score: tables that scored more similar to
 * each other pull together harder, so the resulting 2D layout genuinely reflects
 * similarity space for this cluster (the same mathematical family as MDS/PCA on a
 * distance matrix, adapted for the sparse pairwise graph this pipeline actually
 * produces -- see docs/TABLE_DEDUPLICATION_DESIGN.md for why a full distance matrix
 * across all ~5,245 clustered tables isn't computed: most table pairs were never scored
 * at all, only Stage-1-blocked candidates were).
 *
 * Deliberately one graph per CLUSTER, not one graph across the whole fleet: clusters are
 * disjoint by construction (union-find, see dedup.clustering), so a fleet-wide force
 * layout would just show ~498 physically separate blobs with no meaningful inter-cluster
 * forces to compute -- better to render each cluster's real internal structure clearly at
 * a readable scale than one unreadable graph of 5,245 points.
 */

interface GraphNode extends SimulationNodeDatum {
  id: string;
  table_name: string;
  schema_name: string;
  total_size_gb: number;
  role: "keep" | "drop_candidate";
  keep_reason: string | null;
}

interface GraphLink extends SimulationLinkDatum<GraphNode> {
  similarity_score: number;
}

const roleVariant = (role: "keep" | "drop_candidate") => (role === "keep" ? "success" : "warning");

const radiusForSize = (sizeGb: number): number => Math.max(8, Math.min(34, 6 + Math.sqrt(sizeGb) * 2.2));

export const ClusterForceGraph = ({
  cluster,
  pairs,
  width = 560,
  height = 340
}: {
  cluster: SimilarityClusterRead;
  pairs: SimilarityPairRead[];
  width?: number;
  height?: number;
}) => {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const simulationRef = useRef<ReturnType<typeof forceSimulation<GraphNode>> | null>(null);

  const initialNodes = useMemo<GraphNode[]>(
    () =>
      cluster.members.map((m) => ({
        id: m.table_id,
        table_name: m.table_name,
        schema_name: m.schema_name,
        total_size_gb: m.total_size_gb,
        role: m.role,
        keep_reason: m.keep_reason
      })),
    [cluster.cluster_id, cluster.members]
  );

  const initialLinks = useMemo<GraphLink[]>(() => {
    const memberIds = new Set(initialNodes.map((n) => n.id));
    return pairs
      .filter(
        (p) =>
          memberIds.has(p.table_a_id) &&
          memberIds.has(p.table_b_id) &&
          p.similarity_score !== null
      )
      .map((p) => ({
        source: p.table_a_id,
        target: p.table_b_id,
        similarity_score: p.similarity_score ?? 0
      }));
  }, [initialNodes, pairs]);

  useEffect(() => {
    if (initialNodes.length === 0) return undefined;

    // Real physics: link distance is INVERSE to similarity (more similar -> pulled
    // closer), charge repels nodes apart so they don't overlap, collide prevents bubbles
    // colliding given their real size-driven radius, center keeps the whole cluster
    // roughly centered in the SVG viewport.
    const simulation = forceSimulation<GraphNode>(initialNodes)
      .force(
        "link",
        forceLink<GraphNode, GraphLink>(initialLinks)
          .id((d) => d.id)
          .distance((l) => 140 * (1 - l.similarity_score) + 20)
          .strength((l) => Math.max(0.05, l.similarity_score))
      )
      .force("charge", forceManyBody().strength(-90))
      .force(
        "collide",
        forceCollide<GraphNode>().radius((d) => radiusForSize(d.total_size_gb) + 4)
      )
      .force("center", forceCenter(width / 2, height / 2))
      .stop();

    simulationRef.current = simulation;
    simulation.tick(150); // run synchronously to a settled layout, no animated jitter
    setNodes([...simulation.nodes()]);
    setLinks([...initialLinks]);

    return () => {
      simulation.stop();
    };
  }, [initialNodes, initialLinks, width, height]);

  if (initialNodes.length === 0) {
    return null;
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Similarity layout for a ${cluster.members.length}-table cluster`}
      className="rounded-md bg-[var(--color-surface-sunken)]"
    >
      <g>
        {links.map((link, i) => {
          const source = link.source as GraphNode;
          const target = link.target as GraphNode;
          if (typeof source !== "object" || typeof target !== "object") return null;
          return (
            <line
              key={i}
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              stroke={cryoColors.border}
              strokeWidth={Math.max(0.5, link.similarity_score * 3)}
              strokeOpacity={0.5 + link.similarity_score * 0.4}
            />
          );
        })}
      </g>
      <g>
        {nodes.map((node) => {
          const isHovered = hoveredId === node.id;
          const radius = radiusForSize(node.total_size_gb);
          return (
            <g
              key={node.id}
              transform={`translate(${node.x ?? 0}, ${node.y ?? 0})`}
              onMouseEnter={() => setHoveredId(node.id)}
              onMouseLeave={() => setHoveredId((cur) => (cur === node.id ? null : cur))}
              style={{ cursor: "pointer" }}
            >
              <circle
                r={radius}
                fill={node.role === "keep" ? cryoColors.gradeA : cryoColors.gradeD}
                fillOpacity={isHovered ? 0.95 : 0.75}
                stroke={node.role === "keep" ? cryoColors.gradeA : "transparent"}
                strokeWidth={node.role === "keep" ? 2 : 0}
              />
              {isHovered ? (
                <g transform={`translate(${radius + 6}, 0)`}>
                  <foreignObject x={0} y={-28} width={220} height={80} style={{ overflow: "visible" }}>
                    <div className="max-w-[220px] rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2 text-xs shadow-lg">
                      <p className="truncate font-semibold text-foreground">
                        {node.schema_name}.{node.table_name}
                      </p>
                      <p className="mt-0.5 text-muted-foreground">{formatGb(node.total_size_gb)}</p>
                      <Badge variant={roleVariant(node.role)} className="mt-1 text-[10px]">
                        {node.role === "keep" ? "Keep" : "Drop candidate"}
                      </Badge>
                    </div>
                  </foreignObject>
                </g>
              ) : null}
            </g>
          );
        })}
      </g>
    </svg>
  );
};
