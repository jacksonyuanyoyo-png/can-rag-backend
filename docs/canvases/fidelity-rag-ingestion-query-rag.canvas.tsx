// @ts-nocheck
import { Divider, H1, H2, H3, Pill, Row, Stack, Text, useHostTheme } from "cursor/canvas";

/** 流程与上一版一致（1–15 链 + 本地双列）；样式对齐 fidelity-rag-layered-and-flow（浅底、虚线区、白卡片、浅蓝序号）。 */
const openAiIngestion = [
  "本地 / 上传源文件",
  "FastAPI\nKnowledgeBaseService\nOpenAIClient",
  "POST /files\nupload_file → file_id",
  "POST /vector_stores/{id}/files\nattach_file_to_vector_store",
  "OpenAI 托管\n解析 · 分段 · Embedding",
  "托管 Vector Store\n向量索引就绪",
  "JSON 元数据\nvector_store_id\nbackend_refs",
];

const openAiRag = [
  "用户 Query",
  "FastAPI 网关\n参数校验",
  "解析知识库\nvector_store_id",
  "OpenAI File Search /\nVector Store 检索",
  "相关文本片段\nscore · citations",
  "Prompt 模板\n上下文 + 问题",
  "调用 LLM\nChat / Responses",
  "Answer\n可追溯引用",
];

const localPipelineIngestion = [
  "源文件 → LOCAL_UPLOAD_ROOT",
  "read_text → RagPipeline.index_document",
  "TextChunker.split（RAG_CHUNK_SIZE / OVERLAP）",
  "HashEmbeddingService.embed（每段）",
  "JsonVectorStore.upsert_document",
];

const localPipelineQuery = [
  "用户 Query",
  "HashEmbeddingService.embed(query)",
  "JsonVectorStore.search + SimpleReranker",
  "SearchHit → 拼装 Prompt → LLM（业务层对接）",
];

export default function FidelityRagIngestionQueryRag() {
  const theme = useHostTheme();

  const pipelineWrap = {
    border: `1px dashed ${theme.stroke.primary}`,
    background: theme.fill.quaternary,
    borderRadius: 12,
    padding: 16,
  };

  const chainStyle = {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "center",
    gap: "6px 4px",
  };

  const numStyle = {
    flexShrink: 0,
    width: 28,
    height: 28,
    borderRadius: 999,
    background: theme.fill.secondary,
    color: theme.accent.primary,
    fontSize: 12,
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    border: `1px solid ${theme.stroke.secondary}`,
  };

  const cardStyle = {
    border: `1px solid ${theme.stroke.primary}`,
    background: theme.bg.elevated,
    borderRadius: 10,
    padding: "10px 12px",
    maxWidth: 176,
    fontSize: 11,
    lineHeight: 1.35,
    color: theme.text.primary,
    textAlign: "center" as const,
    whiteSpace: "pre-line" as const,
  };

  const sectionStyle = {
    border: `1px dashed ${theme.stroke.primary}`,
    background: theme.fill.quaternary,
    borderRadius: 12,
    padding: 12,
  };

  const labelStyle = {
    width: 118,
    color: theme.text.secondary,
    fontWeight: 600,
    fontSize: 13,
    paddingTop: 6,
  };

  const groupStyle = {
    flex: 1,
    border: `1px dashed ${theme.stroke.secondary}`,
    background: theme.bg.elevated,
    borderRadius: 8,
    padding: "10px 10px 12px",
    minHeight: 120,
  };

  const renderChain = (items: string[], startIndex: number) => (
    <div style={chainStyle}>
      {items.map((box, i) => {
        const n = String(startIndex + i);
        const isLast = i === items.length - 1;
        return (
          <div key={n} style={{ display: "contents" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={numStyle}>{n}</div>
              <div style={cardStyle}>{box}</div>
            </div>
            {!isLast ? (
              <div style={{ color: theme.text.tertiary, fontSize: 22, padding: "0 2px" }}>→</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );

  return (
    <Stack gap={18}>
      <Stack gap={6}>
        <Row gap={8} align="center">
          <H1>源文件入库与对话 RAG 流程（基于本工程）</H1>
          <Pill tone="info">流程同上一版 · 样式对齐 layered-and-flow</Pill>
        </Row>
        <Text tone="secondary">
          阶段 A 为入库（1–7），阶段 B 为对话 RAG（8–15）。OpenAI 路径对应 OpenAIClient；本地双列对应 RagPipeline 与 KnowledgeBaseService。
        </Text>
      </Stack>

      <div style={pipelineWrap}>
        <H2>OpenAI Vector Store 托管路径</H2>
        <H3>阶段 A — 入库：分段与 Embedding 在 OpenAI 侧完成</H3>
        {renderChain(openAiIngestion, 1)}
        <Divider />
        <H3>阶段 B — 对话：RAG 检索与生成</H3>
        {renderChain(openAiRag, 8)}
      </div>

      <div style={sectionStyle}>
        <div style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
          <div style={{ ...labelStyle, display: "flex", justifyContent: "center" }}>本地 RagPipeline</div>
          <div style={{ display: "flex", gap: 8, flex: 1 }}>
            <div style={groupStyle}>
              <Text size="small" weight="semibold">
                工程内已落地（入库）
              </Text>
              <Text size="small" tone="secondary">
                RagPipeline：chunk → embed → JsonVectorStore；由 KnowledgeBaseService.index_document 触发。
              </Text>
              <Stack gap={4}>
                {localPipelineIngestion.map((line) => (
                  <Text key={line} size="small">
                    · {line}
                  </Text>
                ))}
              </Stack>
            </div>
            <div style={groupStyle}>
              <Text size="small" weight="semibold">
                对话检索
              </Text>
              <Stack gap={4}>
                {localPipelineQuery.map((line) => (
                  <Text key={line} size="small">
                    · {line}
                  </Text>
                ))}
              </Stack>
            </div>
          </div>
        </div>
      </div>

      <Stack gap={8}>
        <H2>图中需要表达的重点</H2>
        <Text size="small">
          OpenAI 路径中步骤 5 的解析、分段、向量化不在本仓库显式实现，由 Vector Store 托管流水线完成；本仓库负责 REST 编排与 KnowledgeBaseRepository
          元数据。
        </Text>
        <Text size="small" tone="secondary">
          视觉与 fidelity-rag-layered-and-flow 一致：浅底、虚线容器、白卡片步骤、灰色箭头与浅蓝序号圈。
        </Text>
      </Stack>
    </Stack>
  );
}
