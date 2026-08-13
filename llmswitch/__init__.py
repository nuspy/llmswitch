"""llmswitch — provider LLM locali e remoti, con la VRAM presa sul serio.

    from llmswitch import LocalEngines, LmStudioConfig

    eng = LocalEngines(LmStudioConfig(model="qwen3.6-35b-a3b"))
    r = eng.resolve()          # un modello già in memoria si usa e basta
    print(r.backend, r.model, r.reason)

    from llmswitch import Registry
    reg = Registry(app_name="mioprogetto")
    base, key, model = reg.endpoint_for("chat")
"""
from .local import (
    LmStudioConfig,
    LocalEngines,
    Resolution,
    VllmConfig,
    human_size,
    same_model,
    vram_free_bytes,
)
from .providers import TYPES, Registry, default_registry_path

__version__ = "0.1.0"

__all__ = [
    "LocalEngines", "LmStudioConfig", "VllmConfig", "Resolution",
    "Registry", "TYPES", "default_registry_path",
    "human_size", "same_model", "vram_free_bytes", "__version__",
]
