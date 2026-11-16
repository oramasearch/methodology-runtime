# SPDX-License-Identifier: MIT

"""Logistic regression with the standard library: an example workspace methodology.

Numeric features are standardized; anything that is not a number counts as 0 after scaling. The
target's positive class is 1, true, "1", "yes" or "true".
"""

import math

POSITIVE = {1, True, "1", "yes", "true", "True", "YES"}


def _label(value):
    return 1.0 if value in POSITIVE else 0.0


def _number(value):
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _matrix(model, data):
    rows = []
    for values in data.X():
        row = []
        for index, value in enumerate(values):
            number = _number(value)
            mean, scale = model["means"][index], model["scales"][index]
            row.append(0.0 if number is None else (number - mean) / scale)
        rows.append(row)
    return rows


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp = math.exp(z)
    return exp / (1.0 + exp)


def fit(data, params):
    columns = list(zip(*data.X())) if len(data) else []
    means, scales = [], []
    for column in columns:
        numbers = [n for n in (_number(v) for v in column) if n is not None]
        mean = sum(numbers) / len(numbers) if numbers else 0.0
        variance = sum((n - mean) ** 2 for n in numbers) / len(numbers) if numbers else 0.0
        means.append(mean)
        scales.append(math.sqrt(variance) or 1.0)
    model = {"features": list(data.features), "means": means, "scales": scales}
    x = _matrix(model, data)
    y = [_label(v) for v in data.y()]
    weights = [0.0] * len(model["features"])
    bias = 0.0
    rate, l2, count = params["learning_rate"], params["l2"], max(len(x), 1)
    for _ in range(params["epochs"]):
        gradient = [0.0] * len(weights)
        gradient_bias = 0.0
        for row, label in zip(x, y):
            error = _sigmoid(sum(w * v for w, v in zip(weights, row)) + bias) - label
            for index, value in enumerate(row):
                gradient[index] += error * value
            gradient_bias += error
        weights = [w - rate * (g / count + l2 * w) for w, g in zip(weights, gradient)]
        bias -= rate * gradient_bias / count
    model["weights"] = weights
    model["bias"] = bias
    return model


def _probabilities(model, data):
    return [
        _sigmoid(sum(w * v for w, v in zip(model["weights"], row)) + model["bias"])
        for row in _matrix(model, data)
    ]


def predict(artifact, data):
    return _probabilities(artifact, data)


def _auc(labels, scores):
    positives = [s for s, l in zip(scores, labels) if l == 1.0]
    negatives = [s for s, l in zip(scores, labels) if l == 0.0]
    if not positives or not negatives:
        return 0.5
    wins = 0.0
    for p in positives:
        for n in negatives:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(positives) * len(negatives))


def score(artifact, data):
    labels = [_label(v) for v in data.y()]
    probabilities = _probabilities(artifact, data)
    epsilon = 1e-12
    log_loss = -sum(
        l * math.log(max(p, epsilon)) + (1 - l) * math.log(max(1 - p, epsilon))
        for p, l in zip(probabilities, labels)
    ) / max(len(labels), 1)
    accuracy = sum((p >= 0.5) == (l == 1.0) for p, l in zip(probabilities, labels)) / max(len(labels), 1)
    return {"auc": _auc(labels, probabilities), "accuracy": accuracy, "log_loss": log_loss}


def explain(artifact, data):
    return [
        {name: weight * value for name, weight, value in zip(artifact["features"], artifact["weights"], row)}
        for row in _matrix(artifact, data)
    ]
