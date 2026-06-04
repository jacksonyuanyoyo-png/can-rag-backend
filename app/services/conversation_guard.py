from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.domain.conversation import ConversationRecord, MessageRole


class ConversationIntent(StrEnum):
    SMALLTALK = "smalltalk"
    GENERAL_CHAT = "general_chat"
    RAG_QUESTION = "rag_question"
    FOLLOWUP_QUESTION = "followup_question"
    CLARIFICATION = "clarification"
    UNSAFE_OR_POLICY_VIOLATION = "unsafe_or_policy_violation"


class RiskLevel(StrEnum):
    NONE = "none"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ConversationIntentResult:
    intent: ConversationIntent
    should_retrieve: bool
    risk_level: RiskLevel
    reason: str
    safe_response: str | None = None


_SMALLTALK_RE = re.compile(
    r"^(?:"
    r"hi|hello|hey|yo|"
    r"你好|您好|嗨|哈喽|早上好|下午好|晚上好|"
    r"谢谢|多谢|感谢|thanks|thank you|"
    r"bye|goodbye|再见|拜拜"
    r")[!！。.\s]*$",
    re.IGNORECASE,
)
_GENERAL_CHAT_RE = re.compile(
    r"^(?:你是谁|你是(?:什么|谁)|你能做什么|你可以做什么|"
    r"介绍一下你自己|help|帮助|what can you do)[?？!！。.\s]*$",
    re.IGNORECASE,
)
_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"^(?:这个|那个|它|这点|上面|刚才|前面|第二点|第一点|第三点)"
    r"|(?:展开|继续|详细|还有|补充|解释|说明|说说|呢|吗|为什么|怎么处理)"
    r")",
    re.IGNORECASE,
)
_QUESTION_HINT_RE = re.compile(
    r"(?:"
    r"什么|为何|为什么|怎么|如何|是否|哪些|哪个|多少|"
    r"流程|政策|条款|费率|费用|要求|规定|文档|文件|PDF|pdf|"
    r"what|why|how|which|where|when|policy|document|fee|rate"
    r")",
    re.IGNORECASE,
)
_UNSAFE_ACTION_RE = re.compile(
    r"(?:"
    r"导出|下载|列出|打印|展示|显示|给我全部|全部给我|批量|获取所有|"
    r"dump|export|download|list\s+all|print|reveal|show\s+all|exfiltrate|"
    r"绕过|忽略(?:.*)(?:规则|限制|指令)|bypass|ignore(?:.*)(?:instruction|rule)"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_TARGET_RE = re.compile(
    r"(?:"
    r"客户资料|客户数据|用户数据|员工信息|个人信息|隐私|"
    r"SIN|SSN|身份证|手机号|电话|地址|银行卡|工资|薪资|"
    r"密码|密钥|api[\s_-]*key|token|private[\s_-]*key|secret|connection\s+string|"
    r"system\s+prompt|系统提示词|内部提示词|上下文|知识库全文|全部文档|所有文档"
    r")",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?:"
    r"忽略(?:之前|以上|系统|开发者).*(?:指令|规则)|"
    r"打印.*(?:system\s+prompt|系统提示词|内部提示词)|"
    r"泄露.*(?:上下文|提示词|prompt)|"
    r"ignore\s+(?:previous|above|system|developer).*(?:instruction|rule)|"
    r"(?:show|print|reveal).*(?:system\s+prompt|hidden\s+prompt|developer\s+message)"
    r")",
    re.IGNORECASE,
)

_UNSAFE_RESPONSE = (
    "抱歉，我不能帮助导出、泄露或绕过访问控制来获取敏感信息。"
    "如果你是在做合规或安全排查，请改为描述授权范围内的具体问题。"
)


class ConversationGuard:
    def classify(
        self,
        content: str,
        *,
        has_kb: bool,
        conversation: ConversationRecord,
    ) -> ConversationIntentResult:
        text = _normalize(content)
        has_history = _has_prior_user_message(conversation, text)

        if _is_unsafe(text):
            return ConversationIntentResult(
                intent=ConversationIntent.UNSAFE_OR_POLICY_VIOLATION,
                should_retrieve=False,
                risk_level=RiskLevel.HIGH,
                reason="unsafe_policy",
                safe_response=_UNSAFE_RESPONSE,
            )

        if _is_smalltalk(text):
            return ConversationIntentResult(
                intent=ConversationIntent.SMALLTALK,
                should_retrieve=False,
                risk_level=RiskLevel.NONE,
                reason="smalltalk",
            )

        if _is_general_chat(text):
            return ConversationIntentResult(
                intent=ConversationIntent.GENERAL_CHAT,
                should_retrieve=False,
                risk_level=RiskLevel.NONE,
                reason="general_chat",
            )

        if _looks_like_followup(text):
            return ConversationIntentResult(
                intent=ConversationIntent.FOLLOWUP_QUESTION
                if has_history
                else ConversationIntent.CLARIFICATION,
                should_retrieve=has_kb and has_history,
                risk_level=RiskLevel.NONE,
                reason="followup" if has_history else "ambiguous_without_history",
            )

        if has_kb and _looks_like_rag_question(text):
            return ConversationIntentResult(
                intent=ConversationIntent.RAG_QUESTION,
                should_retrieve=True,
                risk_level=RiskLevel.NONE,
                reason="rag_question",
            )

        if has_kb:
            return ConversationIntentResult(
                intent=ConversationIntent.RAG_QUESTION,
                should_retrieve=True,
                risk_level=RiskLevel.NONE,
                reason="default_with_kb",
            )

        return ConversationIntentResult(
            intent=ConversationIntent.GENERAL_CHAT,
            should_retrieve=False,
            risk_level=RiskLevel.NONE,
            reason="default_without_kb",
        )


def _normalize(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip())


def _has_prior_user_message(conversation: ConversationRecord, current_text: str) -> bool:
    for message in conversation.messages:
        if message.role != MessageRole.USER:
            continue
        text = _normalize(message.content)
        if text and text != current_text:
            return True
    return False


def _is_unsafe(text: str) -> bool:
    return bool(_PROMPT_INJECTION_RE.search(text)) or bool(
        _UNSAFE_ACTION_RE.search(text) and _SENSITIVE_TARGET_RE.search(text)
    )


def _is_smalltalk(text: str) -> bool:
    return len(text) <= 30 and bool(_SMALLTALK_RE.match(text))


def _is_general_chat(text: str) -> bool:
    return len(text) <= 40 and bool(_GENERAL_CHAT_RE.match(text))


def _looks_like_followup(text: str) -> bool:
    return len(text) <= 40 and bool(_FOLLOWUP_RE.search(text))


def _looks_like_rag_question(text: str) -> bool:
    return bool(_QUESTION_HINT_RE.search(text))
