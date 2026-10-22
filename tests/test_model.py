"""Modelo base: orden de features configurable y probabilidades validas."""

from __future__ import annotations

from src.models.baseline import FEATURE_ORDER, LogisticBaseline


def test_default_feature_order_is_the_original_baseline() -> None:
    assert FEATURE_ORDER == (
        "trend",
        "momentum_10d",
        "distance_to_sma",
        "volume_relative",
        "vol_20d",
    )


def test_custom_feature_order_trains_and_predicts() -> None:
    rows = [{"a": i / 10, "b": float(i % 3 - 1)} for i in range(50)]
    labels = [1 if r["a"] > 2.4 else 0 for r in rows]
    model = LogisticBaseline(
        epochs=300, learning_rate=0.2, feature_order=("a", "b")
    ).fit(rows, labels)

    assert set(model.describe()) == {"a", "b"}
    high = float(model.predict_proba([{"a": 5.0, "b": 0.0}])[0])
    low = float(model.predict_proba([{"a": -5.0, "b": 0.0}])[0])
    assert 0.0 <= low <= high <= 1.0
    assert high > low
