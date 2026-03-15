import json
import re
import sys
import math
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Set, Optional

# Constants
SHOT_DURATION = 15.0  # seconds
WPM = 180.0  # Words per minute estimation for mixed content
WPS = WPM / 60.0  # Words per second (approx 3.0)
MIN_DURATION = 5.0 # Min duration to avoid tiny fragments if possible

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

class ScriptToStoryboardConverter:
    def __init__(self, adapted_dir: Path):
        self.adapted_dir = adapted_dir
        self.rewrite_dir = adapted_dir / "rewrite"
        self.entity_merge_path = adapted_dir / "entity_merge.json"
        self.name_map_path = adapted_dir / "name_map.json"
        
        # Maps for entity resolution
        self.target_to_canonical: Dict[str, str] = {} # Aurora -> Sophia
        self.canonical_to_type: Dict[str, str] = {}   # Sophia -> person
        self.search_patterns: Dict[str, str] = {}     # lowercase target -> canonical

        self._load_metadata()

    def _load_metadata(self):
        """Load entity and name mapping data."""
        if not self.entity_merge_path.exists() or not self.name_map_path.exists():
            print(f"Warning: Metadata files not found in {self.adapted_dir}")
            return

        try:
            # Load Name Map: Key(Canonical) -> Value(Target)
            with open(self.name_map_path, 'r', encoding='utf-8') as f:
                name_map = json.load(f)
            
            # Load Entity Types
            with open(self.entity_merge_path, 'r', encoding='utf-8') as f:
                merge_data = json.load(f)
            
            canonical_types = {}
            for ent in merge_data.get('entities', []):
                canonical_types[ent.get('canonical_name')] = ent.get('type', 'unknown')
            
            # Build lookups
            for canonical, target in name_map.items():
                if not isinstance(target, str):
                    continue
                    
                self.target_to_canonical[target] = canonical
                self.canonical_to_type[canonical] = canonical_types.get(canonical, 'unknown')
                
                # We search for the Target name in text, but map to Canonical
                self.search_patterns[target.lower()] = canonical
                
                # Also handle aliases if needed? For now rely on name_map primary target.
                
            print(f"Loaded {len(self.search_patterns)} entity patterns.")
            
        except Exception as e:
            print(f"Error loading metadata: {e}")

    def _clean_text(self, text: str) -> str:
        """Remove decorative headers/footers and clean up text."""
        lines = text.split('\n')
        cleaned_lines = []
        
        # Simple heuristic to skip decorative headers
        # Skip lines that are just symbols or short chapter markers
        content_started = False
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            
            # Skip decorative lines like ". ݁₊ ⊹ ." or "࣪ ִֶָ☾."
            if re.match(r'^[\.\s\W\u2000-\u206F\u2E00-\u2E7F\u3000-\u303F]+$', stripped) or len(stripped) < 2:
                continue
            
            # Additional check for lines that are just non-alphanumeric chars (allowing some punctuation)
            if not re.search(r'[a-zA-Z0-9]', stripped):
                continue
                
            # Skip "CHAPTER TWO—sunshine" type headers
            if re.match(r'^CHAPTER\s+[A-Z]+', stripped, re.IGNORECASE):
                continue
                
            # Skip "A/N" author notes at the end
            if stripped.startswith("A/N") or stripped.startswith("Author's Note"):
                break # Assume end of story content
                
            cleaned_lines.append(stripped)
            
        return "\n".join(cleaned_lines)

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences using regex."""
        # Simple split by . ! ? followed by space or quote
        # This is a basic approximation
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'\(\[])', text)
        return [s.strip() for s in sentences if s.strip()]

    def _estimate_duration(self, text: str) -> float:
        """Estimate duration based on word count."""
        words = len(text.split())
        return max(words / WPS, 1.0)

    def _extract_entities(self, text: str) -> List[str]:
        """Find canonical entity names mentioned in text."""
        found = set()
        text_lower = text.lower()
        
        for pattern_lower, canonical in self.search_patterns.items():
            # Whole word matching
            pattern = r'\b' + re.escape(pattern_lower) + r'\b'
            if re.search(pattern, text_lower):
                found.add(canonical)
                
        return sorted(list(found))

    def _process_chapter(self, chapter_dir: Path):
        """Process a single chapter."""
        input_path = chapter_dir / "adapted.txt"
        if not input_path.exists():
            return

        print(f"Processing {chapter_dir.name}...")
        
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                raw_text = f.read()
        except Exception as e:
            print(f"Error reading {input_path}: {e}")
            return

        clean_text = self._clean_text(raw_text)
        sentences = self._split_into_sentences(clean_text)
        
        shots = []
        shot_id = 1
        
        current_shot_content = []
        current_word_count = 0
        current_entities = set()
        
        # Current inferred location (persist across shots until changed)
        current_location = "UNKNOWN" 
        
        for sentence in sentences:
            # Estimate added duration
            words = len(sentence.split())
            duration = words / WPS
            
            # Check if adding this sentence would exceed target significantly
            # We target 15s (approx 45 words)
            # If current + new > 15s + margin, maybe split now?
            # But we want to fill up to 15s.
            
            # Simple greedy packing:
            # Add sentence. If total duration >= 15s, finalize shot.
            
            current_shot_content.append(sentence)
            current_word_count += words
            
            # Extract entities for this sentence
            ents = self._extract_entities(sentence)
            current_entities.update(ents)
            
            # Update location context if a location entity is found
            for ent in ents:
                if self.canonical_to_type.get(ent) == 'location':
                    current_location = ent # Update current location context
            
            current_duration = current_word_count / WPS
            
            if current_duration >= SHOT_DURATION:
                # Finalize shot
                content_str = " ".join(current_shot_content)
                
                # Determine location for this shot
                # Use entities found in this shot, or fallback to context
                shot_locations = [e for e in current_entities if self.canonical_to_type.get(e) == 'location']
                if not shot_locations and current_location != "UNKNOWN":
                    # If no explicit location mentioned, assume we are still in previous location
                    # But add it to locations list? Or just Scene Heading?
                    pass
                
                # Scene Heading Construction
                # If a location entity is present, use it. Else use context.
                if shot_locations:
                    primary_loc = shot_locations[0]
                    scene_heading = f"EXT/INT. {primary_loc.upper()}" # We don't know INT/EXT, generic
                elif current_location != "UNKNOWN":
                    scene_heading = f"EXT/INT. {current_location.upper()}"
                else:
                    scene_heading = "SCENE"

                shots.append(Shot(
                    id=shot_id,
                    duration=15.0, # Force exactly 15s as requested? Or close estimate?
                                   # User said "Each shot must be fixed to 15s"
                                   # I will set it to 15.0 in JSON, even if text varies slightly.
                    content=content_str,
                    scene_heading=scene_heading,
                    entities=sorted(list(current_entities)),
                    locations=sorted(list(set(shot_locations) if shot_locations else ([current_location] if current_location != "UNKNOWN" else [])))
                ))
                
                shot_id += 1
                current_shot_content = []
                current_word_count = 0
                current_entities = set()

        # Handle remainder
        if current_shot_content:
            content_str = " ".join(current_shot_content)
            
            shot_locations = [e for e in current_entities if self.canonical_to_type.get(e) == 'location']
            if shot_locations:
                primary_loc = shot_locations[0]
                scene_heading = f"EXT/INT. {primary_loc.upper()}"
            elif current_location != "UNKNOWN":
                scene_heading = f"EXT/INT. {current_location.upper()}"
            else:
                scene_heading = "SCENE"

            shots.append(Shot(
                id=shot_id,
                duration=15.0, # Pad remainder to 15s?
                content=content_str,
                scene_heading=scene_heading,
                entities=sorted(list(current_entities)),
                locations=sorted(list(set(shot_locations) if shot_locations else ([current_location] if current_location != "UNKNOWN" else [])))
            ))

        # Save output
        output_path = chapter_dir / "storyboard.json"
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump([asdict(s) for s in shots], f, indent=2, ensure_ascii=False)
            print(f"Saved {len(shots)} shots to {output_path}")
        except Exception as e:
            print(f"Error saving {output_path}: {e}")

    def run(self):
        """Run conversion on all chapters."""
        if not self.rewrite_dir.exists():
            print(f"Rewrite directory not found: {self.rewrite_dir}")
            return

        for chapter_dir in sorted(self.rewrite_dir.iterdir()):
            if chapter_dir.is_dir() and (chapter_dir / "adapted.txt").exists():
                self._process_chapter(chapter_dir)

def main():
    if len(sys.argv) < 2:
        print("Usage: python script_to_storyboard.py <adapted_directory>")
        adapted_dir = Path("/Users/hjr/Desktop/script-fusion/output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted")
    else:
        adapted_dir = Path(sys.argv[1])
        
    converter = ScriptToStoryboardConverter(adapted_dir)
    converter.run()

if __name__ == "__main__":
    main()
