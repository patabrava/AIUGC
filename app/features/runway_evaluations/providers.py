"""Seedance host contracts. Legacy URLs/table names remain rolling-compatible."""
from dataclasses import dataclass
from typing import Any
from app.adapters.modelark_client import MODEL, RATIOS, get_modelark_client
from app.adapters.runway_client import SEEDANCE_2_5_PORTRAIT_RATIOS, get_runway_client

@dataclass(frozen=True)
class EvaluationProvider:
    name: str
    model: str
    quota: str
    endpoint: str
    api_version: str
    seed_max: int
    ratios: dict
    storage_prefix: str
    unit: str

RUNWAY = EvaluationProvider("runway", "seedance2_5", "runway_seedance_2_5", "/v1/image_to_video",
                            "2024-11-06", 4_294_967_295, SEEDANCE_2_5_PORTRAIT_RATIOS, "runway-evaluations", "runway_credit")
MODELARK = EvaluationProvider("modelark", MODEL, "modelark_seedance_2_5", "/contents/generations/tasks",
                              "v3", 2_147_483_647, RATIOS, "modelark-evaluations", "usd_microdollar")


def profile(settings: Any) -> EvaluationProvider:
    name = getattr(settings, "seedance_evaluation_provider", "runway")
    if name not in {"runway", "modelark"}:
        raise ValueError("Unsupported Seedance evaluation provider")
    return MODELARK if name == "modelark" else RUNWAY


def setting(settings: Any, suffix: str, default=None):
    return getattr(settings, f"{profile(settings).name}_{suffix}", default)


class ProviderSettings:
    """Override only host selection; every credential/config stays host-scoped."""
    def __init__(self, settings, provider):
        self._settings = settings
        self.seedance_evaluation_provider = provider
    def __getattr__(self, name):
        return getattr(self._settings, name)


def client_factory(settings):
    return get_modelark_client(settings) if profile(settings).name == "modelark" else get_runway_client(settings)
