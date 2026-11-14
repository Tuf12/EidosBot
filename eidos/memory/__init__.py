"""EidosBot Memory and Experiential Learning Subsystem."""

from eidos.memory.consolidator import EpisodicConsolidator, IdleConsolidationRunner
from eidos.memory.conversation_store import ConversationStore
from eidos.memory.embeddings import OllamaEmbeddingClient
from eidos.memory.entity_extractor import EntityExtractor
from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.graph_store import GraphStore
from eidos.memory.recall_stream import RecallStreamInterceptor
from eidos.memory.reflector import EpisodicReflector
from eidos.memory.trajectory_logger import TrajectoryLogger
from eidos.memory.types import (
    CandidateRule,
    ClusterSummary,
    ConsolidationReport,
    ENTITY_TAXONOMY,
    ENTITY_TYPE_COMMAND,
    ENTITY_TYPE_DEVICE,
    ENTITY_TYPE_DOMAIN,
    ENTITY_TYPE_PATH,
    ENTITY_TYPE_PROJECT,
    ENTITY_TYPE_WINDOW,
    VALID_ENTITY_TYPES,
    EpisodeMetadata,
    EpisodeRecord,
    EpisodeReflection,
    EpisodicSearchResult,
    EntityType,
    ExtractedEntity,
    ReflectionEvaluation,
    StepRecord,
    UserProfile,
)
from eidos.memory.user_store import UserStore

__all__ = [
    "CandidateRule",
    "ClusterSummary",
    "ConsolidationReport",
    "ConversationStore",
    "EpisodeMetadata",
    "EpisodeRecord",
    "EpisodeReflection",
    "EpisodicSearchResult",
    "ExtractedEntity",
    "EntityType",
    "ENTITY_TAXONOMY",
    "VALID_ENTITY_TYPES",
    "ENTITY_TYPE_COMMAND",
    "ENTITY_TYPE_DEVICE",
    "ENTITY_TYPE_DOMAIN",
    "ENTITY_TYPE_PATH",
    "ENTITY_TYPE_PROJECT",
    "ENTITY_TYPE_WINDOW",
    "EntityExtractor",
    "GraphStore",
    "ReflectionEvaluation",
    "StepRecord",
    "UserProfile",
    "UserStore",
    "TrajectoryLogger",
    "OllamaEmbeddingClient",
    "EpisodicStore",
    "EpisodicReflector",
    "EpisodicConsolidator",
    "IdleConsolidationRunner",
    "RecallStreamInterceptor",
]
