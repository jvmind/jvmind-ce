from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI) -> None:
    _base = Path(__file__).resolve().parent.parent
    _fe = _base / "frontend" / "dist"
    if not _fe.exists():
        _fe = _base / "frontend"
    _fe_src = _base / "frontend"

    def _resolve_page(filename: str) -> Path:
        dist_file = _fe / filename
        if dist_file.exists():
            return dist_file
        src_file = _fe_src / filename
        if src_file.exists():
            return src_file
        raise HTTPException(404, f"{filename} not found")

    if _fe.exists():
        if (_fe / "assets").exists():
            app.mount("/assets", StaticFiles(directory=_fe / "assets"), name="assets")
            app.mount("/lib", StaticFiles(directory=_fe / "lib"), name="lib")
            app.mount("/image", StaticFiles(directory=_fe / "image"), name="image")
        else:
            app.mount("/static", StaticFiles(directory=_fe), name="static")

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