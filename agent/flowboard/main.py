import asyncio
import hmac
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware

from flowboard.config import CORS_ORIGIN_REGEX, WS_HOST
from flowboard.db import get_session, init_db
from flowboard.db.models import Request
from flowboard.routes import activity, affiliate, agent, auth, batch, boards, characters, chat, edges, estimate, flow_projects, grok, llm, media, models, nodes, plans, postprod, projects, prompt, settings, tab_state, templates, upload, vision
from flowboard.routes import references as references_route
from flowboard.routes import requests as requests_route
from flowboard.services.flow_client import flow_client
from flowboard.services.ws_server import run_ws_server
from flowboard.worker.processor import get_worker

# Guard rail: the dedicated WS server is unauthenticated and would expose the
# callback secret to any process that can reach it. Refuse to boot if someone
# overrode WS_HOST to a non-loopback address.
if WS_HOST not in ("127.0.0.1", "localhost", "::1"):
    raise RuntimeError(
        f"FLOWBOARD_WS_HOST must be loopback (got {WS_HOST!r}); the extension WS "
        "is unauthenticated by design and must not be network-reachable."
    )

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def redact(text: str) -> str:
    """Blank out anything that looks like a credential."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_replace, text)
    return text


def _replace(match: "re.Match[str]") -> str:
    # Group 1 is the label that identifies the secret (`?key=`, `Bearer `,
    # `"api_key": `). Keeping it makes the line still readable as a request.
    return f"{match.group(1)}<redacted>"


_SECRET_PATTERNS = (
    # Value-shaped patterns run FIRST: they match the secret itself, so a
    # label pattern cannot stop early at a space and leave the tail exposed.
    # `Authorization: Bearer ya29.AIza…` did exactly that — the label pattern
    # consumed only the word "Bearer".
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(AIza)[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(sk-)[A-Za-z0-9_\-]{16,}"),
    # Query parameter, the case this was written for: Google's credits
    # endpoint takes the API key in the URL and httpx logs the whole URL.
    re.compile(r"([?&](?:key|api_?key|access_?token|token)=)[^&\s\"'<>]+", re.I),
    # Header, in the shapes a logged header dict or a raw header line takes.
    re.compile(
        r"((?:x-goog-api-key|x-api-key|x-callback-secret|authorization)"
        r"['\"]?\s*[:=]\s*['\"]?)[^\s,'\"}\]]+",
        re.I,
    ),
    # JSON or kwargs body.
    re.compile(
        r"(['\"](?:api_?key|access_?token|secret|password)['\"]\s*:\s*['\"])[^'\"]+"
    ),
)


class _RedactSecrets(logging.Filter):
    """Strip credentials out of everything on its way to a log.

    httpx logs every request at INFO with the full URL, and Google's credits
    endpoint takes the API key as a query parameter — so the key was landing
    in the agent log in clear text on every startup. Redaction happens at the
    handler, not the call site, because the call site is a library.

    Both halves of a record are cleaned. Redacting only the formatted message
    leaves `exc_info` untouched, and a traceback prints the offending URL in
    full — one `logger.exception` around an httpx call is all it takes.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        cleaned = redact(text)
        if cleaned != text:
            # getMessage() already merged the args, so replace both halves or
            # the formatter would re-expand the originals.
            record.msg, record.args = cleaned, ()

        # A traceback is formatted by the Formatter from `exc_info`, which
        # never passes through here. Render it now, clean it, and hand the
        # result over as pre-formatted text so the Formatter reuses it.
        if record.exc_info and not record.exc_text:
            import traceback

            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RedactSecrets())


def _recover_orphan_running_requests() -> int:
    """Mark any pre-existing 'running' requests as failed so a restart doesn't
    leave nodes polling a request that nobody is processing anymore."""
    from datetime import datetime, timezone
    from sqlmodel import select as _select

    touched = 0
    with get_session() as s:
        rows = s.exec(_select(Request).where(Request.status == "running")).all()
        for r in rows:
            r.status = "failed"
            r.error = "agent_restart_lost"
            r.finished_at = datetime.now(timezone.utc)
            s.add(r)
            touched += 1
        if touched:
            s.commit()
    return touched


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recovered = _recover_orphan_running_requests()
    if recovered:
        logger.info("recovered %d orphan running request(s) → failed", recovered)
    worker = get_worker()
    ws_task = asyncio.create_task(run_ws_server(), name="ext-ws-server")
    worker_task = asyncio.create_task(worker.start(), name="request-worker")
    logger.info("flowboard agent started (ws:9223 + worker)")
    try:
        yield
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(worker.drain(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("worker drain timed out")
        for t in (ws_task, worker_task):
            t.cancel()
        await asyncio.gather(ws_task, worker_task, return_exceptions=True)
        logger.info("flowboard agent stopped")


app = FastAPI(title="Flowboard Agent", version="0.0.2", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    # No route reads cookies; the extension authenticates with an explicit
    # X-Callback-Secret header. Sending credentials cross-origin would only
    # widen what a hostile page could do with an ambient session.
    allow_credentials=False,
    # PUT is used by /api/llm/config and /api/llm/providers/{name} (and the
    # settings routes to come); omitting it silently breaks those saves from
    # the browser at the CORS preflight, not at the call site.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Callback-Secret"],
)

app.include_router(boards.router)
app.include_router(nodes.router)
app.include_router(edges.router)
app.include_router(chat.router)
app.include_router(projects.router)
app.include_router(flow_projects.router)
app.include_router(references_route.router)
app.include_router(requests_route.router)
app.include_router(media.bytes_router)
app.include_router(media.api_router)
app.include_router(upload.router)
app.include_router(plans.router)
app.include_router(vision.router)
app.include_router(prompt.router)
app.include_router(auth.router)
app.include_router(llm.router)
app.include_router(activity.router)
app.include_router(postprod.router)
app.include_router(postprod.bytes_router)
app.include_router(settings.router)
app.include_router(characters.router)
app.include_router(tab_state.router)
app.include_router(affiliate.router)
app.include_router(grok.router)
app.include_router(batch.router)
app.include_router(templates.router)
app.include_router(models.router)
app.include_router(estimate.router)
app.include_router(agent.router)


@app.get("/api/health")
def health() -> dict:
    from flowboard.worker.processor import get_worker

    # The 403 breaker stops all dispatch to protect the account. That has
    # to be visible: without it a stalled queue looks exactly like a slow
    # one — rows sit at 'queued' forever with nothing in the UI saying why.
    return {
        "ok": True,
        "extension_connected": flow_client.connected,
        "ws_stats": flow_client.ws_stats,
        "breaker": get_worker().breaker_state,
    }


@app.post("/api/ext/callback")
async def ext_callback(
    body: FastAPIRequest,
    x_callback_secret: str | None = Header(default=None, alias="X-Callback-Secret"),
) -> dict:
    """HTTP callback for the extension to deliver API responses."""
    if not x_callback_secret or not hmac.compare_digest(
        x_callback_secret, flow_client.callback_secret
    ):
        raise HTTPException(status_code=401, detail="invalid callback secret")

    try:
        payload = await body.json()
    except Exception:
        # `from None`: the parse error's own text is about byte offsets, which
        # tells the caller nothing they can act on.
        raise HTTPException(status_code=400, detail="invalid json body") from None

    if not isinstance(payload, dict) or "id" not in payload:
        raise HTTPException(status_code=400, detail="missing id")

    matched = flow_client.resolve_callback(payload)
    return {"ok": matched}
