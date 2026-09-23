"""Compatibility wrapper for the shared offline converter."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_shared_path = Path(__file__).resolve().parents[1] / "quant_lab_tools" / "ml_excel_converter.py"
_spec = spec_from_file_location("shared_ml_excel_converter", _shared_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load shared converter: {_shared_path}")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
build_ml_excel_sheet = _module.build_ml_excel_sheet
resolve_input = _module.resolve_input
