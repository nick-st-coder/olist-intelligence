"""Tests for the chronological split and the temporal cross-validation folds."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.modeling.splitting import (
    SplitWindows,
    chronological_split,
    split_windows_from_config,
    temporal_cv_splits,
)

PURCHASE = "order_purchase_timestamp"


def _frame(start: str, end: str, freq: str = "D") -> pd.DataFrame:
    timeline = pd.date_range(start, end, freq=freq)
    return pd.DataFrame(
        {
            "order_id": [f"o{i}" for i in range(len(timeline))],
            PURCHASE: timeline,
            "is_late": [i % 2 for i in range(len(timeline))],
        }
    )


def test_windows_are_strictly_ordered() -> None:
    windows = SplitWindows(
        train_start=pd.Timestamp("2017-01-01"),
        train_end=pd.Timestamp("2018-03-31"),
        validation_end=pd.Timestamp("2018-05-31"),
        test_end=pd.Timestamp("2018-08-31"),
    )
    windows.assert_ordered()


def test_out_of_order_windows_raise() -> None:
    windows = SplitWindows(
        train_start=pd.Timestamp("2017-01-01"),
        train_end=pd.Timestamp("2018-05-31"),
        validation_end=pd.Timestamp("2018-03-31"),
        test_end=pd.Timestamp("2018-08-31"),
    )
    with pytest.raises(ValueError, match="not strictly increasing"):
        windows.assert_ordered()


def test_split_is_chronological_and_disjoint() -> None:
    frame = _frame("2017-01-01", "2018-12-31")
    split = chronological_split(frame)

    assert split.train[PURCHASE].max() < split.validation[PURCHASE].min()
    assert split.validation[PURCHASE].max() < split.test[PURCHASE].min()
    assert split.train[PURCHASE].max().date() <= pd.Timestamp("2018-03-31").date()
    assert split.validation[PURCHASE].min().date() >= pd.Timestamp("2018-04-01").date()
    assert split.test[PURCHASE].min().date() >= pd.Timestamp("2018-06-01").date()


def test_split_windows_match_the_config() -> None:
    windows = split_windows_from_config()
    assert windows.train_end.month == 3
    assert windows.train_end.year == 2018


def test_split_drops_unlabelled_and_out_of_range_rows() -> None:
    frame = _frame("2016-06-01", "2018-12-31")
    frame["is_late"] = frame["is_late"].astype("Float64")
    frame.loc[frame.index[:5], "is_late"] = pd.NA

    split = chronological_split(frame)
    kept = len(split.train) + len(split.validation) + len(split.test)

    in_window = (frame[PURCHASE] >= pd.Timestamp("2017-01-01")) & (
        frame[PURCHASE] <= pd.Timestamp("2018-08-31")
    )
    expected = len(frame[(frame["is_late"].notna()) & in_window])
    assert kept == expected
    assert kept < len(frame)


def test_train_precedes_validation_precedes_test_in_time() -> None:
    split = chronological_split(_frame("2017-01-01", "2018-12-31"))
    assert split.train[PURCHASE].max() < split.validation[PURCHASE].min()
    assert split.validation[PURCHASE].max() < split.test[PURCHASE].min()


def test_no_order_appears_in_two_partitions() -> None:
    split = chronological_split(_frame("2017-01-01", "2018-12-31"))
    ids = (
        set(split.train["order_id"])
        | set(split.validation["order_id"])
        | set(split.test["order_id"])
    )
    total = len(split.train) + len(split.validation) + len(split.test)
    assert len(ids) == total


def test_cv_folds_expand_and_are_time_ordered() -> None:
    train = _frame("2017-01-01", "2018-03-31")
    folds = temporal_cv_splits(train, n_splits=4, purge_days=30)

    assert len(folds) == 4
    previous_size = 0
    for fit_idx, eval_idx in folds:
        assert set(fit_idx).isdisjoint(eval_idx)
        assert len(fit_idx) > previous_size  # expanding window
        previous_size = len(fit_idx)

        fit_end = train.iloc[fit_idx][PURCHASE].max()
        eval_start = train.iloc[eval_idx][PURCHASE].min()
        assert fit_end < eval_start
        assert (eval_start - fit_end).days > 30


def test_cv_discards_a_purge_gap() -> None:
    train = _frame("2017-01-01", "2018-03-31")
    folds = temporal_cv_splits(train, n_splits=4, purge_days=60)
    for fit_idx, eval_idx in folds:
        fit_end = train.iloc[fit_idx][PURCHASE].max()
        eval_start = train.iloc[eval_idx][PURCHASE].min()
        assert (eval_start - fit_end).days > 60


def test_cv_rejects_an_invalid_fold_count() -> None:
    with pytest.raises(ValueError, match="n_splits"):
        temporal_cv_splits(_frame("2017-01-01", "2018-03-31"), n_splits=0)


def test_cv_rejects_a_too_short_window() -> None:
    with pytest.raises(ValueError, match="too short"):
        temporal_cv_splits(_frame("2018-01-01", "2018-01-02"), n_splits=4)


def test_cv_indices_are_positional() -> None:
    train = _frame("2017-01-01", "2017-06-30").reset_index(drop=True)
    folds = temporal_cv_splits(train, n_splits=3)
    for fit_idx, eval_idx in folds:
        assert fit_idx.max() < len(train)
        assert eval_idx.max() < len(train)
        assert np.issubdtype(fit_idx.dtype, np.integer)
