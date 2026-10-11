"""Run as a standalone local process:  python -m wealth_server   (stdio transport)."""
from config import setup_environment
from wealth_server.server import build_server

if __name__ == "__main__":
    setup_environment()
    build_server().run()
