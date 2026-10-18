import { useEffect, useMemo, useRef, useState } from "react";
import {
  Layer,
  Rectangle,
  Sankey,
  Tooltip,
  type SankeyLinkProps,
  type SankeyNodeProps
} from "recharts";

export interface FlowNode {
  id: string;
  label: string;
  count: number;
  color: string;
  /** Interactive nodes render as keyboard-operable buttons and call `onNodeClick`. */
  clickable?: boolean;
  selected?: boolean;
}

export interface FlowLink {
  source: string;
  target: string;
  value: number;
}

const LABEL_GUTTER = 132;

const useElementWidth = () => {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => setWidth(element.clientWidth);
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
};

type NodeDatum = FlowNode & { name: string };

/**
 * Left-to-right flow of counts between stages, in the order given (no re-sorting), with labelled,
 * optionally clickable nodes. Links take the colour of the node they flow into.
 */
export const FlowSankey = ({
  nodes,
  links,
  onNodeClick,
  formatCount = (value) => value.toLocaleString(),
  unit = "",
  height = 260,
  testId
}: {
  nodes: FlowNode[];
  links: FlowLink[];
  onNodeClick?: (id: string) => void;
  formatCount?: (value: number) => string;
  unit?: string;
  height?: number;
  testId?: string;
}) => {
  const { ref, width } = useElementWidth();
  const data = useMemo(() => {
    const index = new Map(nodes.map((node, position) => [node.id, position]));
    return {
      nodes: nodes.map<NodeDatum>((node) => ({ ...node, name: node.label })),
      links: links
        .filter((link) => index.has(link.source) && index.has(link.target) && link.value > 0)
        .map((link) => ({
          source: index.get(link.source) as number,
          target: index.get(link.target) as number,
          value: link.value
        }))
    };
  }, [nodes, links]);

  const renderNode = (props: SankeyNodeProps) => {
    const { x, y, width: nodeWidth, height: nodeHeight, payload } = props;
    const node = payload as unknown as NodeDatum;
    const labelLeft = x + nodeWidth + LABEL_GUTTER > width;
    const clickable = Boolean(node.clickable && onNodeClick);
    return (
      <Layer key={node.id}>
        <Rectangle x={x} y={y} width={nodeWidth} height={Math.max(nodeHeight, 2)} fill={node.color} radius={2} />
        <g
          role={clickable ? "button" : undefined}
          tabIndex={clickable ? 0 : undefined}
          aria-label={clickable ? `${node.label}: ${formatCount(node.count)}` : undefined}
          aria-pressed={clickable ? Boolean(node.selected) : undefined}
          style={{ cursor: clickable ? "pointer" : "default" }}
          onClick={() => clickable && onNodeClick?.(node.id)}
          onKeyDown={(event) => {
            if (clickable && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              onNodeClick?.(node.id);
            }
          }}
        >
          <text
            x={labelLeft ? x - 6 : x + nodeWidth + 6}
            y={y + nodeHeight / 2}
            dy="0.35em"
            textAnchor={labelLeft ? "end" : "start"}
            style={{
              fontSize: 12,
              fill: "var(--color-text-primary)",
              fontWeight: node.selected ? 650 : 500,
              textDecoration: clickable ? "underline dotted" : undefined,
              textUnderlineOffset: 3
            }}
          >
            {node.label}
            <tspan dx={6} style={{ fill: "var(--color-text-secondary)", fontWeight: 500 }}>
              {formatCount(node.count)}
            </tspan>
          </text>
        </g>
      </Layer>
    );
  };

  const renderLink = (props: SankeyLinkProps) => {
    const { sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload } = props;
    const target = payload.target as unknown as NodeDatum;
    return (
      <path
        d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`}
        fill="none"
        stroke={target.color}
        strokeOpacity={0.22}
        strokeWidth={Math.max(linkWidth, 1)}
      />
    );
  };

  return (
    <div ref={ref} className="w-full" data-testid={testId}>
      {width > 0 && data.links.length ? (
        <Sankey
          width={width}
          height={height}
          data={data}
          sort={false}
          nodeWidth={8}
          nodePadding={14}
          iterations={0}
          margin={{ top: 8, right: LABEL_GUTTER, bottom: 8, left: 4 }}
          node={renderNode}
          link={renderLink}
        >
          <Tooltip formatter={(value) => [`${formatCount(Number(value))}${unit ? ` ${unit}` : ""}`, ""]} separator="" />
        </Sankey>
      ) : null}
    </div>
  );
};
