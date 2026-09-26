"""Local web UI: queue labels (upload, phone share sheet, email, text), see the
sheet before it prints, print, and reprint from history.

Runs on the Mac (the Epson driver options only exist in its CUPS), bound to
localhost; `tailscale serve` makes it reachable from the phone.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import logging
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import core, mail, store
from .config import CONFIG
from .inputs import ACCEPT, is_supported, open_source

HERE = Path(__file__).parent / "web"
log = logging.getLogger("rmlabels.web")
MAIL_POLL_SECONDS = 300


async def poll_mail():
    while True:
        try:
            added = await asyncio.to_thread(mail.fetch_into_queue, store.connect())
            if added:
                log.info("queued from mail: %s", ", ".join(added))
        except Exception as e:  # keep polling through Bridge restarts etc.
            log.warning("mail check failed: %s", e)
        await asyncio.sleep(MAIL_POLL_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(poll_mail()) if CONFIG["mail"].get("user") else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="rmlabels", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Stop other websites open in your browser from POSTing here (e.g. to print).
    Browsers always send Origin on cross-site POSTs; the iOS Shortcut sends none."""
    origin = request.headers.get("origin")
    if request.method == "POST" and origin:
        hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
        if urlsplit(origin).netloc not in hosts:
            return Response("cross-origin request refused", status_code=403)
    return await call_next(request)


# ── Building sheets from the queue ────────────────────────────────────────────
def item_labels(item) -> list:
    if item["kind"] == "text":
        return [core.TextLabel(item["text"])]
    try:
        return list(core.extract_labels([open_source(item["name"], path=Path(item["path"]))]))
    except Exception as e:
        log.warning("can't read %s: %s", item["name"], e)
        return []


def png_uri(pix: pymupdf.Pixmap) -> str:
    return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()


def friendly(ts: str | None) -> str | None:
    return f"{dt.datetime.fromisoformat(ts):%a %d %b, %H:%M}" if ts else None


def build(db, start: int, fill: bool):
    """(sheet document, rows for the queue list, ids of items that yield labels, end position)."""
    rows, labels, ids = [], [], []
    for it in store.queue(db):
        found = item_labels(it)
        thumb = None
        if found and not isinstance(found[0], core.TextLabel):
            doc, pno, clip, _ = found[0]
            thumb = png_uri(doc[pno].get_pixmap(clip=clip, dpi=24))
        rows.append({"item": it, "count": len(found), "thumb": thumb,
                     "printed": friendly(store.last_printed(db, it["sha256"])) if it["sha256"] else None})
        labels += found
        if found:
            ids.append(it["id"])
    out = pymupdf.open()
    end = core.place(out, labels, start, fill, log=lambda *_: None) if labels else start
    return out, rows, ids, len(labels), end


def preview_images(out: pymupdf.Document, start: int) -> list[str]:
    """PNG previews with the label outlines drawn in, and labels already used
    on a part-used first sheet shaded. (Only the preview; never printed.)"""
    prev = pymupdf.open()
    prev.insert_pdf(out)
    images = []
    for n, page in enumerate(prev):
        for pos in range(4):
            edge = core.cell_rect(pos, pad=0)
            if n == 0 and pos < start:
                page.draw_rect(edge, color=None, fill=(0.85, 0.85, 0.85))
                page.insert_textbox(edge, "\n\n\n\n\nalready used", fontsize=16, color=(0.45, 0.45, 0.45), align=1)
            page.draw_rect(edge, color=(0.6, 0.6, 0.6), width=0.8, dashes="[4] 0")
        images.append(png_uri(page.get_pixmap(dpi=50)))
    return images


def panel(request: Request, start: int | None = None, fill: bool = False, mono: bool = True,
          draft: bool = False, message: str | None = None, error: bool = False):
    db = store.connect()
    start = core.read_next_position() if start is None else max(0, min(3, start))
    out, rows, ids, n, end = build(db, start, fill)
    return templates.TemplateResponse(request, "_panel.html", {
        "rows": rows, "n_labels": n, "n_sheets": len(out), "start": start, "fill": fill, "mono": mono,
        "draft": draft, "previews": preview_images(out, start) if n else [], "message": message, "error": error,
        "next_free": end % 4 + 1 if n and end % 4 else None, "saved_start": core.read_next_position(),
    })


def opts(start: str | None, fill, mono, draft):
    """Checkbox/radio form values → panel() keyword arguments."""
    return {"start": int(start) - 1 if start else None, "fill": bool(fill), "mono": bool(mono), "draft": bool(draft)}


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "accept": ACCEPT, "presets": CONFIG["presets"], "mail_on": bool(CONFIG["mail"].get("user")),
        "panel": panel(request).body.decode(),
    })


@app.get("/panel", response_class=HTMLResponse)
def get_panel(request: Request, start: str | None = None, fill: str | None = None,
              mono: str | None = None, draft: str | None = None):
    return panel(request, **opts(start, fill, mono, draft))


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, files: list[UploadFile], start: str | None = Form(None),
                 fill: str | None = Form(None), mono: str | None = Form(None), draft: str | None = Form(None)):
    added, skipped = await add_uploads(files, "upload")
    msg = f"Added {len(added)} file(s)." if added else "Nothing new added."
    if skipped:
        msg += " Skipped: " + ", ".join(skipped)
    return panel(request, **opts(start, fill, mono, draft), message=msg, error=bool(skipped) and not added)


async def add_uploads(files: list[UploadFile], source: str):
    db = store.connect()
    added, skipped = [], []
    for f in files:
        name = f.filename or "upload"
        if not is_supported(name):
            skipped.append(f"{name} (not a PDF or image)")
            continue
        _, new = store.add_file(db, name, await f.read(), source)
        (added if new else skipped).append(name if new else f"{name} (already queued)")
    return added, skipped


@app.post("/api/upload")
async def api_upload(file: list[UploadFile]):
    """For the iOS share-sheet Shortcut: multipart field `file`, one or more."""
    added, skipped = await add_uploads(file, "phone")
    return JSONResponse({"added": added, "skipped": skipped,
                         "summary": (f"Queued {', '.join(added)}" if added else "Nothing new") +
                                    (f" — skipped {', '.join(skipped)}" if skipped else "")})


@app.post("/text", response_class=HTMLResponse)
def add_text(request: Request, text: str = Form(""), preset: str | None = Form(None),
             start: str | None = Form(None), fill: str | None = Form(None),
             mono: str | None = Form(None), draft: str | None = Form(None)):
    body = CONFIG["presets"].get(preset, "") if preset else text
    if not body.strip():
        return panel(request, **opts(start, fill, mono, draft), message="Type some text first.", error=True)
    store.add_text(store.connect(), body, "upload")
    return panel(request, **opts(start, fill, mono, draft), message="Text label added.")


@app.post("/items/{item_id}/remove", response_class=HTMLResponse)
def remove(request: Request, item_id: int, start: str | None = Form(None), fill: str | None = Form(None),
           mono: str | None = Form(None), draft: str | None = Form(None)):
    store.remove(store.connect(), item_id)
    return panel(request, **opts(start, fill, mono, draft))


@app.post("/clear", response_class=HTMLResponse)
def clear(request: Request, start: str | None = Form(None), fill: str | None = Form(None),
          mono: str | None = Form(None), draft: str | None = Form(None)):
    store.clear(store.connect())
    return panel(request, **opts(start, fill, mono, draft), message="Queue cleared.")


@app.get("/sheet.pdf")
def sheet_pdf(start: str | None = None, fill: str | None = None):
    o = opts(start, fill, None, None)
    s = core.read_next_position() if o["start"] is None else o["start"]
    out, _, _, n, _ = build(store.connect(), s, o["fill"])
    if not n:
        return Response("The queue is empty.", status_code=404)
    return Response(out.tobytes(garbage=3, deflate=True), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="rm-labels-preview.pdf"'})


@app.post("/print", response_class=HTMLResponse)
def print_queue(request: Request, start: str | None = Form(None), fill: str | None = Form(None),
                mono: str | None = Form(None), draft: str | None = Form(None)):
    o = opts(start, fill, mono, draft)
    s = core.read_next_position() if o["start"] is None else o["start"]
    db = store.connect()
    out, _, ids, n, end = build(db, s, o["fill"])
    if not n:
        return panel(request, **o, message="Nothing to print.", error=True)
    dest = CONFIG["out_dir"] / f"rm-labels-{dt.datetime.now():%Y%m%d-%H%M%S}.pdf"
    core.save_sheet(out, dest)
    try:
        core.print_sheet(dest, mono=o["mono"], draft=o["draft"])
    except Exception as e:
        return panel(request, **o, message=f"Printing failed: {e}", error=True)
    core.write_next_position(end)
    store.record_job(db, ids, dest, s + 1, n, len(out), o["mono"])
    left = f"next free label is {end % 4 + 1}" if end % 4 else "that sheet is full"
    return panel(request, fill=o["fill"], mono=o["mono"], draft=o["draft"],
                 message=f"Sent {n} label(s) on {len(out)} sheet(s) to the printer; {left}.")


@app.post("/mail/check", response_class=HTMLResponse)
async def check_mail(request: Request, start: str | None = Form(None), fill: str | None = Form(None),
                     mono: str | None = Form(None), draft: str | None = Form(None)):
    try:
        added = await asyncio.to_thread(mail.fetch_into_queue, store.connect())
        msg, err = (f"Queued from mail: {', '.join(added)}" if added else "No new labels in mail."), False
    except Exception as e:
        msg, err = f"Mail check failed: {e}", True
    return panel(request, **opts(start, fill, mono, draft), message=msg, error=err)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    jobs = [(j, items, friendly(j["printed_at"])) for j, items in store.history(store.connect())]
    return templates.TemplateResponse(request, "history.html", {"jobs": jobs})


@app.get("/history/{job_id}/sheet.pdf")
def history_sheet(job_id: int):
    row = store.connect().execute("SELECT sheet FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row or not Path(row["sheet"]).exists():
        return Response("That sheet file is no longer there.", status_code=404)
    return FileResponse(row["sheet"], media_type="application/pdf", content_disposition_type="inline")


@app.post("/history/requeue")
async def requeue(request: Request):
    form = await request.form()
    store.requeue(store.connect(), [int(i) for i in form.getlist("item")])
    return RedirectResponse("/", status_code=303)


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Labels", "short_name": "Labels", "start_url": "/", "display": "standalone",
        "background_color": "#f6f7f9", "theme_color": "#f6f7f9",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")
