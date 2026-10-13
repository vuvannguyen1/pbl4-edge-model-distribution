"""Cấu hình chung cho pytest."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_STORE = REPO_ROOT / "models"
REGISTRY_PATH = REPO_ROOT / "registry" / "model_registry.json"

# Cho phép `import validate_examples` (tools/protocol/ không phải package)
sys.path.insert(0, str(REPO_ROOT / "tools" / "protocol"))
sys.path.insert(0, str(REPO_ROOT))
