# -*- coding: utf-8 -*-
"""Repo root. Override with IG_FARM_BASE or THREADS_FARM_BASE."""
import os

ROOT = os.path.abspath(
    os.environ.get("IG_FARM_BASE")
    or os.environ.get("THREADS_FARM_BASE")
    or os.path.dirname(os.path.abspath(__file__))
)
