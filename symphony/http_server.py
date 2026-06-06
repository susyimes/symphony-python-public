from __future__ import annotations

from aiohttp import web

from .orchestrator import Orchestrator


class HttpStatusServer:
    def __init__(self, orchestrator: Orchestrator, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.bound_port: int | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/api/v1/state", self.state)
        app.router.add_get("/api/v1/{issue_identifier}", self.issue)
        app.router.add_post("/api/v1/refresh", self.refresh)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        sockets = getattr(self.site, "_server", None).sockets if getattr(self.site, "_server", None) else []
        if sockets:
            self.bound_port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def index(self, request: web.Request) -> web.Response:
        snapshot = self.orchestrator.snapshot()
        body = [
            "<!doctype html><meta charset='utf-8'><title>Symphony</title>",
            "<style>body{font-family:system-ui;margin:2rem;line-height:1.4}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.35rem .5rem}</style>",
            "<h1>Symphony</h1>",
            f"<p>Running: {snapshot['counts']['running']} | Retrying: {snapshot['counts']['retrying']}</p>",
            "<h2>Running</h2><table><tr><th>Issue</th><th>State</th><th>Session</th><th>Last event</th></tr>",
        ]
        for row in snapshot["running"]:
            body.append(
                f"<tr><td>{row['issue_identifier']}</td><td>{row['state']}</td><td>{row['session_id'] or ''}</td><td>{row['last_event'] or ''}</td></tr>"
            )
        body.append("</table><h2>Retrying</h2><table><tr><th>Issue</th><th>Attempt</th><th>Due</th><th>Error</th></tr>")
        for row in snapshot["retrying"]:
            body.append(f"<tr><td>{row['issue_identifier']}</td><td>{row['attempt']}</td><td>{row['due_at']}</td><td>{row['error'] or ''}</td></tr>")
        body.append("</table>")
        return web.Response(text="".join(body), content_type="text/html")

    async def state(self, request: web.Request) -> web.Response:
        return web.json_response(self.orchestrator.snapshot())

    async def issue(self, request: web.Request) -> web.Response:
        identifier = request.match_info["issue_identifier"]
        snapshot = self.orchestrator.issue_snapshot(identifier)
        if snapshot is None:
            return web.json_response({"error": {"code": "issue_not_found", "message": f"unknown issue: {identifier}"}}, status=404)
        return web.json_response(snapshot)

    async def refresh(self, request: web.Request) -> web.Response:
        self.orchestrator.request_tick()
        return web.json_response({"queued": True, "coalesced": False, "operations": ["poll", "reconcile"]}, status=202)
