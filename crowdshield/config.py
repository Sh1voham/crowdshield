"""Configuration: every tunable number lives in config/default.json.

The JSON is loaded into frozen dataclasses. Unknown keys raise, so a typo in the
config fails loudly instead of silently using a default.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "default.json"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class GridCfg:
    cell_m: float = 1.0
    kernel_R_m: float = 1.0
    cutoff_R: float = 3.0
    update_hz: float = 5.0
    area_margin_m: float = 0.5


@dataclass(frozen=True)
class KinematicsCfg:
    min_dt_s: float = 1.0
    ema_tau_s: float = 1.0
    min_age_s: float = 0.5
    history_s: float = 3.0


@dataclass(frozen=True)
class PressureCfg:
    window_s: float = 8.0
    min_samples: int = 15
    min_density: float = 3.0
    velocity_min_weight: float = 1.0
    speed_min_weight: float = 0.3
    p_thr: float = 0.02


@dataclass(frozen=True)
class RecallCfg:
    recall_estimate: float = 1.0
    recall_curve: Optional[tuple] = None  # ((measured_density, recall), ...)


@dataclass(frozen=True)
class StatusCfg:
    amber_density: float = 4.0
    red_density: float = 6.0
    amber_pidx: float = 0.5
    amber_pidx_density: float = 3.0
    red_pidx: float = 1.0
    red_pidx_density: float = 4.0
    rise_hold_s: float = 2.0
    fall_hold_s: float = 10.0


@dataclass(frozen=True)
class TurbulenceCfg:
    on_pidx: float = 1.0
    on_density: float = 3.0
    on_hold_s: float = 3.0
    off_pidx: float = 0.8
    off_hold_s: float = 5.0


@dataclass(frozen=True)
class ForecastCfg:
    window_s: float = 90.0
    min_samples: int = 20
    min_span_s: float = 30.0
    min_slope_per_s: float = 0.0008
    min_level: float = 2.0
    min_r2: float = 0.4
    max_horizon_s: float = 900.0
    alert_horizon_s: float = 300.0


@dataclass(frozen=True)
class RiskCfg:
    w_density: float = 0.55
    w_pressure: float = 0.30
    w_trend: float = 0.15
    trend_ref_per_min: float = 1.0


@dataclass(frozen=True)
class TrackerCfg:
    gate_m: float = 1.0
    max_age_s: float = 1.0


@dataclass(frozen=True)
class AdapterCfg:
    labels: tuple = ("person", "head", "pedestrian", "0")
    ts_unit: str = "auto"
    coords: str = "auto"
    default_fps: float = 10.0
    max_backstep_s: float = 1.0


@dataclass(frozen=True)
class PublishCfg:
    state_hz: float = 5.0
    status_hz: float = 1.0
    actuator_period_s: float = 5.0
    tracks_cap: int = 400
    stale_s: float = 3.0


@dataclass(frozen=True)
class PipelineCfg:
    model: Optional[str] = None
    precision: Optional[str] = None


@dataclass(frozen=True)
class Config:
    grid: GridCfg = field(default_factory=GridCfg)
    kinematics: KinematicsCfg = field(default_factory=KinematicsCfg)
    pressure: PressureCfg = field(default_factory=PressureCfg)
    recall: RecallCfg = field(default_factory=RecallCfg)
    status: StatusCfg = field(default_factory=StatusCfg)
    turbulence: TurbulenceCfg = field(default_factory=TurbulenceCfg)
    forecast: ForecastCfg = field(default_factory=ForecastCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    tracker: TrackerCfg = field(default_factory=TrackerCfg)
    adapter: AdapterCfg = field(default_factory=AdapterCfg)
    publish: PublishCfg = field(default_factory=PublishCfg)
    pipeline: PipelineCfg = field(default_factory=PipelineCfg)
    profile: str = "default"


_SECTIONS = {f.name: f for f in fields(Config) if f.name != "profile"}


def _build(cls, data: dict, where: str):
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected an object, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; known: {sorted(known)}")
    kwargs = {}
    for name, value in data.items():
        if isinstance(value, list):
            value = tuple(tuple(v) if isinstance(v, list) else v for v in value)
        kwargs[name] = value
    return cls(**kwargs)


def _merge(base: dict, override: dict, where: str) -> dict:
    out = copy.deepcopy(base)
    for section, values in override.items():
        if section not in _SECTIONS:
            raise ConfigError(f"{where}: unknown section {section!r}")
        out.setdefault(section, {})
        out[section].update(values)
    return out


def config_from_dict(raw: dict, profile: str = "default") -> Config:
    raw = copy.deepcopy(raw)
    demo = raw.pop("demo_profile", None)
    unknown = sorted(set(raw) - set(_SECTIONS))
    if unknown:
        raise ConfigError(f"config: unknown section(s) {unknown}")
    if profile == "demo":
        if not demo:
            raise ConfigError("profile 'demo' requested but the config has no demo_profile block")
        raw = _merge(raw, demo, "demo_profile")
    elif profile != "default":
        raise ConfigError(f"unknown profile {profile!r} (use default or demo)")
    built = {name: _build(f.default_factory, raw.get(name, {}), name)  # type: ignore[misc]
             for name, f in _SECTIONS.items()}
    cfg = Config(profile=profile, **built)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.grid.cell_m <= 0 or cfg.grid.kernel_R_m <= 0:
        raise ConfigError("grid.cell_m and grid.kernel_R_m must be positive")
    if cfg.pressure.p_thr <= 0:
        raise ConfigError("pressure.p_thr must be positive")
    if not (0 < cfg.recall.recall_estimate <= 1.5):
        raise ConfigError("recall.recall_estimate must be in (0, 1.5]")
    curve = cfg.recall.recall_curve
    if curve is not None:
        if len(curve) < 2 or any(len(p) != 2 for p in curve):
            raise ConfigError("recall.recall_curve must be a list of [measured_density, recall] pairs")
        xs = [p[0] for p in curve]
        if xs != sorted(xs) or any(not (0 < p[1] <= 1.5) for p in curve):
            raise ConfigError("recall.recall_curve must be sorted by density with recall in (0, 1.5]")
    if cfg.status.red_density <= cfg.status.amber_density:
        raise ConfigError("status.red_density must exceed status.amber_density")


def load_config(path: Optional[str | Path] = None, profile: str = "default") -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return config_from_dict(raw, profile)


def config_to_dict(cfg: Any) -> Any:
    """Plain-JSON view of a config (used by the publisher and the gateway)."""
    if is_dataclass(cfg):
        return {f.name: config_to_dict(getattr(cfg, f.name)) for f in fields(cfg)}
    if isinstance(cfg, tuple):
        return [config_to_dict(v) for v in cfg]
    return cfg
