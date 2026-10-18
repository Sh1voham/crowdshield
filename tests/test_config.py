import json

import pytest

from crowdshield.config import ConfigError, DEFAULT_CONFIG, config_from_dict, load_config


def raw():
    return json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def test_default_loads():
    cfg = load_config()
    assert cfg.status.amber_density == 4.0 and cfg.status.red_density == 6.0
    assert cfg.pressure.p_thr == 0.02
    assert cfg.profile == "default"


def test_unknown_key_raises():
    d = raw()
    d["status"]["amber_densty"] = 3.0
    with pytest.raises(ConfigError, match="amber_densty"):
        config_from_dict(d)


def test_unknown_section_raises():
    d = raw()
    d["statuss"] = {}
    with pytest.raises(ConfigError):
        config_from_dict(d)


def test_demo_profile_overrides():
    cfg = load_config(profile="demo")
    assert cfg.profile == "demo"
    assert cfg.status.amber_density == 1.5 and cfg.status.red_density == 2.5
    assert cfg.pressure.min_density < 3.0
    assert cfg.pressure.p_thr == 0.02  # untouched values keep their defaults


def test_recall_curve_validation():
    d = raw()
    d["recall"]["recall_curve"] = [[2.0, 0.9], [1.0, 0.95]]
    with pytest.raises(ConfigError):
        config_from_dict(d)
    d["recall"]["recall_curve"] = [[1.0, 0.95], [2.0, 0.9]]
    cfg = config_from_dict(d)
    assert cfg.recall.recall_curve == ((1.0, 0.95), (2.0, 0.9))
