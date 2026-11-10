"""Chronological splitting and validation for the delivery-delay model.

The model is trained on the past and applied to the future, so every boundary here is a date.
No shuffling, no ``train_test_split``. Each partition is a contiguous window of purchase
timestamps, and the windows are asserted to be ordered and non-overlapping so an accidental
config change cannot leak the future into training.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CONFIG_PATH: Path = PROJECT_ROOT / "configs" / "model.yaml"
PURCHASE_COLUMN = "order_purchase_timestamp"
LABEL_COLUMN = "is_late"


@dataclass(frozen=True)
class SplitWindows:
    """Inclusive end-dates for the three contiguous chronological windows."""

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_end: pd.Timestamp
    test_end: pd.Timestamp

    def labels(self) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
        """Return half-open ``[start, end)`` bounds per partition."""
        return {
            "train": (self.train_start, self._day_after(self.train_end)),
            "validation": (self._day_after(self.train_end), self._day_after(self.validation_end)),
            "test": (self._day_after(self.validation_end), self._day_after(self.test_end)),
        }

    @staticmethod
    def _day_after(ts: pd.Timestamp) -> pd.Timestamp:
        return pd.Timestamp(ts).normalize() + pd.Timedelta(days=1)

    def assert_ordered(self) -> None:
        """Raise unless ``train_end < validation_end < test_end``."""
        if not self.train_start < self.train_end < self.validation_end < self.test_end:
            raise ValueError(
                "split cutoffs are not strictly increasing: "
                f"{self.train_start.date()} -> {self.train_end.date()} -> "
                f"{self.validation_end.date()} -> {self.test_end.date()}"
            )


@dataclass(frozen=True)
class Split:
    """Train/validation/test frames plus the figures that justify the boundaries."""

    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    windows: SplitWindows

    @property
    def summary(self) -> pd.DataFrame:
        rows = []
        for name, frame in (
            ("train", self.train),
            ("validation", self.validation),
            ("test", self.test),
        ):
            rows.append(
                {
                    "partition": name,
                    "orders": len(frame),
                    "late": int(frame[LABEL_COLUMN].sum()),
                    "late_rate": float(frame[LABEL_COLUMN].mean()),
                    "purchase_from": frame[PURCHASE_COLUMN].min(),
                    "purchase_to": frame[PURCHASE_COLUMN].max(),
                }
            )
        return pd.DataFrame(rows)


def load_model_config(path: Path | None = None) -> dict:
    """Load ``configs/model.yaml``."""
    config_path = path or CONFIG_PATH
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def split_windows_from_config(config: dict | None = None) -> SplitWindows:
    """Read the four cutoff dates out of the ``split`` block."""
    block = (config or load_model_config())["split"]
    windows = SplitWindows(
        train_start=pd.Timestamp(block["train_start"]),
        train_end=pd.Timestamp(block["train_end"]),
        validation_end=pd.Timestamp(block["validation_end"]),
        test_end=pd.Timestamp(block["test_end"]),
    )
    windows.assert_ordered()
    return windows


def chronological_split(
    order_level: pd.DataFrame,
    windows: SplitWindows | None = None,
) -> Split:
    """Partition labelled orders into train/validation/test by purchase date.

    Unlabelled rows (orders that never completed, or completed without a timestamps) are
    excluded: they cannot contribute to either fitting or scoring.
    """
    windows = windows or split_windows_from_config()
    windows.assert_ordered()

    labelled = order_level[order_level[LABEL_COLUMN].notna()].copy()
    labelled[LABEL_COLUMN] = labelled[LABEL_COLUMN].astype(int)

    bounds = windows.labels()
    frames = {}
    for name, (start, end) in bounds.items():
        mask = (labelled[PURCHASE_COLUMN] >= start) & (labelled[PURCHASE_COLUMN] < end)
        frames[name] = labelled.loc[mask].copy()

    dropped = len(order_level) - sum(len(f) for f in frames.values())
    if dropped:
        logger.info(
            "chronological split discarded %s rows outside %s..%s",
            f"{dropped:,}",
            windows.train_start.date(),
            windows.test_end.date(),
        )

    split = Split(
        train=frames["train"],
        validation=frames["validation"],
        test=frames["test"],
        windows=windows,
    )
    for row in split.summary.itertuples():
        logger.info(
            "%s: %s orders, %s late (%.2f%%)",
            row.partition,
            f"{row.orders:,}",
            f"{row.late:,}",
            row.late_rate * 100,
        )
    return split


def temporal_cv_splits(
    train: pd.DataFrame,
    *,
    n_splits: int = 4,
    purge_days: int = 30,
    purchase_column: str = PURCHASE_COLUMN,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds over the training period, with a purge gap at each boundary.

    Folds cut at calendar boundaries so every order in a fold shares the same cut-off date.
    ``purge_days`` removes orders from the end of each fit window whose label could have been
    observed after the following fold started — an order purchased on the last day of a fit
    window is typically delivered a week or two later.

    Returned indices are **positional** (safe to pass to ``DataFrame.iloc``) and refer to
    ``train`` in its original row order.
    """
    if not 1 <= n_splits <= 10:
        raise ValueError(f"n_splits must be between 1 and 10, got {n_splits}")

    timestamps = train[purchase_column].to_numpy()
    order_positions = np.argsort(timestamps, kind="mergesort")
    sorted_days = pd.DatetimeIndex(timestamps[order_positions]).normalize().to_numpy()

    unique_days = np.array(sorted(pd.unique(sorted_days)))
    if len(unique_days) < n_splits + 1:
        raise ValueError("training window too short for the requested number of folds")

    # Reserve the first third of the window for the initial fit, then expand fold by fold.
    first = len(unique_days) // 3
    boundaries = np.unique(np.linspace(first, len(unique_days) - 1, n_splits + 1).astype(int))
    if len(boundaries) < 2:
        raise ValueError("training window too short for the requested number of folds")

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for fit_end_idx, eval_end_idx in itertools.pairwise(boundaries):
        eval_start = pd.Timestamp(unique_days[fit_end_idx])
        eval_end = pd.Timestamp(unique_days[eval_end_idx])
        purge_before = eval_start - pd.Timedelta(days=purge_days)

        fit_mask = sorted_days < purge_before.to_datetime64()
        eval_mask = (sorted_days >= eval_start.to_datetime64()) & (
            sorted_days < eval_end.to_datetime64()
        )
        if not fit_mask.any() or not eval_mask.any():
            continue

        folds.append((np.sort(order_positions[fit_mask]), order_positions[eval_mask]))

    if not folds:
        raise ValueError("no usable temporal folds were produced")
    return folds
