import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional

# Constants
SHOT_DURATION = 15.0  # seconds
WPM = 180.0  # Words per minute estimation for mixed content
WPS = WPM / 60.0  # Words per second (approx 3.0)

@dataclass
class Entity:
    canonical_name: str # The Key (e.g., Sophia)
    target_name: str    # The Value used in text (e.g., Aurora)
    type: str
    aliases: List[str] = field(default_factory=list)

@dataclass
class Shot:
    id: int
    duration: float
    content: str
    scene_heading: str
    entities: List[str]  # List of canonical_names
    locations: List[str] # List of canonical_names of locations

def load_names(json_path: Path) -> list[str]:
    """Load character names from the input JSON file."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            name_map = data.get("name_map", {})
            return [k for k in name_map.keys() if isinstance(k, str)]
    except Exception as e:
        print(f"Warning: Failed to load names from {json_path}: {e}")
        return []

def load_name_map(json_path: Path) -> Dict[str, str]:
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            name_map = data.get("name_map", {})
            if not isinstance(name_map, dict):
                return {}
            normalized: Dict[str, str] = {}
            for key, value in name_map.items():
                if isinstance(key, str) and isinstance(value, str):
                    k = key.strip()
                    v = value.strip()
                    if k and v:
                        normalized[k] = v
            return normalized
    except Exception as e:
        print(f"Warning: Failed to load name_map from {json_path}: {e}")
        return {}

def clean_text_for_storyboard(text: str) -> str:
    """Clean text for storyboard processing."""
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped: continue
        
        # Skip decorative lines
        if re.match(r'^[\.\s\W\u2000-\u206F\u2E00-\u2E7F\u3000-\u303F]+$', stripped) or len(stripped) < 2:
            continue
        if not re.search(r'[a-zA-Z0-9]', stripped):
            continue
        if re.match(r'^CHAPTER\s+[A-Z]+', stripped, re.IGNORECASE):
            continue
        if stripped.startswith("A/N") or stripped.startswith("Author's Note"):
            break
            
        cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines)

def split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using regex."""
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\(\[])', text)
    return [s.strip() for s in sentences if s.strip()]
