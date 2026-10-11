"""Build the local vector index over the knowledge folders (needs OPENAI_API_KEY for embeddings).

    python build_index.py            normal index
    python build_index.py --demo     index that also includes the deliberately poisoned demo document
"""
import sys

from config import setup_environment
from knowledge import build_index

if __name__ == "__main__":
    setup_environment()
    print(build_index(include_demo="--demo" in sys.argv))
