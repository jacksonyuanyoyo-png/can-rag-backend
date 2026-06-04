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

type WorkflowStep = 1 | 2 | 3;

type WorkflowNode = {
  id: string;
  label: string;
  sublabel?: string;
  step: WorkflowStep;
  x: number;
  y: number;
  width: number;
  height: number;
};

type WorkflowEdge = {
  id: string;
  from: string;
  to: string;
  path?: string;
  primary?: boolean;
};

const CANVAS_WIDTH = 560;
const CANVAS_HEIGHT = 440;

const STEP_LABELS: Record<WorkflowStep, string> = {
  1: "Step I · 创建知识库并入库",
  2: "Step II · 验证检索质量",
  3: "Step III · 流式问答与引用",
};

const COLUMN_DIVIDERS = [186, 376];

const WORKFLOW_NODES: WorkflowNode[] = [
  { id: "kb", label: "创建 Knowledge Base", step: 1, x: 24, y: 52, width: 132, height: 38 },
  {
    id: "path-a",
    label: "路径 A",
    sublabel: "presign → PUT → complete",
    step: 1,
    x: 8,
    y: 108,
    width: 118,
    height: 44,
  },
  {
    id: "path-b",
    label: "路径 B",
    sublabel: "web-import → Markdown",
    step: 1,
    x: 134,
    y: 108,
    width: 118,
    height: 44,
  },
  { id: "import-job", label: "POST import-jobs", step: 1, x: 24, y: 168, width: 132, height: 36 },
  { id: "worker", label: "ImportJobWorker", step: 1, x: 24, y: 218, width: 132, height: 36 },
  { id: "pipeline", label: "Parse → Chunk → Embed", step: 1, x: 24, y: 268, width: 132, height: 40 },
  {
    id: "db",
    label: "kb_data + kb_index",
    sublabel: "PostgreSQL pgvector",
    step: 1,
    x: 24,
    y: 328,
    width: 132,
    height: 44,
  },
  { id: "hit-test", label: "POST hit-test", step: 2, x: 214, y: 72, width: 132, height: 38 },
  { id: "query-embed", label: "Query Embedding", step: 2, x: 214, y: 132, width: 132, height: 36 },
  {
    id: "search-data",
    label: "search_data",
    sublabel: "pgvector cosine",
    step: 2,
    x: 214,
    y: 192,
    width: 132,
    height: 44,
  },
  {
    id: "aggregate",
    label: "按 data_id 聚合",
    sublabel: "score · snippet",
    step: 2,
    x: 214,
    y: 258,
    width: 132,
    height: 44,
  },
  {
    id: "stream",
    label: "messages:stream",
    sublabel: "SSE",
    step: 3,
    x: 394,
    y: 72,
    width: 148,
    height: 38,
  },
  { id: "kb-ids", label: "knowledgeBaseIds", step: 3, x: 394, y: 128, width: 148, height: 36 },
  {
    id: "sse",
    label: "retrieval.*",
    sublabel: "message.delta",
    step: 3,
    x: 394,
    y: 182,
    width: 148,
    height: 44,
  },
  { id: "llm", label: "OpenAI Chat", step: 3, x: 394, y: 248, width: 148, height: 36 },
  {
    id: "citations",
    label: "citations JSON",
    sublabel: "chunkId · score",
    step: 3,
    x: 394,
    y: 302,
    width: 148,
    height: 44,
  },
];

const WORKFLOW_EDGES: WorkflowEdge[] = [
  { id: "e-kb-a", from: "kb", to: "path-a" },
  { id: "e-kb-b", from: "kb", to: "path-b" },
  { id: "e-a-job", from: "path-a", to: "import-job" },
  { id: "e-b-job", from: "path-b", to: "import-job" },
  { id: "e-job-worker", from: "import-job", to: "worker" },
  { id: "e-worker-pipe", from: "worker", to: "pipeline" },
  { id: "e-pipe-db", from: "pipeline", to: "db", primary: true },
  {
    id: "e-db-search",
    from: "db",
    to: "search-data",
    primary: true,
    path: "M 90 372 C 90 400, 280 220, 280 214",
  },
  { id: "e-ht-embed", from: "hit-test", to: "query-embed" },
  { id: "e-embed-search", from: "query-embed", to: "search-data" },
  { id: "e-search-agg", from: "search-data", to: "aggregate", primary: true },
  {
    id: "e-agg-sse",
    from: "aggregate",
    to: "sse",
    primary: true,
    path: "M 280 280 C 280 240, 468 210, 468 204",
  },
  { id: "e-stream-kb", from: "stream", to: "kb-ids" },
  { id: "e-kb-sse", from: "kb-ids", to: "sse" },
  { id: "e-sse-llm", from: "sse", to: "llm" },
  { id: "e-llm-cit", from: "llm", to: "citations", primary: true },
  { id: "e-ht-search", from: "hit-test", to: "search-data", path: "M 280 110 L 280 192" },
];

const NODE_MAP = Object.fromEntries(WORKFLOW_NODES.map((n) => [n.id, n]));

function nodeBottomCenter(node: WorkflowNode) {
  return { x: node.x + node.width / 2, y: node.y + node.height };
}

function nodeTopCenter(node: WorkflowNode) {
  return { x: node.x + node.width / 2, y: node.y };
}

function edgePathBetween(from: WorkflowNode, to: WorkflowNode) {
  const start = nodeBottomCenter(from);
  const end = nodeTopCenter(to);
  const midY = (start.y + end.y) / 2;
  return `M ${start.x} ${start.y} C ${start.x} ${midY}, ${end.x} ${midY}, ${end.x} ${end.y}`;
}

function resolveEdgePath(edge: WorkflowEdge) {
  if (edge.path) return edge.path;
  const from = NODE_MAP[edge.from];
  const to = NODE_MAP[edge.to];
  if (!from || !to) return "";
  return edgePathBetween(from, to);
}

function edgeTouchesStep(edge: WorkflowEdge, step: WorkflowStep) {
  const from = NODE_MAP[edge.from];
  const to = NODE_MAP[edge.to];
  return from?.step === step || to?.step === step;
}

function FlowDiagram({
  activeStep,
  animate,
}: {
  activeStep: WorkflowStep | null;
  animate: boolean;
}) {
  const theme = useHostTheme();

  const resolvedEdges = WORKFLOW_EDGES.map((edge) => ({
    ...edge,
    d: resolveEdgePath(edge),
  })).filter((e) => e.d);

  const windowStyle = {
    border: `1px solid ${theme.stroke.primary}`,
    borderRadius: 10,
    overflow: "hidden" as const,
    background: theme.bg.editor,
    maxWidth: 600,
  };

  const titleBarStyle = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 12px",
    borderBottom: `1px solid ${theme.stroke.tertiary}`,
    background: theme.bg.chrome,
  };

  const dotStyle = (tone: "red" | "yellow" | "green") => {
    const colors = {
      red: theme.diff.stripRemoved,
      yellow: theme.text.tertiary,
      green: theme.diff.stripAdded,
    };
    return {
      width: 10,
      height: 10,
      borderRadius: 999,
      background: colors[tone],
      flexShrink: 0,
    };
  };

  const stageStyle = {
    position: "relative" as const,
    width: "100%",
    aspectRatio: `${CANVAS_WIDTH} / ${CANVAS_HEIGHT}`,
    background: theme.bg.editor,
  };

  const nodeBase = {
    position: "absolute" as const,
    borderRadius: 8,
    border: `1px solid ${theme.stroke.secondary}`,
    background: theme.bg.elevated,
    padding: "6px 8px",
    textAlign: "center" as const,
    display: "flex",
    flexDirection: "column" as const,
    justifyContent: "center",
    gap: 2,
    boxSizing: "border-box" as const,
  };

  return (
    <div style={windowStyle}>
      <style>{`
        @keyframes canRagEdgeFlow {
          to { stroke-dashoffset: -28; }
        }
        .can-rag-edge-animated {
          animation: canRagEdgeFlow 1.2s linear infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .can-rag-edge-animated { animation: none; }
        }
      `}</style>
      <div style={titleBarStyle}>
        <div style={{ display: "flex", gap: 6 }}>
          <div style={dotStyle("red")} />
          <div style={dotStyle("yellow")} />
          <div style={dotStyle("green")} />
        </div>
        <Text size="small" tone="secondary">
          workflow.ts
        </Text>
        <div style={{ marginLeft: "auto" }}>
          <Text size="small" tone="tertiary">
            Ready
          </Text>
        </div>
      </div>
      <div style={stageStyle}>
        <svg
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            pointerEvents: "none",
          }}
          viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`}
          preserveAspectRatio="xMidYMid meet"
          aria-hidden
        >
          {COLUMN_DIVIDERS.map((x) => (
            <line
              key={x}
              x1={x}
              y1={36}
              x2={x}
              y2={CANVAS_HEIGHT - 8}
              stroke={theme.stroke.tertiary}
              strokeWidth={1}
              strokeDasharray="4 4"
            />
          ))}
          {resolvedEdges.map((edge) => {
            const dimmed =
              activeStep !== null &&
              !edgeTouchesStep(edge, activeStep) &&
              !(edge.primary && activeStep > 1);
            const isPrimary = edge.primary;
            return (
              <path
                key={edge.id}
                d={edge.d}
                fill="none"
                stroke={isPrimary ? theme.accent.primary : theme.stroke.secondary}
                strokeWidth={isPrimary ? 2.25 : 1.5}
                strokeDasharray="8 6"
                strokeLinecap="round"
                opacity={dimmed ? 0.22 : isPrimary ? 1 : 0.65}
                className={animate && !dimmed ? "can-rag-edge-animated" : undefined}
              />
            );
          })}
        </svg>
        {([1, 2, 3] as WorkflowStep[]).map((step) => (
          <Text
            key={step}
            size="small"
            tone="tertiary"
            style={{
              position: "absolute",
              top: 8,
              left: step === 1 ? 12 : step === 2 ? 198 : 388,
              fontSize: 10,
              maxWidth: 160,
            }}
          >
            {STEP_LABELS[step]}
          </Text>
        ))}
        {WORKFLOW_NODES.map((node) => {
          const dimmed = activeStep !== null && node.step !== activeStep;
          return (
            <div
              key={node.id}
              style={mergeStyle(nodeBase, {
                left: `${(node.x / CANVAS_WIDTH) * 100}%`,
                top: `${(node.y / CANVAS_HEIGHT) * 100}%`,
                width: `${(node.width / CANVAS_WIDTH) * 100}%`,
                height: `${(node.height / CANVAS_HEIGHT) * 100}%`,
                opacity: dimmed ? 0.32 : 1,
              })}
            >
              <span style={{ fontSize: 11, fontWeight: 600, color: theme.text.primary, lineHeight: 1.25 }}>
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

export default function CanRagPptWorkflow() {
  const [activeStep, setActiveStep] = useCanvasState<WorkflowStep | null>("ppt-active-step", null);
  const [animate, setAnimate] = useCanvasState("ppt-edge-animate", true);

  const steps: Array<{ value: WorkflowStep | null; label: string }> = [
    { value: null, label: "全部" },
    { value: 1, label: "Step I" },
    { value: 2, label: "Step II" },
    { value: 3, label: "Step III" },
  ];

  return (
    <Stack gap={16}>
      <Stack gap={6}>
        <Row gap={8} align="center">
          <H2>从入库到带引用问答</H2>
          <Pill tone="info">CAN-RAG · PPT 右侧架构图</Pill>
        </Row>
        <Text tone="secondary" size="small">
          对齐当前工程：presign / web-import → import-job → pgvector；hit-test 检索验证；SSE 流式问答与 citations。
          点击步骤可高亮对应列，便于录屏插入幻灯片。
        </Text>
      </Stack>

      <Row gap={8} align="center">
        {steps.map((s) => (
          <Button
            key={String(s.value)}
            size="small"
            variant={activeStep === s.value ? "primary" : "secondary"}
            onClick={() => setActiveStep(s.value)}
          >
            {s.label}
          </Button>
        ))}
        <Button size="small" variant="secondary" onClick={() => setAnimate((v) => !v)}>
          {animate ? "暂停连线动画" : "恢复连线动画"}
        </Button>
      </Row>

      <FlowDiagram activeStep={activeStep} animate={animate} />

      <Text tone="tertiary" size="small">
        录屏建议：窗口宽约 520–560px，黑底主题；跨列主链（入库 → 检索 → SSE）使用 accent 色流动虚线。
      </Text>
    </Stack>
  );
}
