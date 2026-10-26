from datetime import date

import pytest

from app.prototypes import AssetStatus, DateRange, WorkflowAction, WorkflowGuard


def test_date_range_rejects_inverted_dates():
    with pytest.raises(ValueError):
        DateRange(date(2026, 2, 2), date(2026, 2, 1))


def test_retired_asset_has_no_actions():
    assert WorkflowGuard.actions_for(AssetStatus.RETIRED) == ()


def test_assigned_asset_can_return_or_transfer_but_not_assign_again():
    WorkflowGuard.assert_allowed(AssetStatus.ASSIGNED, WorkflowAction.RETURN)
    WorkflowGuard.assert_allowed(AssetStatus.ASSIGNED, WorkflowAction.TRANSFER)
    with pytest.raises(ValueError):
        WorkflowGuard.assert_allowed(AssetStatus.ASSIGNED, WorkflowAction.ASSIGN)
