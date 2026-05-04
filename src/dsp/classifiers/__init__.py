"""Rule-based protocol classifiers for V2 Stage 2.

One file per protocol (elrs.py, ocusync.py, wifi.py). Each module exposes
a function that takes a ``Candidate`` and returns a ``Classification`` (or
``None`` if the rules don't match). Populated in Phase 1.
"""
