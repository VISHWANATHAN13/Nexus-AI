"""Run as a standalone local process:  python -m insurance_server   (stdio transport)."""
from config import setup_environment
from insurance_server.server import build_server

if __name__ == "__main__":
    setup_environment()
    build_server().run()
