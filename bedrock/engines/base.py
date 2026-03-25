"""Shared adapter utilities for loading external engine modules."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Optional


class EngineAdapterError(RuntimeError):
    """Raised when an engine module is unavailable or incompatible."""


def load_engine_module(module_name: Optional[str] = None, module_path: Optional[str] = None) -> ModuleType:
    if module_name:
        return importlib.import_module(module_name)

    if module_path:
        path = Path(module_path).expanduser().resolve()
        if not path.exists():
            raise EngineAdapterError(f"Engine module path does not exist: {path}")

        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise EngineAdapterError(f"Unable to load engine module from path: {path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    raise EngineAdapterError("No engine module_name or module_path configured.")


def call_engine(module: ModuleType, function_name: str, *args, **kwargs):
    fn = getattr(module, function_name, None)
    if fn is None:
        raise EngineAdapterError(f"Engine module '{module.__name__}' does not export '{function_name}'.")
    return fn(*args, **kwargs)
