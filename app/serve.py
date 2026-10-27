"""Render entry point. Proxy headers are trusted only inside Render's managed ingress."""

import os

import uvicorn

if __name__ == "__main__":
    on_render = os.getenv("RENDER") == "true"
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=on_render,
        forwarded_allow_ips="*" if on_render else "127.0.0.1",
        access_log=False,
    )
