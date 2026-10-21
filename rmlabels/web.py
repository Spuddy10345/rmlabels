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
import hashlib
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
from fastapi import Depends, FastAPI, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import core, mail, store
from .config import CONFIG
from .inputs import ACCEPT, is_supported, open_source

HERE = Path(__file__).parent / "web"
log = logging.getLogger("rmlabels.web")
MAIL_POLL_SECONDS = 300
HISTORY_PAGE = 20
JOB_STATE_LABEL = {"sent": "Sent", "queued": "Sent", "held": "Held", "printing": "Printing", "stopped": "Needs attention",
                   "done": "Finished", "failed": "Failed", "cancelled": "Cancelled", "undone": "Undone"}
PREFS = {"mono": True, "fill": False, "draft": False}  # print options a fresh page starts with
MAX_UPLOAD = 25 * 1024 * 1024    # one file; a label is a few hundred KB, a phone photo a few MB
MAX_BODY = 100 * 1024 * 1024     # a whole upload request
HOSTS = ["127.0.0.1", "localhost", "*.ts.net"]  # what the app answers to (blocks DNS-rebinding); config adds more
ENGINE = threading.RLock()      # pymupdf isn't thread-safe: hold this for anything that opens or draws a document
PRINT_LOCK = threading.Lock()   # one print (or change to the saved sheet) at a time; slow CUPS calls run under this, not ENGINE


def get_db():
    """One SQLite connection per request, closed afterwards."""
    with store.session() as db:
        yield db


def fetch_mail() -> list[str]:
    """Runs in a worker thread, so it opens (and closes) its own SQLite connection."""
    with store.session() as db:
        return mail.fetch_into_queue(db)


async def poll_mail():
    while True:
        try:
            added = await asyncio.to_thread(fetch_mail)
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
app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS + list(CONFIG["allowed_hosts"]))
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def error_response(request: Request, status: int, message: str):
    """One shape for every failure: JSON for the phone Shortcut, a toast fragment for htmx, a page otherwise."""
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": message, "summary": message}, status_code=status)
    if request.headers.get("hx-request"):
        body = templates.env.get_template("_flash.html").render(message=message, error=True)
        return HTMLResponse(body, status_code=status, headers={"HX-Retarget": "#toast", "HX-Reswap": "innerHTML"})
    return templates.TemplateResponse(request, "error.html", {"status": status, "message": message}, status_code=status)


@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException):
    return error_response(request, exc.status_code, "Not found." if exc.status_code == 404 else str(exc.detail))


@app.exception_handler(RequestValidationError)
def invalid_request(request: Request, exc: RequestValidationError):
    return error_response(request, 422, "That request wasn't valid.")


@app.exception_handler(Exception)
def server_error(request: Request, exc: Exception):
    log.error("%s %s failed", request.method, request.url.path, exc_info=exc)
    return error_response(request, 500, "Something went wrong on the server. If you were printing, check History and the printer before trying again.")


class BodyLimit:
    """Refuse a request body over MAX_BODY whether it announces its size, gets it wrong, or doesn't say (chunked).
    Starlette writes a whole upload to disk before any handler runs, so the limit has to be enforced as it streams in."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and not declared.isdigit():
            return await error_response(Request(scope), 400, "That request was malformed.")(scope, receive, send)
        if declared and int(declared) > MAX_BODY:
            return await too_big(Request(scope))(scope, receive, send)
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY:  # an HTTPException passes through FastAPI's body parsing; other errors become a 400
                    raise StarletteHTTPException(413, too_big_text())
            return message
        await self.app(scope, limited, send)


def too_big_text() -> str:
    return f"That upload is too big (the limit is {MAX_BODY // 2**20} MB)."


def too_big(request: Request):
    return error_response(request, 413, too_big_text())


app.add_middleware(BodyLimit)


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Stop other websites open in your browser from POSTing here (e.g. to print).
    Browsers always send Origin on cross-site POSTs; the iOS Shortcut sends none."""
    if request.method == "POST":
        origin = request.headers.get("origin")
        hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
        if (origin and urlsplit(origin).netloc not in hosts) or request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(request, 403, "Request refused: it came from a different site.")
    return await call_next(request)


# ── Building sheets from the queue ────────────────────────────────────────────
# Everything below is cached because detecting labels, placing them and drawing previews is the slow part,
# and a panel is rebuilt on every checkbox, every 15 s refresh and again when you print. Items never change
# once queued and their files are named by content hash, so an item id is a safe key. All of it is used under
# ENGINE only (pymupdf isn't thread-safe, and these documents are shared between requests).
_items: dict[int, tuple[list, str | None]] = {}     # item id → (labels, thumbnail)
_sheets: dict[tuple, pymupdf.Document] = {}          # (queued ids, used labels, fill) → placed sheets
_previews: dict[tuple, bytes] = {}                  # (sheet key, page, dpi) → PNG
KEEP_SHEETS, KEEP_PREVIEWS = 2, 16


def item_labels(item) -> list:
    if item["kind"] == "text":
        return [core.TextLabel(item["text"])]
    try:
        return list(core.extract_labels([open_source(item["name"], path=Path(item["path"]))]))
    except Exception as e:
        log.warning("can't read %s: %s", item["name"], e)
        return []


def item_entry(item) -> tuple[list, str | None]:
    if item["id"] not in _items:
        found = item_labels(item)
        thumb = None
        if found and not isinstance(found[0], core.TextLabel):
            doc, pno, clip, _ = found[0]
            thumb = png_uri(doc[pno].get_pixmap(clip=clip, dpi=24))
        _items[item["id"]] = found, thumb
    return _items[item["id"]]


def png_uri(pix: pymupdf.Pixmap) -> str:
    return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()


def friendly(ts: str | None) -> str | None:
    return f"{dt.datetime.fromisoformat(ts):%a %d %b, %H:%M}" if ts else None


def place_queue(db, mask: int, fill: bool, queued=None):
    """(sheet document, ids of items that yield labels, label count) for a sheet whose labels in `mask` are already
    used. Call with ENGINE held: the document is cached and shared, so treat it as read-only."""
    queued = store.queue(db) if queued is None else queued
    for gone in set(_items) - {it["id"] for it in queued}:
        del _items[gone]
    labels, ids = [], []
    for it in queued:
        found = item_entry(it)[0]
        labels += found
        if found:
            ids.append(it["id"])
    key = sheet_key(queued, mask, fill)
    if key not in _sheets:
        out = pymupdf.open()
        core.place(out, labels, 0, fill, log=lambda *_: None, skip=used_slots(mask))
        _sheets[key] = out
        while len(_sheets) > KEEP_SHEETS:
            oldest = next(iter(_sheets))
            _sheets.pop(oldest).close()
            for k in [k for k in _previews if k[0] == oldest]:
                del _previews[k]
    out = _sheets[key] = _sheets.pop(key)  # most recently used goes last
    return out, ids, len(labels)


def build(db, mask: int, fill: bool, queued=None):
    """place_queue() plus the rows for the queue list: (sheets, rows, ids, label count)."""
    queued = store.queue(db) if queued is None else queued
    out, ids, n = place_queue(db, mask, fill, queued)
    printed = store.last_printed_many(db, [it["sha256"] for it in queued if it["sha256"]])  # one query, not one per item
    rows = [{"item": it, "count": len(item_entry(it)[0]), "thumb": item_entry(it)[1],
             "printed": friendly(printed.get(it["sha256"]))} for it in queued]
    return out, rows, ids, n


def sheet_key(queued, mask: int, fill: bool) -> tuple:
    return tuple(it["id"] for it in queued), mask, fill


def used_slots(mask: int) -> set[int]:
    return {i for i in range(4) if mask >> i & 1}


def used_words(mask: int) -> str:
    """'label 1', 'labels 1 and 3', 'labels 1, 2 and 4'."""
    used = [str(i + 1) for i in sorted(used_slots(mask))]
    return f"label {used[0]}" if len(used) == 1 else "labels " + ", ".join(used[:-1]) + " and " + used[-1]


def preview_png(db, mask: int, fill: bool, page_no: int, dpi: int) -> bytes | None:
    """PNG of one sheet with the label outlines drawn in, and labels already used on a part-used first sheet
    shaded. (Only the preview; never printed.) None if there is no such sheet."""
    queued = store.queue(db)
    key = (sheet_key(queued, mask, fill), page_no, dpi)
    if key not in _previews:
        out = place_queue(db, mask, fill, queued)[0]
        if not 0 <= page_no < len(out):
            return None
        prev = pymupdf.open()
        prev.insert_pdf(out, from_page=page_no, to_page=page_no)
        page = prev[0]
        for pos in range(4):
            edge = core.cell_rect(pos, pad=0)
            if page_no == 0 and mask >> pos & 1:
                page.draw_rect(edge, color=None, fill=(0.85, 0.85, 0.85))
                page.insert_textbox(edge, "\n\n\n\n\nused", fontsize=16, color=(0.45, 0.45, 0.45), align=1)
            page.draw_rect(edge, color=(0.6, 0.6, 0.6), width=0.8, dashes="[4] 0")
        _previews[key] = page.get_pixmap(dpi=dpi).tobytes("png")
        prev.close()
        while len(_previews) > KEEP_PREVIEWS:
            del _previews[next(iter(_previews))]
    return _previews[key]


JOB_TEXT = {  # state → (headline, detail). "done" is worded carefully: CUPS finishing is not paper in the tray.
    "sent": ("Sent to the printer", "Waiting for CUPS to report back."),
    "queued": ("Waiting in the printer queue", ""),
    "held": ("The job is on hold", "Release it in the printer queue, or undo and print again."),
    "printing": ("Printing…", ""),
    "stopped": ("The printer needs attention", "The job will carry on once that's sorted."),
    "done": ("Finished spooling", "CUPS reports the job complete. If nothing came out, check the tray and the printer's display."),
    "failed": ("The print failed", "CUPS aborted the job. Undo puts the labels back in the queue."),
    "cancelled": ("The print was cancelled", "Undo puts the labels back in the queue."),
    "undone": ("Print undone", "Those labels are back in the queue."),
}
CHIP_POLLS = 90        # × 2 s ≈ 3 minutes of watching a job
CHIP_RECENT = dt.timedelta(minutes=10)


def job_context(db, job, n: int = 0) -> dict:
    """What the status chip shows for a print job. Asks CUPS unless the job has already finished."""
    state, problem = job["state"] or "sent", ""
    watching = bool(job["cups_job"]) and state not in core.FINAL_STATES | {"undone"}
    if watching:
        seen, problem = core.job_status(job["cups_job"])
        if seen != "unknown":  # a status hiccup says nothing about the print itself
            state = seen
            if state != job["state"]:
                store.set_job_state(db, job["id"], state)
    poll = watching and state not in core.FINAL_STATES and n < CHIP_POLLS
    headline, detail = JOB_TEXT.get(state, JOB_TEXT["sent"])
    if watching and not poll and state not in core.FINAL_STATES:
        detail = "No confirmation from the printer after about 3 minutes. Check the tray and the printer's display."
    return {"id": job["id"], "state": state, "headline": headline, "detail": detail, "problem": problem,
            "labels": job["labels"], "when": friendly(job["printed_at"]), "poll": poll, "next": n + 1,
            "undo": state in ("failed", "cancelled"), "n_items": len(store.job_items(db, job["id"]))}


def recent_job_chip(db) -> dict | None:
    job = store.latest_job(db)
    if job and dt.datetime.now() - dt.datetime.fromisoformat(job["printed_at"]) < CHIP_RECENT:
        return job_context(db, job)


def token(db, queued=None) -> str:
    """Identifies what Print would act on (the queued items and the saved sheet), so a stale tab can't print something else."""
    ids = ",".join(str(r["id"]) for r in (store.queue(db) if queued is None else queued))
    return hashlib.sha1(f"{ids}|{core.read_used_mask()}".encode()).hexdigest()[:10]


def panel(request: Request, mask: int | None = None, fill: bool = False, mono: bool = True,
          draft: bool = False, message: str | None = None, error: bool = False, status: int = 200,
          chip: dict | None = None, undo: list[int] | None = None, base: int | None = None):
    """The queue, the sheet map and the preview. `mask` is which labels are used; None means the saved sheet.
    `base` is the saved sheet the page's map was built from: if the saved sheet has moved on since (another tab
    or the CLI printed), the page's map is replaced with the saved one, so a stale map can't be printed."""
    with ENGINE, store.session() as db:
        saved = core.read_used_mask()
        if base is not None and base != saved:
            if mask is not None and mask != base:
                note = "The sheet in the printer changed elsewhere, so your label ticks were reset. Check the sheet below."
                message, error = (f"{message} {note}" if message else note), True
            mask = saved
        mask = saved if mask is None else mask
        queued = store.queue(db)
        out, rows, ids, n = build(db, mask, fill, queued)
        free = core.free_slots(mask)
        fills = (free or [0, 1, 2, 3])[:n]  # a sheet with no free label means the print starts on a fresh one
        after = core.used_after(mask, n) if n else mask
        slots = [{"n": i + 1, "used": bool(mask >> i & 1), "prints": i in fills} for i in range(4)]
        return templates.TemplateResponse(request, "_panel.html", {
            "rows": rows, "n_labels": n, "n_sheets": len(out), "slots": slots, "fill": fill, "mono": mono, "draft": draft,
            "first": (free or [0])[0] + 1, "next_free": (core.free_slots(after) or [0])[0] + 1 if n and after else None,
            "mask": mask, "saved_mask": saved, "base": saved, "saved_text": f"{used_words(saved)} used" if saved else "fresh sheet",
            "used_qs": "used_set=1" + "".join(f"&used={i + 1}" for i in sorted(used_slots(mask))),
            "sheets": len(out) if n else 0, "message": message, "error": error,
            "version": token(db, queued), "chip": chip, "undo": ",".join(map(str, undo or [])),
        }, status_code=status)


def opts(start: str | None, fill, mono, draft, used=(), used_set: str | None = None, base: str | None = None):
    """The controls form (or query string) → panel() keyword arguments. `mask` stays None unless the page said
    which labels are used, either as ticked slots or as the older 'start at label N'."""
    mask = None
    if used_set is not None:
        mask = sum(1 << (int(n) - 1) for n in used if n.isdigit() and 1 <= int(n) <= 4)
    elif start:
        try:
            mask = core.mask_from_start(max(0, min(3, int(start) - 1)))
        except ValueError:
            pass
    if mask == core.FULL:  # every label used = a spent sheet = the next one is fresh (as when a print fills a sheet)
        mask = 0
    base_mask = int(base) & core.FULL if base and base.isdigit() else None
    return {"mask": mask, "fill": bool(fill), "mono": bool(mono), "draft": bool(draft), "base": base_mask}


def controls_form(start: str | None = Form(None), fill: str | None = Form(None), mono: str | None = Form(None),
                  draft: str | None = Form(None), used: list[str] = Form([]), used_set: str | None = Form(None),
                  base: str | None = Form(None)) -> dict:
    return opts(start, fill, mono, draft, used, used_set, base)


def controls_query(start: str | None = None, fill: str | None = None, mono: str | None = None,
                   draft: str | None = None, used: list[str] = Query([]), used_set: str | None = None,
                   base: str | None = None) -> dict:
    return opts(start, fill, mono, draft, used, used_set, base)


# ── Pages ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "index.html", {
        "accept": ACCEPT, "presets": CONFIG["presets"], "mail_on": bool(CONFIG["mail"].get("user")),
        "panel": panel(request, **{**PREFS, **store.get_setting(db, "prefs", {})}).body.decode(), "chip": recent_job_chip(db),
    })


@app.get("/panel", response_class=HTMLResponse)
def get_panel(request: Request, o: dict = Depends(controls_query)):
    return panel(request, **o)


@app.get("/panel/changed")
def panel_changed(request: Request, v: str | None = None, o: dict = Depends(controls_query),
                  db: sqlite3.Connection = Depends(get_db)):
    """Polled by open tabs: 204 (no swap) unless the queue changed behind their back (phone, mail, another tab)."""
    if v and v == token(db):
        return Response(status_code=204)
    return panel(request, **o)


@app.post("/upload", response_class=HTMLResponse)
def upload(request: Request, files: list[UploadFile] = [], o: dict = Depends(controls_form),
           db: sqlite3.Connection = Depends(get_db)):
    files = [f for f in files if f.filename]
    if not files:
        return panel(request, **o, message="Choose a file first.", error=True)
    added, skipped = add_uploads(db, files, "upload")
    msg = f"Added {len(added)} file(s)." if added else "Nothing new added."
    if skipped:
        msg += " Skipped: " + ", ".join(skipped)
    return panel(request, **o, message=msg, error=bool(skipped) and not added)


def add_uploads(db, files: list[UploadFile], source: str):
    added, skipped = [], []
    for f in files:
        name = f.filename or "upload"
        if not is_supported(name):
            skipped.append(f"{name} (not a PDF or image)")
            continue
        data = f.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            skipped.append(f"{name} (over the {MAX_UPLOAD // 2**20} MB limit)")
            continue
        try:  # refuse what can never become a label now, with the reason, rather than queueing a dud
            with ENGINE:
                open_source(name, data=data)[1].close()
        except ValueError as e:
            skipped.append(f"{name} ({e})")
            continue
        _, new = store.add_file(db, name, data, source)
        (added if new else skipped).append(name if new else f"{name} (already queued)")
    return added, skipped


@app.post("/api/upload")
def api_upload(request: Request, file: list[UploadFile] = [], db: sqlite3.Connection = Depends(get_db)):
    """For the iOS share-sheet Shortcut: multipart field `file`, one or more."""
    token = CONFIG["api_token"]
    if token and not secrets.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()):
        return error_response(request, 401, "Missing or wrong API token.")
    file = [f for f in file if f.filename]
    if not file:
        return error_response(request, 400, "No file received.")
    added, skipped = add_uploads(db, file, "phone")
    return JSONResponse({"added": added, "skipped": skipped,
                         "summary": (f"Queued {', '.join(added)}" if added else "Nothing new") +
                                    (f" — skipped {', '.join(skipped)}" if skipped else "")})


@app.post("/text", response_class=HTMLResponse)
def add_text(request: Request, text: str = Form(""), preset: str | None = Form(None),
             o: dict = Depends(controls_form), db: sqlite3.Connection = Depends(get_db)):
    body = CONFIG["presets"].get(preset, "") if preset else text
    if not body.strip():
        return panel(request, **o, message="Type some text first.", error=True)
    store.add_text(db, body, "upload")
    reply = panel(request, **o, message="Text label added.")
    if not preset:  # tells the page to clear the box it came from; a preset button must leave typed text alone
        reply.headers["HX-Trigger"] = "text-added"
    return reply


@app.post("/items/{item_id}/remove", response_class=HTMLResponse)
def remove(request: Request, item_id: int, o: dict = Depends(controls_form), db: sqlite3.Connection = Depends(get_db)):
    item = store.get_item(db, item_id)
    gone = store.remove(db, item_id)
    if not gone:
        return panel(request, **o)
    name = (item["text"] if item["kind"] == "text" else item["name"]).strip().splitlines()[0][:40]
    return panel(request, **o, message=f"Removed {name}.", undo=gone)


@app.post("/clear", response_class=HTMLResponse)
def clear(request: Request, o: dict = Depends(controls_form), db: sqlite3.Connection = Depends(get_db)):
    gone = store.clear(db)
    return panel(request, **o, message="Queue cleared.", undo=gone)


@app.post("/items/restore", response_class=HTMLResponse)
def restore(request: Request, ids: str = Form(""), o: dict = Depends(controls_form), db: sqlite3.Connection = Depends(get_db)):
    """Undo a remove or Clear."""
    n = store.restore(db, [int(i) for i in ids.split(",") if i.strip().isdigit()])
    if not n:
        return panel(request, **o, message="Nothing to bring back: it was printed or queued again since.", error=True)
    return panel(request, **o, message=f"Put {n} item{'s' if n != 1 else ''} back in the queue.")


@app.get("/preview/{page_no}.png")
def preview(page_no: int, dpi: int = 50, o: dict = Depends(controls_query), db: sqlite3.Connection = Depends(get_db)):
    with ENGINE:
        mask = core.read_used_mask() if o["mask"] is None else o["mask"]
        png = preview_png(db, mask, o["fill"], page_no, max(30, min(200, dpi)))
    if png is None:
        raise StarletteHTTPException(404)
    # the URL carries the queue version, so a changed queue is a new URL and this can be kept
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/sheet.pdf")
def sheet_pdf(o: dict = Depends(controls_query), db: sqlite3.Connection = Depends(get_db)):
    with ENGINE:
        mask = core.read_used_mask() if o["mask"] is None else o["mask"]
        out, _, n = place_queue(db, mask, o["fill"])
        if not n:
            return Response("The queue is empty.", status_code=404)
        pdf = out.tobytes(garbage=3, deflate=True)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="rm-labels-preview.pdf"'})


@app.post("/print", response_class=HTMLResponse)
def print_queue(request: Request, v: str | None = Form(None), o: dict = Depends(controls_form),
                db: sqlite3.Connection = Depends(get_db)):
    with PRINT_LOCK:  # nothing else may print, or change the saved sheet, between the check below and the record
        with ENGINE:  # pymupdf: place the sheet and save it. Released before CUPS, which can be slow
            if (v and v != token(db)) or (o["base"] is not None and o["base"] != core.read_used_mask()):
                return panel(request, **o, status=409, error=True,
                             message="The queue changed since you looked, so nothing was printed. Check the sheet below, then print again.")
            mask = core.read_used_mask() if o["mask"] is None else o["mask"]
            out, ids, n = place_queue(db, mask, o["fill"])
            if not n:
                return panel(request, **o, message="Nothing to print.", error=True)
            sheets = len(out)
            dest = CONFIG["out_dir"] / f"rm-labels-{dt.datetime.now():%Y%m%d-%H%M%S}.pdf"
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                core.save_sheet(out, dest)
            except OSError as e:
                return panel(request, **o, message=f"Couldn't save the sheet, so nothing was printed: {e}", error=True, status=502)
        try:
            cups_job = core.print_sheet(dest, mono=o["mono"], draft=o["draft"])
        except Exception as e:
            return panel(request, **o, message=f"Printing failed: {e}", error=True, status=502)
        after = core.used_after(mask, n)
        first = (core.free_slots(mask) or [0])[0] + 1
        log.info("print: %d label(s), %d sheet(s), from label %d (used %s), items %s, cups job %s",
                 n, sheets, first, bin(mask), ids, cups_job)
        try:
            core.write_used_mask(after)
            job_id = store.record_job(db, ids, dest, first, n, sheets, o["mono"], cups_job=cups_job, used_before=mask)
        except Exception as e:
            log.exception("sent to CUPS (job %s) but couldn't record it", cups_job)
            return panel(request, **o, status=500, error=True,
                         message=f"It was sent to the printer, but I couldn't save that ({e}). The labels are still in the queue, "
                                 "so remove them before printing again or they will print twice.")
    # It has printed and is recorded. Whatever fails from here must not look like a failed print.
    try:
        store.set_setting(db, "prefs", {k: o[k] for k in PREFS})  # the next visit, on any device, starts with these
    except Exception:
        log.exception("couldn't remember the print options")
    chip = None
    try:
        chip = job_context(db, store.get_job(db, job_id))
    except Exception:
        log.exception("couldn't read the status of CUPS job %s", cups_job)
    left = f"next free label is {core.free_slots(after)[0] + 1}" if after else "that sheet is full"
    return panel(request, fill=o["fill"], mono=o["mono"], draft=o["draft"], chip=chip,
                 message=f"Sent {n} label(s) on {sheets} sheet(s) to the printer; {left}.")


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
def job_status_chip(request: Request, job_id: int, n: int = 0, db: sqlite3.Connection = Depends(get_db)):
    job = store.get_job(db, job_id)
    if not job:
        raise StarletteHTTPException(404)
    return templates.TemplateResponse(request, "_job.html", {"c": job_context(db, job, n)})


@app.post("/jobs/{job_id}/undo", response_class=HTMLResponse)
def undo_job(request: Request, job_id: int, o: dict = Depends(controls_form), db: sqlite3.Connection = Depends(get_db)):
    """A print CUPS gave up on: put its labels back in the queue, and the saved position back where it was
    (only if nothing has printed since, otherwise the position would be wrong)."""
    o = {**o, "mask": None}  # show the saved sheet afterwards, not whatever the form had ticked
    with PRINT_LOCK:
        job = store.get_job(db, job_id)
        if not job:
            raise StarletteHTTPException(404)
        if job["state"] not in ("failed", "cancelled"):
            return panel(request, **o, status=409, error=True, message="Only a failed or cancelled print can be undone.")
        items = store.job_items(db, job_id)
        n = store.requeue(db, items)
        store.mark_undone(db, job_id)  # those items weren't printed, so they must not count as "printed before"
        note = ""
        before = job["used_before"] if job["used_before"] is not None else core.mask_from_start(job["start"] - 1)
        if store.latest_job(db)["id"] == job_id and core.read_used_mask() == core.used_after(before, job["labels"]):
            core.write_used_mask(before)
            note = f" The next free label is back to {(core.free_slots(before) or [0])[0] + 1}."
        if n < len(items):
            note += f" {len(items) - n} already in the queue again, so not added twice."
        store.set_job_state(db, job_id, "undone")
    return panel(request, **o, chip=job_context(db, store.get_job(db, job_id)),
                 message=f"Put {n} item(s) back in the queue.{note}")


@app.post("/position", response_class=HTMLResponse)
def set_position(request: Request, o: dict = Depends(controls_form)):
    """Correct which labels the saved sheet has used (after a jam, or when a different sheet is loaded)."""
    mask = o.pop("mask")
    if mask is None:
        return panel(request, **o, message="Pick a label first.", error=True)
    with PRINT_LOCK:
        core.write_used_mask(mask)
        saved = core.read_used_mask()
    if not saved:
        return panel(request, **o, message="Saved: the sheet in the printer is fresh, all four labels free.")
    return panel(request, **o, message=f"Saved: {used_words(saved)} {'is' if len(used_slots(saved)) == 1 else 'are'} used, "
                                       f"and label {core.free_slots(saved)[0] + 1} is the next free one.")


_printer: tuple[float, tuple[str, str]] = (0.0, ("unknown", ""))


@app.get("/printer", response_class=HTMLResponse)
def printer_line(request: Request):
    """One line about the printer, polled by the page. Cached briefly: several tabs shouldn't each ask CUPS."""
    global _printer
    if time.monotonic() - _printer[0] > 20:
        _printer = (time.monotonic(), core.printer_status())
    state, problem = _printer[1]
    return templates.TemplateResponse(request, "_printer.html", {"state": state, "problem": problem})


@app.post("/grid", response_class=HTMLResponse)
def print_grid(request: Request, o: dict = Depends(controls_form)):
    """The calibration grid (for checking the nudge settings against a real sheet). Not a print job in History."""
    dest = CONFIG["out_dir"] / "rm-labels-grid.pdf"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with ENGINE:
            out = pymupdf.open()
            core.test_grid(out)
            core.save_sheet(out, dest)
            out.close()
        cups_job = core.print_sheet(dest, mono=o["mono"], draft=o["draft"])
    except Exception as e:
        return panel(request, **o, message=f"The test grid didn't print: {e}", error=True, status=502)
    return panel(request, **o, message="Sent the test grid to the printer" + (f" (CUPS job {cups_job})" if cups_job else "")
                 + ". Use plain paper: it isn't meant for a label sheet.")


@app.post("/mail/check", response_class=HTMLResponse)
def check_mail(request: Request, o: dict = Depends(controls_form)):
    try:
        added = fetch_mail()
        msg, err = (f"Queued from mail: {', '.join(added)}" if added else "No new labels in mail."), False
    except Exception as e:
        msg, err = f"Mail check failed: {e}", True
    return panel(request, **o, message=msg, error=err)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, before: int | None = None, db: sqlite3.Connection = Depends(get_db)):
    page = store.history(db, HISTORY_PAGE + 1, before)  # one extra, to know whether there are older prints
    jobs = [(j, items, friendly(j["printed_at"]), JOB_STATE_LABEL.get(j["state"] or "sent", "Sent")) for j, items in page[:HISTORY_PAGE]]
    return templates.TemplateResponse(request, "history.html", {
        "jobs": jobs, "older": page[HISTORY_PAGE - 1][0]["id"] if len(page) > HISTORY_PAGE else None})


@app.get("/history/{job_id}/sheet.pdf")
def history_sheet(request: Request, job_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT sheet FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row or not Path(row["sheet"]).exists():
        return error_response(request, 404, "That sheet file is no longer there.")
    return FileResponse(row["sheet"], media_type="application/pdf", content_disposition_type="inline")


@app.post("/history/{job_id}/delete", response_class=HTMLResponse)
def delete_job(job_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Forget one print; the page drops its card."""
    store.delete_job(db, job_id)
    return HTMLResponse("")


@app.post("/history/requeue")
def requeue(item: list[str] = Form([]), db: sqlite3.Connection = Depends(get_db)):
    store.requeue(db, [int(i) for i in item if i.isdigit()])
    return RedirectResponse("/", status_code=303)


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "id": "/", "scope": "/", "name": "Labels", "short_name": "Labels", "start_url": "/", "display": "standalone",
        "description": "Queue shipping labels and print them four to a sheet.",
        "background_color": "#f6f7f9", "theme_color": "#f6f7f9",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")
