"""Tests for src.pipeline.stages.cluster.ClusterStage."""

import asyncio

from src.pipeline.contracts import Burst, ChannelRole
from src.pipeline.stages.cluster import ClusterStage
from tests.unit._v2_helpers import make_v2_test_config


def _burst(burst_id: str = "b") -> Burst:
    return Burst(
        burst_id=burst_id,
        role=ChannelRole.OMNI,
        start_time_s=0.0,
        end_time_s=0.002,
        freq_lo_hz=2.4365e9,
        freq_hi_hz=2.4373e9,
        peak_power_dbm=-55.0,
        snr_db=20.0,
        bin_start=100,
        bin_end=140,
        frame_start_index=0,
        frame_end_index=8,
    )


class TestClusterStage:
    def test_empty_input_empty_output(self):
        stage = ClusterStage(make_v2_test_config())
        assert asyncio.run(stage.process(())) == ()

    def test_one_burst_yields_one_candidate(self):
        stage = ClusterStage(make_v2_test_config())
        out = asyncio.run(stage.process((_burst(),)))
        assert len(out) == 1
        cand = out[0]
        assert cand.candidate_id.startswith("cand-")
        assert cand.num_bursts == 1
        assert cand.bandwidth_hz == 0.8e6

    def test_candidate_ids_increment_deterministically(self):
        stage = ClusterStage(make_v2_test_config())
        out = asyncio.run(stage.process((_burst("b1"), _burst("b2"))))
        assert [c.candidate_id for c in out] == ["cand-1", "cand-2"]

    def test_reset_resets_id_counter(self):
        stage = ClusterStage(make_v2_test_config())
        asyncio.run(stage.process((_burst("b1"),)))
        stage.reset()
        out = asyncio.run(stage.process((_burst("b2"),)))
        assert out[0].candidate_id == "cand-1"
