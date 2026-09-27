"""ORM models. Importing this package registers every table on `Base.metadata`."""

from cortex_api.models.api_key import ApiKey
from cortex_api.models.audit_log import AuditLog
from cortex_api.models.conversation import Conversation
from cortex_api.models.document import Document
from cortex_api.models.document_chunk import EMBEDDING_DIMENSIONS, DocumentChunk
from cortex_api.models.embedding import Embedding
from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.models.knowledge_query import KnowledgeQuery, SearchMode
from cortex_api.models.memory import Memory, MemoryType
from cortex_api.models.message import Message, MessageRole
from cortex_api.models.model_execution import ModelExecution, RoutingMode
from cortex_api.models.organization import Organization
from cortex_api.models.user import User, UserRole

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "ApiKey",
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "Embedding",
    "EvidenceEdge",
    "EvidenceEdgeType",
    "EvidenceNode",
    "EvidenceNodeType",
    "KnowledgeQuery",
    "Memory",
    "MemoryType",
    "Message",
    "MessageRole",
    "ModelExecution",
    "Organization",
    "RoutingMode",
    "SearchMode",
    "User",
    "UserRole",
]
