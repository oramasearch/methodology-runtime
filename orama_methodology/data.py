# SPDX-License-Identifier: MIT

"""The data object a methodology's functions receive."""

from __future__ import annotations

from typing import Any, Iterable


class Data:
    """Rows of one split, with the target and feature columns named.

    `rows` is a list of dicts, one per row, as Lake read them from the workspace. `columns` lists
    every column seen, in first-seen order. `target` is the target column's name, or None for a
    methodology with no target. `features` are the columns a methodology should learn from: the
    run's features when Lake names them, otherwise every column but the target.
    """

    def __init__(
        self,
        rows: Iterable[dict[str, Any]],
        target: str | None = None,
        features: list[str] | None = None,
    ) -> None:
        self.rows: list[dict[str, Any]] = list(rows)
        columns: list[str] = []
        seen: set[str] = set()
        for row in self.rows:
            for name in row:
                if name not in seen:
                    seen.add(name)
                    columns.append(name)
        self.columns: list[str] = columns
        self.target: str | None = target or None
        if features:
            self.features: list[str] = list(features)
        else:
            self.features = [name for name in columns if name != self.target]

    def __len__(self) -> int:
        return len(self.rows)

    def X(self) -> list[list[Any]]:
        """The feature values, one list per row, in `features` order (None where missing)."""
        return [[row.get(name) for name in self.features] for row in self.rows]

    def y(self) -> list[Any] | None:
        """The target values, one per row, or None when there is no target."""
        if self.target is None:
            return None
        return [row.get(self.target) for row in self.rows]

    def subset(self, indices: Iterable[int]) -> "Data":
        """The rows at `indices`, with the same target and features."""
        return Data([self.rows[index] for index in indices], self.target, self.features)

    def __repr__(self) -> str:
        return (
            f"Data(rows={len(self.rows)}, target={self.target!r}, "
            f"features={len(self.features)})"
        )
