"""§66.1 State Pool — 状态到监督池的派生映射。"""

from __future__ import annotations

import pytest

from src.domain import State
from src.supervision.state_pool import PoolName, StatePoolManager, SupervisionLevel


@pytest.fixture
def pools() -> StatePoolManager:
    return StatePoolManager()


class TestStatePoolMapping:
    """§66.1 状态的正确池归属。"""

    def test_anomaly_to_anomaly_pool(self, pools):
        assert pools.pool_for(State.ANOMALY) == PoolName.ANOMALY

    def test_suspected_start_to_watch_pool(self, pools):
        assert pools.pool_for(State.SUSPECTED_START) == PoolName.WATCH

    def test_start_confirmed_to_confirmed_pool(self, pools):
        assert pools.pool_for(State.START_CONFIRMED) == PoolName.CONFIRMED

    def test_continuation_to_continuation_pool(self, pools):
        assert pools.pool_for(State.CONTINUATION) == PoolName.CONTINUATION

    def test_exhaustion_to_risk_pool(self, pools):
        assert pools.pool_for(State.EXHAUSTION) == PoolName.RISK

    def test_withdrawal_to_exit_pool(self, pools):
        assert pools.pool_for(State.WITHDRAWAL) == PoolName.EXIT


class TestStatePoolExtras:
    """补充映射：NORMAL / ARCHIVE 及派生标签覆盖。"""

    def test_sleeping_to_normal_pool(self, pools):
        assert pools.pool_for(State.SLEEPING) == PoolName.NORMAL

    def test_cooldown_to_normal_pool(self, pools):
        assert pools.pool_for(State.COOLDOWN) == PoolName.NORMAL

    def test_rejected_to_archive_pool(self, pools):
        assert pools.pool_for(State.REJECTED) == PoolName.ARCHIVE

    def test_label_override_distribution_to_risk(self, pools):
        """派生标签覆盖 State 映射（§6.6 DISTRIBUTION → RISK）。"""
        assert pools.pool_for(State.CONTINUATION, labels=["distribution"]) == PoolName.RISK

    def test_label_override_accumulation_to_watch(self, pools):
        """§6.3 ACCUMULATION → WATCH。"""
        assert pools.pool_for(State.ANOMALY, labels=["accumulation"]) == PoolName.WATCH

    def test_label_precedence_exit_over_watch(self, pools):
        """同 tick 多标签：更接近终局的池优先（direction_flip > breakout_start）。"""
        pool = pools.pool_for(State.SUSPECTED_START, labels=["breakout_start", "direction_flip"])
        assert pool == PoolName.EXIT

    def test_label_case_insensitive(self, pools):
        assert pools.pool_for(State.SLEEPING, labels=["DISTRIBUTION"]) == PoolName.RISK

    def test_unknown_label_ignored(self, pools):
        assert pools.pool_for(State.SLEEPING, labels=["something_new"]) == PoolName.NORMAL

    def test_all_states_mapped(self, pools):
        for state in State:
            assert pools.pool_for(state) in set(PoolName)


class TestPoolMetadata:
    def test_eight_pools(self, pools):
        assert set(pools.pool_names) == set(PoolName)

    def test_spec_has_focus_and_question(self, pools):
        for pool in PoolName:
            spec = pools.spec(pool)
            assert spec.title
            assert spec.focus
            assert spec.supervision_question

    def test_supervision_question_state_aware(self, pools):
        """§8 不同状态不能共用同一套监督条件。"""
        assert "异常" in pools.spec(PoolName.ANOMALY).supervision_question
        assert "确认" in pools.spec(PoolName.WATCH).supervision_question
        assert "结束" in pools.spec(PoolName.EXIT).supervision_question

    def test_level_derived(self, pools):
        assert pools.level_for(PoolName.NORMAL) == SupervisionLevel.LOW
        assert pools.level_for(PoolName.WATCH) == SupervisionLevel.HIGH
        assert pools.level_for(PoolName.EXIT) == SupervisionLevel.HIGH