import math

import numpy as np

from crowdshield.fields import FieldComputer, GridSpec, PressureBuffer, RecallModel, fit_recall_curve


def test_kernel_integrates_to_one():
    spec = GridSpec(20.0, 20.0, 0.05)
    fc = FieldComputer(spec, kernel_R=1.0, cutoff_R=5.0)
    f = fc.compute(np.array([[10.0, 10.0]]), np.zeros((1, 2)), np.zeros(1, dtype=bool))
    integral = f["rho"].sum() * 0.05 * 0.05
    assert abs(integral - 1.0) < 1e-3


def test_uniform_lattice_density():
    # 2 people per m^2 on a lattice -> density about 2 away from the edges
    xs, ys = np.meshgrid(np.arange(0.25, 20, 0.5), np.arange(0.5, 20, 1.0))
    pts = np.c_[xs.ravel(), ys.ravel()]
    fc = FieldComputer(GridSpec(20, 20, 1.0))
    f = fc.compute(pts, np.zeros_like(pts), np.zeros(len(pts), dtype=bool))
    assert abs(f["rho"][10, 10] - 2.0) < 0.05


def test_velocity_field_and_null_cells():
    fc = FieldComputer(GridSpec(10, 4, 1.0))
    pts = np.array([[1.0, 1.0], [1.2, 1.1], [1.1, 0.9]])
    vel = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
    f = fc.compute(pts, vel, np.ones(3, dtype=bool))
    assert abs(f["vx"][1, 1] - 1.0) < 1e-9 and abs(f["vy"][1, 1]) < 1e-9
    assert math.isnan(f["vx"][0, 9])  # far cell: no weight -> null


def _run_pressure(vel_fn, steps=60, n=40):
    spec = GridSpec(6, 6, 1.0)
    fc = FieldComputer(spec)
    pb = PressureBuffer((spec.rows, spec.cols), window_s=8.0, min_samples=15, min_density=3.0)
    rng = np.random.default_rng(0)
    pts = 2.5 + rng.random((n, 2))  # dense cluster around (3, 3)
    P = None
    for k in range(steps):
        t = k * 0.2
        v = vel_fn(t, n)
        f = fc.compute(pts, v, np.ones(n, dtype=bool))
        P = pb.update(t, f["vx"], f["vy"], f["rho"])
    return P, f["rho"]


def test_pressure_zero_for_uniform_steady_flow():
    P, rho = _run_pressure(lambda t, n: np.tile([0.8, 0.0], (n, 1)))
    assert rho.max() >= 3.0
    assert P.max() < 1e-12


def test_pressure_positive_for_oscillating_flow():
    P, rho = _run_pressure(lambda t, n: np.tile([0.5 * math.sin(2 * math.pi * t / 2.0), 0.0], (n, 1)))
    assert P.max() > 0.1


def test_pressure_needs_density_gate():
    spec = GridSpec(6, 6, 1.0)
    pb = PressureBuffer((spec.rows, spec.cols), min_density=3.0)
    rho = np.full((6, 6), 2.0)
    for k in range(40):
        vx = np.full((6, 6), math.sin(k))
        P = pb.update(k * 0.2, vx, np.zeros((6, 6)), rho)
    assert P.max() == 0.0


def test_recall_correction_curve_and_constant():
    rc = RecallModel(recall_curve=[(1.0, 0.95), (3.0, 0.8), (5.0, 0.75)])
    out = rc.correct(np.array([0.5, 3.0, 9.0]))
    assert np.allclose(out, [0.5 / 0.95, 3.0 / 0.8, 9.0 / 0.75])
    assert np.allclose(RecallModel(0.8).correct(np.array([4.0])), [5.0])


def test_fit_recall_curve():
    d = np.r_[np.full(10, 1.0), np.full(10, 3.0)]
    r = np.r_[np.full(10, 0.95), np.full(10, 0.8)]
    curve = fit_recall_curve(d, r, bin_width=1.0)
    assert curve == [[1.0, 0.95], [3.0, 0.8]]
