"""Run the notebook's offline tests without Jupyter or any live API calls.

Usage: python validate_notebook.py
"""
from pathlib import Path
import unittest
import sys
import types
import nbformat

path = Path(__file__).with_name('regulatory_agentic_layer_qwen3.ipynb')
notebook = nbformat.read(path, as_version=4)
nbformat.validate(notebook)
# Register a real module so deferred type annotations resolve as in a kernel.
module = types.ModuleType('capstone_notebook_validation')
sys.modules[module.__name__] = module
namespace = module.__dict__
for index, cell in enumerate(notebook.cells):
    if cell.cell_type == 'code' and set(cell.metadata.get('tags', [])) & {'library', 'tests'}:
        exec(compile(cell.source, f'{path.name}:cell-{index + 1}', 'exec'), namespace)
suite = unittest.defaultTestLoader.loadTestsFromTestCase(namespace['AgentTests'])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
