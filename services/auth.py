from __future__ import annotations

import os
import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from aiohttp import web
from google_auth_oauthlib.flow import Flow
from ui.embeds import error_embed, success_embed, warning_embed

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

AUTH_SESSION_TIMEOUT = timedelta(minutes=15)
AUTH_TIMEOUT_MESSAGE = "Authentication timed out."
AUTH_INVALID_MESSAGE = "That authentication session is no longer valid."

@dataclass(slots=True)
class AuthSession:
    user_id: int
    state: str
    redirect_uri: str
    authorization_url: str
    future: asyncio.Future[AuthResult]
    interaction: object
    created_at: datetime
    flow: Flow
    timeout_task: asyncio.Task[None] | None = None

@dataclass(slots=True)
class AuthResult:
    success: bool
    message: str

class GoogleAuthService:
    def __init__(self, bot):
        self.bot = bot
        self._pending: dict[str, AuthSession] = {}
        self._completed: dict[str, AuthResult] = {}
        self._publish_tasks: set[asyncio.Task[None]] = set()
        self._timeout_tasks: set[asyncio.Task[None]] = set()
        self._app = None
        self._runner = None
        self._site = None

    @property
    def session_timeout_seconds(self) -> int:
        return int(AUTH_SESSION_TIMEOUT.total_seconds())

    def has_http_server(self) -> bool:
        return bool(self.bot.config.listen_address)

    def build_redirect_uri(self) -> str:
        if self.bot.config.public_base_url:
            parsed = urlparse(self.bot.config.public_base_url)
            if parsed.scheme and parsed.netloc:
                return self.bot.config.public_base_url.rstrip("/")
        if self.bot.config.listen_address:
            host, port = self._split_listen_address(self.bot.config.listen_address)
            return f"http://{host}:{port}/auth/callback"
        return "http://localhost:13371/auth/callback"

    async def start(self) -> None:
        if not self.bot.config.listen_address:
            return
        host, port = self._split_listen_address(self.bot.config.listen_address)
        self._app = web.Application()
        self._app.add_routes([web.get("/auth/callback", self._handle_callback), web.get("/", self._handle_index)])
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        self.bot.logger.info("Auth callback server listening on %s:%s", host, port)

    async def close(self) -> None:
        for state in list(self._pending):
            await self.timeout_session(state)

        tracked_tasks = [*self._timeout_tasks, *self._publish_tasks]
        if tracked_tasks:
            await asyncio.gather(*tracked_tasks, return_exceptions=True)

        if self._site:
            with contextlib.suppress(Exception):
                await self._site.stop()
        if self._runner:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
        self._site = None
        self._runner = None
        self._app = None
        self._completed.clear()
        self._pending.clear()

    def schedule_publish(self, session: AuthSession) -> None:
        task = asyncio.create_task(self.await_and_publish(session), name=f"youtify-auth-publish-{session.state}")
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    async def begin_auth(self, interaction) -> AuthSession:
        redirect_uri = self.build_redirect_uri()
        flow = Flow.from_client_secrets_file(
            self.bot.config.client_secrets_file,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )

        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )

        future: asyncio.Future[AuthResult] = asyncio.get_running_loop().create_future()
        session = AuthSession(
            user_id=interaction.user.id,
            state=state,
            redirect_uri=redirect_uri,
            authorization_url=authorization_url,
            future=future,
            interaction=interaction,
            created_at=datetime.now(timezone.utc),
            flow=flow,
        )

        self._pending[state] = session
        timeout_task = asyncio.create_task(
            self._expire_session_after_delay(state, self.session_timeout_seconds),
            name=f"youtify-auth-timeout-{state}",
        )
        session.timeout_task = timeout_task
        self._timeout_tasks.add(timeout_task)
        timeout_task.add_done_callback(self._timeout_tasks.discard)
        return session

    async def await_and_publish(self, session: AuthSession) -> None:
        try:
            result: AuthResult = await session.future
        except Exception as exc:
            result = AuthResult(success=False, message=str(exc))

        try:
            if result.success:
                await session.interaction.edit_original_response(
                    embed=success_embed("Google auth complete", result.message), view=None)
            elif result.message == AUTH_TIMEOUT_MESSAGE:
                await session.interaction.edit_original_response(
                    embed=warning_embed("Google auth timed out", result.message), view=None)
            else:
                await session.interaction.edit_original_response(
                    embed=error_embed("Google auth failed", result.message), view=None)
        except Exception:
            self.bot.logger.exception("Failed to edit auth response for %s", session.user_id)
        finally:
            self._completed.setdefault(session.state, result)
            self._pending.pop(session.state, None)

    async def complete_from_redirect_url(self, state: str, authorization_response: str) -> AuthResult:
        session = self._pending.get(state)
        if session is None:
            return self._completed.get(state, AuthResult(False, AUTH_INVALID_MESSAGE))

        flow = session.flow

        if session.future.done():
            return self._completed.get(state, session.future.result())
        try:
            # noinspection PyTypeChecker
            await asyncio.to_thread(flow.fetch_token, authorization_response=authorization_response)
            creds_json = flow.credentials.to_json()
            email = getattr(flow.credentials, "id_token", None)
            self.bot.db.set_auth_record(session.user_id, creds_json, email=email if isinstance(email, str) else None)
            result = AuthResult(True, "Your Google account is now linked.")
            self._finalize_session(session, result)
            return result
        except Exception as exc:
            message = f"{exc}"
            result = AuthResult(False, message)
            self._finalize_session(session, result)
            return result

    async def timeout_session(self, state: str) -> AuthResult:
        session = self._pending.get(state)
        if session is None:
            return self._completed.get(state, AuthResult(False, AUTH_INVALID_MESSAGE))

        result = AuthResult(False, AUTH_TIMEOUT_MESSAGE)
        self._finalize_session(session, result)
        return result

    async def _expire_session_after_delay(self, state: str, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._expire_session(state)

    async def _expire_session(self, state: str) -> AuthResult:
        session = self._pending.get(state)
        if session is None:
            return self._completed.get(state, AuthResult(False, AUTH_INVALID_MESSAGE))

        result = AuthResult(False, AUTH_TIMEOUT_MESSAGE)
        self._finalize_session(session, result, cancel_timeout_task=False)
        return result

    def _finalize_session(self, session: AuthSession, result: AuthResult, *, cancel_timeout_task: bool = True) -> None:
        if session.future.done():
            self._completed[session.state] = result
            self._pending.pop(session.state, None)
            return

        session.future.set_result(result)
        self._completed[session.state] = result
        self._pending.pop(session.state, None)
        if cancel_timeout_task and session.timeout_task is not None:
            session.timeout_task.cancel()

    @staticmethod
    async def _handle_index(_request: web.Request) -> web.Response:
        return web.Response(text="YouTify auth server is running.", content_type="text/plain")

    async def _handle_callback(self, request: web.Request) -> web.Response:
        state = request.query.get("state")
        if not state:
            return web.Response(text="Missing state.", status=400)

        auth_response = str(request.url)

        result = await self.complete_from_redirect_url(state, auth_response)
        if result.success:
            return web.Response(text="Authentication succeeded. You can close this page now.", content_type="text/plain")
        return web.Response(text=f"Authentication failed. Please try again. {result.message}", status=400, content_type="text/plain")

    @staticmethod
    async def _noop_edit(*_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _split_listen_address(value: str) -> tuple[str, int]:
        if ":" not in value:
            return value, 8080
        host, port = value.rsplit(":", 1)
        return host, int(port)