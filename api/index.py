import sys
import os
import traceback

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

error_on_import = None
try:
    from app.main import app
except Exception as e:
    error_on_import = traceback.format_exc()
    print("STARTUP_ERROR:", error_on_import)
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    app = FastAPI()

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
    async def debug_error(full_path: str = ""):
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:30px;background:#fff0f0;color:#900;'>"
            f"<h2>⚠️ Lỗi Khởi Động Trên Vercel</h2>"
            f"<pre style='background:#fff;padding:15px;border:1px solid #c00;border-radius:6px;overflow:auto;color:#333;font-size:14px;line-height:1.5;'>"
            f"{error_on_import}"
            f"</pre></body></html>",
            status_code=200
        )

handler = app
