// @ts-nocheck
import {
  Button,
  H2,
  mergeStyle,
  Pill,
  Row,
  Stack,
  Text,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

type LayerId = "frontend" | "rag" | "aiGateway" | "data";

type StackNode = {
  id: string;
  layer: LayerId;
  label: string;
  sublabel?: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

type StackEdge = {
  id: string;
  from: string;
  to: string;
  path?: string;
  primary?: boolean;
};

const VIEW_W = 580;
const VIEW_H = 480;

const LAYER_TITLES: Record<LayerId, string> = {
  frontend: "前端 · Next.js",
  rag: "RAG 运行时 · LangChain / LangSmith",
  aiGateway: "模型接入 · LangChain 调用",
  data: "数据层 · PostgreSQL / pgvector",
};

const STACK_NODES: StackNode[] = [
  {
    id: "next",
    layer: "frontend",
    label: "Next.js",
    sublabel: "App Router 14.2.30",
    x: 24,
    y: 48,
    width: 168,
    height: 48,
  },
  {
    id: "react",
    layer: "frontend",
    label: "React",
    sublabel: "18.3.1",
    x: 206,
    y: 48,
    width: 168,
    height: 48,
  },
  {
    id: "node",
    layer: "frontend",
    label: "Node.js",
    sublabel: "推荐 22.x · 最低 18.x",
    x: 388,
    y: 48,
    width: 168,
    height: 48,
  },
  {
    id: "langsmith",
    layer: "rag",
    label: "LangSmith",
    sublabel: "traceable · 链路追踪",
    x: 24,
    y: 118,
    width: 168,
    height: 52,
  },
  {
    id: "langchain",
    layer: "rag",
    label: "LangChain",
    sublabel: "切分 · 检索 · 编排",
    x: 206,
    y: 118,
    width: 168,
    height: 52,
  },
  {
    id: "fastapi-note",
    layer: "rag",
    label: "FastAPI 应用",
    sublabel: "/v1 · 知识库 · SSE",
    x: 388,
    y: 118,
    width: 168,
    height: 52,
  },
  {
    id: "ai-gateway",
    layer: "aiGateway",
    label: "AI API Gateway",
    sublabel: "LangChain 接入 OpenAI 模型",
    x: 100,
    y: 208,
    width: 380,
    height: 48,
  },
  {
    id: "openai-svc",
    layer: "aiGateway",
    label: "OpenAI",
    sublabel: "Chat · Embedding · Vision",
    x: 206,
    y: 272,
    width: 168,
    height: 44,
  },
  {
    id: "postgres",
    layer: "data",
    label: "PostgreSQL",
    sublabel: "会话 / 元数据",
    x: 120,
    y: 348,
    width: 168,
    height: 52,
  },
  {
    id: "pgvector",
    layer: "data",
    label: "pgvector",
    sublabel: "HNSW · kb_data / kb_index",
    x: 308,
    y: 348,
    width: 168,
    height: 52,
  },
];

/** 画布中心 x（前端 / LangChain / Gateway 对齐） */
const CENTER_X = 290;

const STACK_EDGES: StackEdge[] = [
  {
    id: "e-fe-lc",
    from: "react",
    to: "langchain",
    primary: true,
    path: `M ${CENTER_X} 96 L ${CENTER_X} 118`,
  },
  { id: "e-ls-lc", from: "langsmith", to: "langchain", path: "M 108 144 L 206 144" },
  { id: "e-api-lc", from: "fastapi-note", to: "langchain", path: "M 472 144 L 374 144" },
  {
    id: "e-lc-ai-gw",
    from: "langchain",
    to: "ai-gateway",
    primary: true,
    path: `M ${CENTER_X} 170 L ${CENTER_X} 208`,
  },
  {
    id: "e-lc-pg",
    from: "langchain",
    to: "postgres",
    primary: true,
    path: `M ${CENTER_X} 170 C ${CENTER_X - 40} 260, 204 320, 204 348`,
  },
  {
    id: "e-lc-vec",
    from: "langchain",
    to: "pgvector",
    primary: true,
    path: `M ${CENTER_X} 170 C ${CENTER_X + 40} 260, 392 320, 392 348`,
  },
  { id: "e-ai-gw-openai", from: "ai-gateway", to: "openai-svc", primary: true, path: `M ${CENTER_X} 256 L ${CENTER_X} 272` },
  { id: "e-pg-vec", from: "postgres", to: "pgvector", path: "M 288 374 L 308 374" },
];

const NODE_MAP = Object.fromEntries(STACK_NODES.map((n) => [n.id, n]));

const LAYER_BANDS: Record<LayerId, { y: number; h: number }> = {
  frontend: { y: 32, h: 76 },
  rag: { y: 108, h: 80 },
  aiGateway: { y: 192, h: 136 },
  data: { y: 332, h: 88 },
};

function edgeTouchesLayer(edge: StackEdge, layer: LayerId) {
  const from = NODE_MAP[edge.from];
  const to = NODE_MAP[edge.to];
  return from?.layer === layer || to?.layer === layer;
}

function TechStackDiagram({
  activeLayer,
  animate,
}: {
  activeLayer: LayerId | null;
  animate: boolean;
}) {
  const theme = useHostTheme();

  const resolvedEdges = STACK_EDGES.map((edge) => ({
    ...edge,
    d: edge.path ?? "",
  })).filter((e) => e.d);

  const frameStyle = {
    border: `1px solid ${theme.stroke.primary}`,
    borderRadius: 10,
    overflow: "hidden" as const,
    background: theme.bg.editor,
    maxWidth: 620,
  };

  const titleBarStyle = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 12px",
    borderBottom: `1px solid ${theme.stroke.tertiary}`,
    background: theme.bg.chrome,
  };

  const dot = (color: string) => ({
    width: 10,
    height: 10,
    borderRadius: 999,
    background: color,
    flexShrink: 0,
  });

  const stageStyle = {
    position: "relative" as const,
    width: "100%",
    aspectRatio: `${VIEW_W} / ${VIEW_H}`,
    background: theme.bg.editor,
  };

  const nodeBase = {
    position: "absolute" as const,
    borderRadius: 8,
    border: `1px solid ${theme.stroke.secondary}`,
    background: theme.bg.elevated,
    padding: "8px 10px",
    textAlign: "center" as const,
    display: "flex",
    flexDirection: "column" as const,
    justifyContent: "center",
    gap: 2,
    boxSizing: "border-box" as const,
  };

  return (
    <div style={frameStyle}>
      <style>{`
        @keyframes canRagStackFlow {
          to { stroke-dashoffset: -28; }
        }
        .can-rag-stack-edge-flow {
          animation: canRagStackFlow 1.2s linear infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .can-rag-stack-edge-flow { animation: none; }
        }
      `}</style>
      <div style={titleBarStyle}>
        <div style={{ display: "flex", gap: 6 }}>
          <div style={dot(theme.diff.stripRemoved)} />
          <div style={dot(theme.text.tertiary)} />
          <div style={dot(theme.diff.stripAdded)} />
        </div>
        <Text size="small" tone="secondary">
          tech-stack.ts
        </Text>
        <div style={{ marginLeft: "auto" }}>
          <Pill tone="info">postgres_pgvector</Pill>
        </div>
      </div>
      <div style={stageStyle}>
        <svg
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          preserveAspectRatio="xMidYMid meet"
          aria-hidden
        >
          <defs>
            <marker
              id="can-rag-arrow"
              markerWidth="8"
              markerHeight="8"
              refX="6"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 Z" fill="currentColor" />
            </marker>
          </defs>
          {(Object.keys(LAYER_BANDS) as LayerId[]).map((layerId) => {
            const band = LAYER_BANDS[layerId];
            return (
              <rect
                key={layerId}
                x={12}
                y={band.y}
                width={VIEW_W - 24}
                height={band.h}
                rx={8}
                fill={theme.fill.quaternary}
                stroke={theme.stroke.tertiary}
                strokeWidth={1}
                strokeDasharray="4 3"
              />
            );
          })}
          {(Object.keys(LAYER_TITLES) as LayerId[]).map((layerId) => (
            <text
              key={layerId}
              x={20}
              y={LAYER_BANDS[layerId].y + 14}
              fill={theme.text.tertiary}
              fontSize={10}
              fontFamily="system-ui, sans-serif"
            >
              {LAYER_TITLES[layerId]}
            </text>
          ))}
          {resolvedEdges.map((edge) => {
            const dimmed = activeLayer !== null && !edgeTouchesLayer(edge, activeLayer);
            const isPrimary = edge.primary;
            const strokeColor = isPrimary ? theme.accent.primary : theme.stroke.secondary;
            return (
              <path
                key={edge.id}
                d={edge.d}
                fill="none"
                stroke={strokeColor}
                strokeWidth={isPrimary ? 2.25 : 1.5}
                strokeDasharray={isPrimary ? "8 6" : "4 4"}
                strokeLinecap="round"
                opacity={dimmed ? 0.2 : isPrimary ? 1 : 0.45}
                markerEnd={isPrimary ? "url(#can-rag-arrow)" : undefined}
                style={isPrimary ? { color: strokeColor } : undefined}
                className={animate && !dimmed && isPrimary ? "can-rag-stack-edge-flow" : undefined}
              />
            );
          })}
        </svg>
        {STACK_NODES.map((node) => {
          const dimmed = activeLayer !== null && node.layer !== activeLayer;
          const accentBorder = node.id === "ai-gateway" || node.id === "langchain";
          return (
            <div
              key={node.id}
              style={mergeStyle(nodeBase, {
                left: `${(node.x / VIEW_W) * 100}%`,
                top: `${(node.y / VIEW_H) * 100}%`,
                width: `${(node.width / VIEW_W) * 100}%`,
                height: `${(node.height / VIEW_H) * 100}%`,
                opacity: dimmed ? 0.3 : 1,
                borderColor: accentBorder ? theme.accent.primary : theme.stroke.secondary,
              })}
            >
              <span style={{ fontSize: 12, fontWeight: 600, color: theme.text.primary, lineHeight: 1.25 }}>
                {node.label}
              </span>
              {node.sublabel ? (
                <span style={{ fontSize: 9, color: theme.text.tertiary, lineHeight: 1.2 }}>{node.sublabel}</span>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function CanRagTechStack() {
  const [activeLayer, setActiveLayer] = useCanvasState<LayerId | null>("tech-stack-layer", null);
  const [animate, setAnimate] = useCanvasState("tech-stack-animate", true);

  const layers: Array<{ value: LayerId | null; label: string }> = [
    { value: null, label: "全栈" },
    { value: "frontend", label: "前端" },
    { value: "rag", label: "RAG" },
    { value: "aiGateway", label: "AI Gateway" },
    { value: "data", label: "数据层" },
  ];

  return (
    <Stack gap={16}>
      <Stack gap={6}>
        <Row gap={8} align="center">
          <H2>CAN-RAG 技术栈</H2>
          <Pill tone="info">AI API Gateway</Pill>
          <Pill>LangChain + LangSmith</Pill>
        </Row>
        <Text tone="secondary" size="small">
          前端 Next.js 调用 FastAPI 暴露的 /v1 能力；后端 RAG 由 LangChain 编排，经其下方的 AI API Gateway 统一接入
          OpenAI 模型；向量与业务数据落在 PostgreSQL + pgvector，链路由 LangSmith 观测。
        </Text>
      </Stack>

      <Row gap={8} align="center" style={{ flexWrap: "wrap" }}>
        {layers.map((l) => (
          <Button
            key={String(l.value)}
            size="small"
            variant={activeLayer === l.value ? "primary" : "secondary"}
            onClick={() => setActiveLayer(l.value)}
          >
            {l.label}
          </Button>
        ))}
        <Button size="small" variant="ghost" onClick={() => setAnimate((v) => !v)}>
          {animate ? "暂停动画" : "恢复动画"}
        </Button>
      </Row>

      <TechStackDiagram activeLayer={activeLayer} animate={animate} />

      <Stack gap={6}>
        <Text weight="semibold" size="small">
          职责划分
        </Text>
        <Text tone="secondary" size="small">
          前端：Next.js 14.2.30 · React 18.3.1 · Node.js 22.x（≥18）。FastAPI 承载业务 API（知识库、导入、SSE），图中仅作
          应用宿主标注。LangChain 负责切分、检索与调用模型；其下 AI API Gateway 是 LangChain 访问 OpenAI（Chat / Embedding /
          Vision）的模型网关，不是业务 API 网关。LangSmith 记录 traceable。默认向量库 postgres_pgvector（kb_data / kb_index）。
        </Text>
      </Stack>
    </Stack>
  );
}
