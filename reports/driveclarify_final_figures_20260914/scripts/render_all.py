"""Reproduce the four frozen figures without invoking experiments or analysis."""
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
for letter in 'abcd':
    subprocess.run([sys.executable, '-B', str(HERE / f'render_figure_{letter}.py')], check=True)
