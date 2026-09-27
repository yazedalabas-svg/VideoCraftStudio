"""Run the web version of VideoCraft Studio (used by Render).

    python server.py            # listens on $PORT (Render sets it) or 8000

The desktop app (app.py, PyQt6) is unchanged; the web app in web/ reuses
video_engine.py for the same FFmpeg enhancement pipeline, minus the GPU AI models.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    sys.path.insert(0, str(ROOT))  # so `video_engine` and `web` import from the checkout
    os.chdir(ROOT)

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"VideoCraft Studio web app on port {port}", flush=True)
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=port,
        workers=1,  # jobs live in memory; a single process keeps them consistent
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
