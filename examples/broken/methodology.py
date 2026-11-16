# SPDX-License-Identifier: MIT

"""A broken example: it has fit and predict but no score."""


def fit(data, params):
    labels = data.y()
    return sum(1 for value in labels if value in (1, True, "1")) / max(len(labels), 1)


def predict(artifact, data):
    return [artifact] * len(data)
