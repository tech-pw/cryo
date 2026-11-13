import { useEffect, useId, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { Layer, Rectangle, Sankey, type SankeyLinkProps, type SankeyNodeProps } from "recharts";
import "./flowSankey.css";

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

const LABEL_GUTTER = 148;
const NARROW_WIDTH = 480;
const MIN_NODE_HEIGHT = 3;
const CHAR_PX = 6.6;

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
type Focus = { kind: "node"; index: number } | { kind: "link"; index: number } | null;
interface Tip { x: number; y: number; title: string; body: string }

export const truncateLabel = (label: string, maxChars: number): string =>
  label.length <= maxChars ? label : `${label.slice(0, Math.max(1, maxChars - 1)).trimEnd()}…`;

/** Indices of links on any path through `node`: everything that feeds it and everything it feeds. */
export const linksThroughNode = (links: ReadonlyArray<{ source: number; target: number }>, node: number): Set<number> => {
  const walk = (start: number, forward: boolean) => {
    const seen = new Set<number>([start]);
    const queue = [start];
    while (queue.length) {
      const current = queue.shift() as number;
      for (const link of links) {
        const [from, to] = forward ? [link.source, link.target] : [link.target, link.source];
        if (from === current && !seen.has(to)) {
          seen.add(to);
          queue.push(to);
        }
      }
    }
    return seen;
  };
  const upstream = walk(node, false);
  const downstream = walk(node, true);
  const out = new Set<number>();
  links.forEach((link, index) => {
    if (upstream.has(link.target) || downstream.has(link.source)) out.add(index);
  });
  return out;
};

export const shareOf = (value: number, total: number): string => {
  if (!total) return "0%";
  const pct = (value / total) * 100;
  return pct > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
};

/**
 * Left-to-right flow of counts between stages, in the order given (no re-sorting), with labelled,
 * optionally clickable nodes. Hovering or focusing a node lights every path through it.
 */
export const FlowSankey = ({
  nodes,
  links,
  onNodeClick,
  formatCount = (value) => value.toLocaleString(),
  unit = "",
  height = 260,
  summary,
  tableCaption = "Flows",
  testId
}: {
  nodes: FlowNode[];
  links: FlowLink[];
  onNodeClick?: (id: string) => void;
  formatCount?: (value: number) => string;
  unit?: string;
  height?: number;
  /** One sentence with the main finding; shown above the chart and read by screen readers. */
  summary?: string;
  tableCaption?: string;
  testId?: string;
}) => {
  const { ref, width } = useElementWidth();
  const uid = useId().replace(/:/g, "");
  const [hover, setHover] = useState<Focus>(null);
  const [tip, setTip] = useState<Tip | null>(null);
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
  const outgoing = useMemo(() => {
    const totals = new Map<number, number>();
    for (const link of data.links) totals.set(link.source, (totals.get(link.source) ?? 0) + link.value);
    return totals;
  }, [data.links]);

  const selectedIndex = nodes.findIndex((node) => node.selected);
  const focus: Focus = hover ?? (selectedIndex >= 0 ? { kind: "node", index: selectedIndex } : null);
  const lit = useMemo(() => {
    if (!focus) return null;
    if (focus.kind === "link") return new Set([focus.index]);
    return linksThroughNode(data.links, focus.index);
  }, [focus, data.links]);
  const litNodes = useMemo(() => {
    if (!lit) return null;
    const out = new Set<number>();
    for (const index of lit) {
      out.add(data.links[index].source);
      out.add(data.links[index].target);
    }
    if (focus?.kind === "node") out.add(focus.index);
    return out;
  }, [lit, focus, data.links]);

  const place = (event: ReactMouseEvent, title: string, body: string) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    setTip({ x: event.clientX - box.left, y: event.clientY - box.top, title, body });
  };
  const clear = () => {
    setHover(null);
    setTip(null);
  };
  const withUnit = (value: number) => `${formatCount(value)}${unit ? ` ${unit}` : ""}`;

  const renderNode = (props: SankeyNodeProps) => {
    const { x, y, width: nodeWidth, height: nodeHeight, payload, index } = props;
    const node = payload as unknown as NodeDatum;
    const labelLeft = x + nodeWidth + LABEL_GUTTER > width;
    const clickable = Boolean(node.clickable && onNodeClick);
    const dimmed = litNodes != null && !litNodes.has(index);
    const countText = formatCount(node.count);
    const room = Math.floor((LABEL_GUTTER - 14) / CHAR_PX) - countText.length - 1;
    const label = truncateLabel(node.label, Math.max(6, room));
    const drawnHeight = Math.max(nodeHeight, MIN_NODE_HEIGHT);
    return (
      <Layer key={node.id}>
        <Rectangle
          x={x}
          y={y - (drawnHeight - nodeHeight) / 2}
          width={nodeWidth}
          height={drawnHeight}
          fill={node.color}
          radius={2}
          style={{ opacity: dimmed ? 0.35 : 1, transition: "opacity 150ms ease" }}
        />
        <g
          role={clickable ? "button" : undefined}
          tabIndex={clickable ? 0 : undefined}
          aria-label={clickable ? `${node.label}: ${withUnit(node.count)}` : undefined}
          aria-pressed={clickable ? Boolean(node.selected) : undefined}
          style={{ cursor: clickable ? "pointer" : "default", opacity: dimmed ? 0.45 : 1, transition: "opacity 150ms ease", outline: "none" }}
          className="flow-sankey-node"
          onMouseEnter={(event) => {
            setHover({ kind: "node", index });
            place(event, node.label, withUnit(node.count));
          }}
          onMouseMove={(event) => place(event, node.label, withUnit(node.count))}
          onMouseLeave={clear}
          onFocus={() => setHover({ kind: "node", index })}
          onBlur={clear}
          onClick={() => clickable && onNodeClick?.(node.id)}
          onKeyDown={(event) => {
            if (clickable && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              onNodeClick?.(node.id);
            }
            if (event.key === "Escape") clear();
          }}
        >
          <rect x={labelLeft ? x - LABEL_GUTTER : x - 2} y={y + nodeHeight / 2 - 11} width={LABEL_GUTTER + nodeWidth} height={22} fill="transparent" />
          <text
            x={labelLeft ? x - 6 : x + nodeWidth + 6}
            y={y + nodeHeight / 2}
            dy="0.35em"
            textAnchor={labelLeft ? "end" : "start"}
            className="flow-sankey-label"
            style={{
              fontSize: 12,
              fill: "var(--color-text-primary)",
              fontWeight: node.selected ? 650 : 500,
              textDecoration: clickable ? "underline dotted" : undefined,
              textUnderlineOffset: 3,
              paintOrder: "stroke",
              stroke: "var(--primitive-surface)",
              strokeWidth: 4,
              strokeLinejoin: "round"
            }}
          >
            {label !== node.label ? <title>{node.label}</title> : null}
            {label}
            <tspan dx={6} style={{ fill: "var(--color-text-secondary)", fontWeight: 600 }}>
              {countText}
            </tspan>
          </text>
        </g>
      </Layer>
    );
  };

  const renderLink = (props: SankeyLinkProps) => {
    const { sourceX, targetX, sourceY, targetY, sourceControlX, targetControlX, linkWidth, payload, index } = props;
    const source = payload.source as unknown as NodeDatum & { index?: number };
    const target = payload.target as unknown as NodeDatum;
    const on = lit?.has(index) ?? false;
    const opacity = lit == null ? 0.34 : on ? 0.62 : 0.07;
    const gradientId = `${uid}-flow-${index}`;
    const sourceTotal = outgoing.get(data.links[index]?.source ?? -1) ?? 0;
    const body = `${withUnit(payload.value)} · ${shareOf(payload.value, sourceTotal)} of ${source.label}`;
    return (
      <g key={gradientId}>
        <defs>
          <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1={sourceX} x2={targetX} y1={0} y2={0}>
            <stop offset="0%" stopColor={source.color} />
            <stop offset="100%" stopColor={target.color} />
          </linearGradient>
        </defs>
        <path
          d={`M${sourceX},${sourceY} C${sourceControlX},${sourceY} ${targetControlX},${targetY} ${targetX},${targetY}`}
          fill="none"
          stroke={`url(#${gradientId})`}
          strokeOpacity={opacity}
          strokeWidth={Math.max(linkWidth, 1.5)}
          style={{ transition: "stroke-opacity 150ms ease" }}
          data-lit={on ? "true" : undefined}
          onMouseEnter={(event) => {
            setHover({ kind: "link", index });
            place(event, `${source.label} → ${target.label}`, body);
          }}
          onMouseMove={(event) => place(event, `${source.label} → ${target.label}`, body)}
          onMouseLeave={clear}
        />
      </g>
    );
  };

  const terminals = nodes.filter((node, index) => !data.links.some((link) => link.source === index) && data.links.some((link) => link.target === index));
  const terminalTotal = terminals.reduce((sum, node) => sum + node.count, 0);
  const narrow = width > 0 && width < NARROW_WIDTH;

  return (
    <div className="flow-sankey" data-testid={testId}>
      {summary ? <p className="flow-sankey-summary">{summary}</p> : null}
      <div ref={ref} className="flow-sankey-canvas" onMouseLeave={clear}>
        {width > 0 && data.links.length && !narrow ? (
          <Sankey
            width={width}
            height={height}
            data={data}
            sort={false}
            align="left"
            nodeWidth={10}
            nodePadding={16}
            iterations={0}
            margin={{ top: 10, right: LABEL_GUTTER, bottom: 10, left: 4 }}
            node={renderNode}
            link={renderLink}
          />
        ) : null}
        {narrow && terminalTotal > 0 ? (
          <div className="flow-sankey-narrow" data-testid={testId ? `${testId}-narrow` : undefined}>
            <div className="flow-sankey-bar" aria-hidden="true">
              {terminals.map((node) => <span key={node.id} style={{ width: `${(node.count / terminalTotal) * 100}%`, background: node.color }} />)}
            </div>
            <ul>
              {terminals.map((node) => {
                const body = <><span className="flow-sankey-swatch" style={{ background: node.color }} /><span>{node.label}</span><strong>{formatCount(node.count)}</strong><span className="flow-sankey-share">{shareOf(node.count, terminalTotal)}</span></>;
                return <li key={node.id}>{node.clickable && onNodeClick ? <button type="button" onClick={() => onNodeClick(node.id)}>{body}</button> : <span className="flow-sankey-row">{body}</span>}</li>;
              })}
            </ul>
          </div>
        ) : null}
        {tip ? (
          <div className="flow-sankey-tip" role="presentation" style={{ left: Math.min(tip.x + 14, Math.max(0, width - 220)), top: tip.y + 14 }}>
            <strong>{tip.title}</strong>
            <span>{tip.body}</span>
          </div>
        ) : null}
      </div>
      {data.links.length ? (
        <details className="flow-sankey-table">
          <summary>Show as table</summary>
          <table>
            <caption className="sr-only">{tableCaption}</caption>
            <thead><tr><th scope="col">From</th><th scope="col">To</th><th scope="col">{unit ? unit[0].toUpperCase() + unit.slice(1) : "Count"}</th><th scope="col">Share of “from”</th></tr></thead>
            <tbody>
              {data.links.map((link, index) => (
                <tr key={index}>
                  <td>{nodes[link.source].label}</td>
                  <td>{nodes[link.target].label}</td>
                  <td>{formatCount(link.value)}</td>
                  <td>{shareOf(link.value, outgoing.get(link.source) ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
    </div>
  );
};
