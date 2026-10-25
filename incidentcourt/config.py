"""Load project-only settings without sourcing shell or printing credentials."""
import os
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    path = ROOT / '.env.local'
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if key.replace('_', '').isalnum():
                value=value.strip()
                if value.startswith('"'):
                    try:
                        value=json.loads(value)
                    except (ValueError,TypeError):
                        raise ValueError('Invalid quoted project configuration value.') from None
                    if not isinstance(value,str):
                        raise ValueError('Project configuration values must be strings.')
                elif value.startswith("'") and value.endswith("'"):
                    value=value[1:-1]
                os.environ.setdefault(key, value)


load_env()
