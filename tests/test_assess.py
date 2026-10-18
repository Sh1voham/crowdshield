import pytest

from crowdshield.assess import Assessor
from crowdshield.config import load_config


def run(series, cfg=None, dt=0.2):
    a = Assessor.from_config(cfg or load_config())
    out = []
    for k, (d, p) in enumerate(series):
        out.append(a.step(k * dt, d, p))
    return out


def evs(out):
    return [(round(k * 0.2, 1), f"{e['level']}:{e['kind']}") for k, o in enumerate(out) for e in o["events"]]


def test_rise_needs_two_seconds():
    out = run([(1.0, 0)] * 10 + [(4.5, 0)] * 20)
    first_amber = next(k for k, o in enumerate(out) if o["status"] == "AMBER")
    assert round((first_amber - 10) * 0.2, 1) == 2.0
    assert evs(out) == [(4.0, "AMBER:DENSITY")]


def test_short_spike_does_not_escalate():
    out = run([(1.0, 0)] * 5 + [(7.0, 0)] * 9 + [(1.0, 0)] * 20)
    assert all(o["status"] == "GREEN" for o in out)


def test_fall_needs_ten_seconds_and_recovered_event():
    out = run([(6.5, 0)] * 15 + [(1.0, 0)] * 60)
    assert out[14]["status"] == "RED"
    green_at = next(k for k, o in enumerate(out) if k > 14 and o["status"] == "GREEN")
    assert round((green_at - 15) * 0.2, 1) == 10.0
    assert evs(out)[-1][1] == "CLEAR:RECOVERED"


def test_rise_goes_to_lowest_level_held():
    # 1 s AMBER then RED: after 2 s above GREEN, the lowest level held is AMBER
    out = run([(1.0, 0)] * 2 + [(4.5, 0)] * 5 + [(6.5, 0)] * 30)
    assert evs(out)[0][1] == "AMBER:DENSITY"
    assert evs(out)[1][1] == "RED:DENSITY"


def test_pressure_rules_and_cause():
    out = run([(3.5, 0.6)] * 15)
    assert out[-1]["status"] == "AMBER" and out[-1]["cause"] == "PRESSURE"
    out = run([(4.2, 1.2)] * 15)
    assert out[-1]["status"] == "RED" and evs(out)[0][1] == "RED:PRESSURE"


def test_turbulence_on_off():
    out = run([(3.5, 1.5)] * 20 + [(3.5, 0.5)] * 40)
    on = [k for k, o in enumerate(out) if o["turb_on"]]
    assert on and round(on[0] * 0.2, 1) == 3.0
    assert out[on[0]]["turb_since"] == 0.0
    off_at = next(k for k in range(on[0], len(out)) if not out[k]["turb_on"])
    assert round((off_at - 20) * 0.2, 1) == 5.0
    assert ("3.0", "RED:TURBULENCE") in [(str(t), e) for t, e in evs(out)]


def test_forecast_on_rising_trend():
    # density rising 0.02 per s (1.2 per min) from 2.0
    series = [(2.0 + 0.02 * k * 0.2, 0.0) for k in range(300)]
    out = run(series)
    with_fit = [o for o in out if o["t_red_s"] is not None]
    assert with_fit
    o = out[200]  # t = 40 s, fitted ~2.8, red at 6 -> (6-2.8)/0.02 = 160 s
    assert o["trend_per_min"] == pytest.approx(1.2, rel=1e-6)
    assert o["t_red_s"] == pytest.approx((6 - (2.0 + 0.02 * 40)) / 0.02, rel=1e-6)
    assert "INFO:FORECAST" in [e for _, e in evs(out)]


def test_forecast_needs_span_and_samples():
    out = run([(3.0 + 0.01 * k, 0) for k in range(100)])  # 20 s span only
    assert all(o["t_red_s"] is None for o in out)


def test_risk_bounds_and_action():
    o = run([(9.0, 5.0)] * 15)[-1]
    assert o["risk"] == pytest.approx(0.85)  # trend term 0: no forecast yet
    assert o["action"] == "HOLD_ENTRY"
    o = run([(0.0, 0.0)])[-1]
    assert o["risk"] == 0.0 and o["action"] == "NONE"


def test_demo_profile_thresholds():
    out = run([(2.0, 0)] * 15, load_config(profile="demo"))
    assert out[-1]["status"] == "AMBER"
