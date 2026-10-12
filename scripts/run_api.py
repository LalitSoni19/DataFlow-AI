"""Start the local API server with uvicorn."""

from __future__ import annotations

import os
import uvicorn

if __name__ == "__main__":
    host = os.getenv("DATAFLOW_HOST", "127.0.0.1")
    port = int(os.getenv("DATAFLOW_PORT", "8000"))
    debug = os.getenv("DATAFLOW_DEBUG", "false").lower() in ("true", "1")
    uvicorn.run("dataflow_ai.api.main:app", host=host, port=port, reload=debug)


