"""V2 pipeline stages.

One file per stage. Each stage subclasses ``src.pipeline.stage.Stage`` and
implements ``async def process(in_msg) -> out_msg``. Stage files are populated
in Phase 1; this package is intentionally empty in Phase 0.
"""
