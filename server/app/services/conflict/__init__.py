"""冲突检测。"""

from app.services.conflict.detector import (
    ConflictCandidate,
    DetectResult,
    detect_conflicts,
)

__all__ = ["detect_conflicts", "DetectResult", "ConflictCandidate"]
