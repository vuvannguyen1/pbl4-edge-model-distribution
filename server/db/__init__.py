"""Gói CSDL SQLAlchemy cho Server."""

from server.db.connection import (
    get_db,
    get_default_database_url,
    get_engine,
    get_session_factory,
    init_db,
    init_engine,
)
from server.db.models import (
    Base,
    DeviceRecord,
    DistributionRequestRecord,
    ModelArtifactRecord,
    ModelChunkRecord,
    ModelFamilyRecord,
    ModelVariantRecord,
    PerformanceFeedbackRecord,
    TaskRecord,
    UserFeedbackRecord,
)
from server.db.repository import (
    get_feedback_statistics,
    get_ready_variants,
    record_device,
    record_distribution_request,
    record_performance_feedback,
    record_user_feedback,
)
from server.db.sync import sync_registry_to_db

__all__ = [
    "Base",
    "init_engine",
    "get_engine",
    "get_session_factory",
    "init_db",
    "get_db",
    "get_default_database_url",
    "TaskRecord",
    "ModelFamilyRecord",
    "ModelVariantRecord",
    "ModelArtifactRecord",
    "ModelChunkRecord",
    "DeviceRecord",
    "DistributionRequestRecord",
    "PerformanceFeedbackRecord",
    "UserFeedbackRecord",
    "sync_registry_to_db",
    "get_ready_variants",
    "record_device",
    "record_distribution_request",
    "record_performance_feedback",
    "record_user_feedback",
    "get_feedback_statistics",
]

