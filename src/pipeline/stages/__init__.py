"""V2 pipeline stages — one stage per file."""

from src.pipeline.stages.bearing import BearingStage
from src.pipeline.stages.burst import BurstStage
from src.pipeline.stages.classify import ClassifyStage
from src.pipeline.stages.cluster import ClusterStage
from src.pipeline.stages.fuse import FuseStage
from src.pipeline.stages.source import SourceStage
from src.pipeline.stages.spectrogram import SpectrogramStage
from src.pipeline.stages.track import TrackStage

__all__ = [
    "BearingStage",
    "BurstStage",
    "ClassifyStage",
    "ClusterStage",
    "FuseStage",
    "SourceStage",
    "SpectrogramStage",
    "TrackStage",
]
