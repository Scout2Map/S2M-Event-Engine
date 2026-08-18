"""Least squares fit tests for the prediction node, without importing ROS."""

import importlib.util
import pathlib
import sys
import types


def _load_least_squares():
    """Import prediction_node with rclpy and std_msgs stubbed out."""
    for name in ('rclpy', 'rclpy.node', 'std_msgs', 'std_msgs.msg'):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules['rclpy.node'].Node = object
    sys.modules['std_msgs.msg'].Float32 = object
    sys.modules['std_msgs.msg'].String = object

    path = pathlib.Path(__file__).resolve().parents[1] / \
        'scout2map_event' / 'prediction_node.py'
    spec = importlib.util.spec_from_file_location('prediction_node', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PredictionNode._least_squares


def test_recovers_a_known_slope():
    least_squares = _load_least_squares()
    # 0.1 degC per second starting at 20.0
    samples = [(1000.0 + t, 20.0 + 0.1 * t) for t in range(30)]
    slope, intercept, t0 = least_squares(samples)
    assert abs(slope - 0.1) < 1e-9
    assert abs(intercept - 20.0) < 1e-9
    assert t0 == 1000.0


def test_uneven_sample_spacing_does_not_distort_the_slope():
    """Index based fitting gets this wrong; time based fitting does not."""
    least_squares = _load_least_squares()
    stamps = [0.0, 1.0, 2.0, 10.0, 11.0, 12.0]
    samples = [(1000.0 + t, 20.0 + 0.5 * t) for t in stamps]
    slope, _, _ = least_squares(samples)
    assert abs(slope - 0.5) < 1e-9


def test_flat_series_has_zero_slope():
    least_squares = _load_least_squares()
    samples = [(1000.0 + t, 22.0) for t in range(20)]
    slope, intercept, _ = least_squares(samples)
    assert abs(slope) < 1e-12
    assert abs(intercept - 22.0) < 1e-9


def test_identical_timestamps_do_not_divide_by_zero():
    least_squares = _load_least_squares()
    samples = [(1000.0, 20.0), (1000.0, 21.0), (1000.0, 22.0)]
    slope, intercept, _ = least_squares(samples)
    assert slope == 0.0
    assert abs(intercept - 21.0) < 1e-9
