from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def _frontend_root() -> Path:
    """Locate the bundled frontend directory.

    Search order:
      1. ``<package>/frontend/dist``  (shipped with the wheel)
      2. ``<repo>/frontend/dist``     (editable install / dev)
      3. ``<repo>/frontend``          (source-only fallback)
    """
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        pkg_dir / "frontend" / "dist",
        pkg_dir.parent / "frontend" / "dist",
        pkg_dir.parent / "frontend",
        pkg_dir / "frontend",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "Frontend assets not found. If running from source, run "
        "`cd frontend && npm install && npm run build` first."
    )


def mount_frontend(app: FastAPI) -> None:
    _fe = _frontend_root()

    def _resolve_page(filename: str) -> Path:
        candidate = _fe / filename
        if candidate.exists():
            return candidate
        raise HTTPException(404, f"{filename} not found")

    if (_fe / "assets").exists():
        # Production build: assets under /assets, libs under /lib, images under /image
        app.mount("/assets", StaticFiles(directory=_fe / "assets"), name="assets")
        # Mount /src for CSS (style.css, bundled in dist/src/)
        src_dir = _fe / "src"
        if src_dir.exists():
            app.mount("/src", StaticFiles(directory=src_dir), name="src")
        lib_dir = _fe / "lib"
        if lib_dir.exists():
            app.mount("/lib", StaticFiles(directory=lib_dir), name="lib")
        image_dir = _fe / "image"
        if image_dir.exists():
            app.mount("/image", StaticFiles(directory=image_dir), name="image")
    else:
        # Source-only fallback (no build): serve the source tree.
        # Vite would normally rewrite /src/... imports; here we serve raw files
        # so that GET /src/main.js, /src/style.css, /image/* etc. all resolve.
        app.mount("/src", StaticFiles(directory=_fe / "src", html=True), name="src")
        for sub in ("css", "gc-analysis", "jstack-analysis", "heapdump-analysis", "test"):
            d = _fe / "src" / sub
            if d.exists():
                app.mount(f"/src/{sub}", StaticFiles(directory=d, html=True), name=f"src-{sub}")
        image_dir = _fe / "image"
        if image_dir.exists():
            app.mount("/image", StaticFiles(directory=image_dir), name="image")

    @app.get("/")
    @app.get("/app")
    @app.get("/app/")
    def home_page():
        return FileResponse(_fe / "index.html")

    @app.get("/report/{sid}/{rid}")
    def report_page(sid: str, rid: str):
        return FileResponse(_resolve_page("report.html"))

    @app.get("/jstack-report/{sid}/{rid}")
    def jstack_report_page(sid: str, rid: str):
        return FileResponse(_resolve_page("report.html"))

    @app.get("/heapdump-report/{rid}")
    def heapdump_report_page(rid: str):
        return FileResponse(_resolve_page("report.html"))