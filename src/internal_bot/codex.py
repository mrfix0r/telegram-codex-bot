from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Callable, Protocol

from .storage import CodexSessionRecord, Storage


LOG = logging.getLogger(__name__)


class CodexError(RuntimeError):
    pass


class CodexBusyError(CodexError):
    pass


def resolve_codex_executable(configured: str) -> str | None:
    """Находит CLI в PATH или внутри локальной установки Codex Desktop."""
    executable = shutil.which(configured)
    if executable is not None:
        return executable

    configured_path = Path(configured).expanduser()
    if configured_path.is_file():
        return str(configured_path.resolve())

    if os.name != "nt" or configured.lower() not in {"codex", "codex.exe"}:
        return None

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    bin_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    candidates = [
        path
        for path in bin_dir.glob("*/codex.exe")
        if path.is_file()
    ]
    direct_executable = bin_dir / "codex.exe"
    if direct_executable.is_file():
        candidates.append(direct_executable)
    if not candidates:
        return None

    def modified_at(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    selected = max(candidates, key=modified_at).resolve()
    LOG.info("Codex CLI найден автоматически: %s", selected)
    return str(selected)


class RpcTransport(Protocol):
    def set_handler(self, handler: Callable[[dict[str, Any]], None]) -> None: ...

    def start(self) -> None: ...

    def request(
        self, method: str, params: dict[str, Any], timeout: int | None = None
    ) -> Any: ...

    def respond(self, request_id: Any, result: dict[str, Any]) -> None: ...

    def respond_error(self, request_id: Any, code: int, message: str) -> None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _PendingResponse:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: dict[str, Any] | None = None


class CodexAppServerClient:
    """Минимальный потокобезопасный JSONL-клиент Codex App Server."""

    def __init__(self, executable: str, cwd: Path, request_timeout: int = 30) -> None:
        self.executable = executable
        self.cwd = cwd
        self.request_timeout = request_timeout
        self._handler: Callable[[dict[str, Any]], None] = lambda message: None
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int, _PendingResponse] = {}
        self._next_id = 1
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._start_lock = threading.RLock()
        self._closing = False

    def set_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._handler = handler

    def start(self) -> None:
        with self._start_lock:
            process = self._process
            if process is not None and process.poll() is None:
                return

            executable = resolve_codex_executable(self.executable)
            if executable is None:
                raise CodexError(
                    f"Команда {self.executable!r} не найдена. Установите Codex CLI "
                    "или задайте полный путь в BOT_CODEX_EXECUTABLE."
                )

            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(
                    [executable, "app-server", "--stdio"],
                    cwd=self.cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    shell=False,
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise CodexError(f"Не удалось запустить Codex App Server: {exc}") from exc

            self._closing = False
            self._process = process
            threading.Thread(
                target=self._read_stdout,
                name="codex-app-server-reader",
                daemon=True,
            ).start()
            threading.Thread(
                target=self._read_stderr,
                name="codex-app-server-stderr",
                daemon=True,
            ).start()

            try:
                self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "telegram_internal_tool",
                            "title": "Telegram Internal Tool",
                            "version": "0.2.0",
                        }
                    },
                    self.request_timeout,
                )
                self.notify("initialized", {})
            except Exception:
                self.close()
                raise

    def request(
        self, method: str, params: dict[str, Any], timeout: int | None = None
    ) -> Any:
        self.start()
        return self._request(method, params, timeout or self.request_timeout)

    def _request(self, method: str, params: dict[str, Any], timeout: int) -> Any:
        with self._state_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingResponse()
            self._pending[request_id] = pending
        try:
            self._write({"method": method, "id": request_id, "params": params})
            if not pending.event.wait(timeout):
                raise CodexError(f"Codex App Server не ответил на {method} за {timeout} сек.")
            if pending.error is not None:
                message = pending.error.get("message", "неизвестная ошибка")
                raise CodexError(f"Codex App Server ({method}): {message}")
            return pending.result
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self._write({"id": request_id, "result": result})

    def respond_error(self, request_id: Any, code: int, message: str) -> None:
        self._write({"id": request_id, "error": {"code": code, "message": message}})

    def _write(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise CodexError("Codex App Server не запущен")
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexError("Соединение с Codex App Server закрыто") from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    LOG.warning("Codex App Server вернул не-JSON строку: %s", line[:500])
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if request_id is not None and "method" not in message:
                    with self._state_lock:
                        pending = self._pending.get(request_id)
                    if pending is not None:
                        pending.result = message.get("result")
                        error = message.get("error")
                        pending.error = error if isinstance(error, dict) else None
                        pending.event.set()
                    continue
                try:
                    self._handler(message)
                except Exception:
                    LOG.exception("Ошибка обработки события Codex App Server")
        finally:
            exit_code = process.poll()
            with self._state_lock:
                for pending in self._pending.values():
                    pending.error = {
                        "message": f"Codex App Server завершился с кодом {exit_code}"
                    }
                    pending.event.set()
            if not self._closing:
                try:
                    self._handler(
                        {
                            "method": "client/processExited",
                            "params": {"exitCode": exit_code},
                        }
                    )
                except Exception:
                    LOG.exception("Ошибка обработки остановки Codex App Server")

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            value = line.rstrip()
            if value:
                LOG.info("codex app-server: %s", value)

    def close(self) -> None:
        with self._start_lock:
            self._closing = True
            process = self._process
            self._process = None
            if process is None:
                return
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


@dataclass(frozen=True, slots=True)
class CodexStatus:
    thread_id: str | None
    turn_id: str | None
    status: str
    last_response: str
    last_error: str
    pending_requests: int


@dataclass(frozen=True, slots=True)
class CodexThreadSummary:
    thread_id: str
    name: str
    preview: str
    status: str
    cwd: str


@dataclass(slots=True)
class _PendingServerRequest:
    request_id: Any
    method: str
    params: dict[str, Any]


@dataclass(slots=True)
class _Session:
    chat_id: int
    thread_id: str
    turn_id: str | None = None
    status: str = "idle"
    last_response: str = ""
    last_error: str = ""
    loaded: bool = False
    output: list[str] = field(default_factory=list)
    pending: deque[_PendingServerRequest] = field(default_factory=deque)


class CodexManager:
    def __init__(
        self,
        *,
        storage: Storage,
        notify_user: Callable[[int, str], None],
        enabled: bool,
        executable: str,
        workspace: Path,
        model: str | None,
        approval_policy: str,
        request_timeout: int,
        rpc: RpcTransport | None = None,
    ) -> None:
        self.storage = storage
        self.notify_user = notify_user
        self.enabled = enabled
        self.workspace = workspace.resolve()
        self.model = model
        self.approval_policy = approval_policy
        self._lock = threading.RLock()
        self._operations = threading.RLock()
        self._sessions: dict[int, _Session] = {}
        self._thread_to_chat: dict[str, int] = {}
        self._rpc: RpcTransport = rpc or CodexAppServerClient(
            executable=executable,
            cwd=self.workspace,
            request_timeout=request_timeout,
        )
        self._rpc.set_handler(self._handle_message)
        for record in self.storage.list_codex_sessions():
            session = self._from_record(record)
            self._sessions[session.chat_id] = session
            self._thread_to_chat[session.thread_id] = session.chat_id

    @staticmethod
    def _from_record(record: CodexSessionRecord) -> _Session:
        return _Session(
            chat_id=record.chat_id,
            thread_id=record.thread_id,
            status="idle",
            last_response=record.last_response,
            last_error=record.last_error,
        )

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise CodexError("Интеграция Codex отключена в конфигурации")

    @staticmethod
    def _clean_prompt(prompt: str) -> str:
        value = prompt.strip()
        if not value:
            raise CodexError("Запрос не может быть пустым")
        if len(value) > 12_000:
            raise CodexError("Запрос слишком длинный (максимум 12000 символов)")
        return value

    def _thread_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": str(self.workspace),
            "approvalPolicy": self.approval_policy,
            "sandbox": "workspaceWrite",
            "serviceName": "telegram_internal_tool",
        }
        if self.model:
            params["model"] = self.model
        return params

    def _create_session(self, chat_id: int) -> _Session:
        result = self._rpc.request("thread/start", self._thread_params())
        thread = result.get("thread", {}) if isinstance(result, dict) else {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexError("Codex App Server не вернул ID новой задачи")
        session = _Session(chat_id=chat_id, thread_id=thread_id, loaded=True)
        with self._lock:
            previous = self._sessions.get(chat_id)
            if previous is not None:
                self._thread_to_chat.pop(previous.thread_id, None)
            self._sessions[chat_id] = session
            self._thread_to_chat[thread_id] = chat_id
        self._persist(session)
        return session

    def _resume_session(self, session: _Session) -> _Session:
        if session.loaded:
            return session
        params = {"threadId": session.thread_id, **self._thread_params()}
        result = self._rpc.request("thread/resume", params)
        thread = result.get("thread", {}) if isinstance(result, dict) else {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise CodexError("Codex App Server не смог возобновить задачу")
        with self._lock:
            session.loaded = True
            session.status = "idle"
            self._thread_to_chat[thread_id] = session.chat_id
        return session

    def _get_or_create_session(self, chat_id: int) -> _Session:
        with self._lock:
            session = self._sessions.get(chat_id)
        return self._resume_session(session) if session is not None else self._create_session(chat_id)

    def start_task(self, chat_id: int, prompt: str, *, new_thread: bool = False) -> CodexStatus:
        self._require_enabled()
        value = self._clean_prompt(prompt)
        with self._operations:
            self._rpc.start()
            with self._lock:
                current = self._sessions.get(chat_id)
                if current is not None and current.status == "inProgress":
                    raise CodexBusyError(
                        "Codex уже выполняет задачу. Используйте /codex_reply или /codex_stop."
                    )
            session = self._create_session(chat_id) if new_thread else self._get_or_create_session(chat_id)
            return self._start_turn(session, value)

    def continue_task(self, chat_id: int, prompt: str) -> CodexStatus:
        self._require_enabled()
        value = self._clean_prompt(prompt)
        with self._operations:
            self._rpc.start()
            with self._lock:
                session = self._sessions.get(chat_id)
            if session is None:
                raise CodexError("Сначала создайте задачу командой /codex запрос")
            session = self._resume_session(session)
            with self._lock:
                active_turn = session.turn_id if session.status == "inProgress" else None
            if active_turn:
                self._rpc.request(
                    "turn/steer",
                    {
                        "threadId": session.thread_id,
                        "expectedTurnId": active_turn,
                        "input": [{"type": "text", "text": value}],
                    },
                )
                return self.status(chat_id)
            return self._start_turn(session, value)

    def _start_turn(self, session: _Session, prompt: str) -> CodexStatus:
        with self._lock:
            session.turn_id = None
            session.status = "starting"
            session.last_error = ""
            session.output.clear()
            session.pending.clear()
        result = self._rpc.request(
            "turn/start",
            {
                "threadId": session.thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
        )
        turn = result.get("turn", {}) if isinstance(result, dict) else {}
        turn_id = turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise CodexError("Codex App Server не вернул ID запуска")
        with self._lock:
            # Быстрый turn может завершиться в reader-потоке до обработки ответа.
            # В этом случае не затираем уже полученный терминальный статус.
            if session.status == "starting":
                session.turn_id = turn_id
                session.status = str(turn.get("status") or "inProgress")
        self._persist(session)
        return self.status(session.chat_id)

    def stop_task(self, chat_id: int) -> CodexStatus:
        self._require_enabled()
        with self._operations:
            with self._lock:
                session = self._sessions.get(chat_id)
                if session is None or session.status != "inProgress" or not session.turn_id:
                    raise CodexError("Активной задачи Codex нет")
                thread_id, turn_id = session.thread_id, session.turn_id
            self._rpc.request(
                "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
            )
        return self.status(chat_id)

    def list_threads(self, limit: int = 10) -> list[CodexThreadSummary]:
        self._require_enabled()
        limit = max(1, min(limit, 20))
        with self._operations:
            result = self._rpc.request(
                "thread/list",
                {
                    "limit": limit,
                    "sortKey": "recency_at",
                    "sortDirection": "desc",
                },
            )
        rows = result.get("data", []) if isinstance(result, dict) else []
        summaries: list[CodexThreadSummary] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            status = row.get("status", {})
            status_text = status.get("type", "unknown") if isinstance(status, dict) else str(status)
            summaries.append(
                CodexThreadSummary(
                    thread_id=row["id"],
                    name=str(row.get("name") or "Без названия"),
                    preview=str(row.get("preview") or ""),
                    status=status_text,
                    cwd=str(row.get("cwd") or ""),
                )
            )
        return summaries

    def use_thread(self, chat_id: int, thread_id_or_prefix: str) -> CodexStatus:
        self._require_enabled()
        value = thread_id_or_prefix.strip()
        if not value:
            raise CodexError("Укажите ID задачи из /codex_threads")
        with self._operations:
            self._rpc.start()
            with self._lock:
                current = self._sessions.get(chat_id)
                if current is not None and current.status == "inProgress":
                    raise CodexBusyError("Сначала остановите текущую задачу через /codex_stop")
            if len(value) < 20:
                matches = [item.thread_id for item in self.list_threads(20) if item.thread_id.startswith(value)]
                if len(matches) != 1:
                    raise CodexError("Короткий ID не найден или неоднозначен; используйте полный ID")
                value = matches[0]
            result = self._rpc.request(
                "thread/resume", {"threadId": value, **self._thread_params()}
            )
            thread = result.get("thread", {}) if isinstance(result, dict) else {}
            thread_id = thread.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise CodexError("Не удалось подключить задачу Codex")
            session = _Session(chat_id=chat_id, thread_id=thread_id, loaded=True)
            with self._lock:
                previous = self._sessions.get(chat_id)
                if previous is not None:
                    self._thread_to_chat.pop(previous.thread_id, None)
                self._sessions[chat_id] = session
                self._thread_to_chat[thread_id] = chat_id
            self._persist(session)
            return self.status(chat_id)

    def approve(self, chat_id: int, *, for_session: bool = False) -> None:
        self._respond_to_approval(chat_id, approve=True, for_session=for_session)

    def decline(self, chat_id: int) -> None:
        self._respond_to_approval(chat_id, approve=False, for_session=False)

    def _respond_to_approval(
        self, chat_id: int, *, approve: bool, for_session: bool
    ) -> None:
        with self._lock:
            session = self._sessions.get(chat_id)
            request = self._find_pending(session, approval=True)
            if request is None:
                raise CodexError("Ожидающего подтверждения нет")
        if request.method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = "acceptForSession" if approve and for_session else (
                "accept" if approve else "decline"
            )
            self._rpc.respond(request.request_id, {"decision": decision})
        elif request.method == "item/permissions/requestApproval":
            permissions = request.params.get("permissions", {}) if approve else {}
            result: dict[str, Any] = {"permissions": permissions}
            if approve and for_session:
                result["scope"] = "session"
            self._rpc.respond(request.request_id, result)
        else:
            raise CodexError("Этот запрос нельзя подтвердить данной командой")
        with self._lock:
            if request in session.pending:
                session.pending.remove(request)

    def answer(self, chat_id: int, answer_text: str) -> None:
        value = answer_text.strip()
        if not value:
            raise CodexError("Ответ не может быть пустым")
        with self._lock:
            session = self._sessions.get(chat_id)
            request = self._find_pending(session, approval=False)
            if request is None:
                raise CodexError("Codex сейчас не ожидает ответа")
            questions = request.params.get("questions", [])
            if not isinstance(questions, list) or not questions:
                raise CodexError("Запрос Codex не содержит вопросов")
            parts = [part.strip() for part in value.split("|")]
            if len(parts) != len(questions) or any(not part for part in parts):
                raise CodexError(
                    f"Нужно {len(questions)} ответ(а/ов), разделённых символом |"
                )
            answers: dict[str, Any] = {}
            for question, part in zip(questions, parts, strict=True):
                if not isinstance(question, dict) or not isinstance(question.get("id"), str):
                    raise CodexError("Codex прислал вопрос без ID")
                if question.get("isSecret"):
                    raise CodexError("Передача секретных ответов через Telegram запрещена")
                answers[question["id"]] = {"answers": [part]}
        self._rpc.respond(request.request_id, {"answers": answers})
        with self._lock:
            if request in session.pending:
                session.pending.remove(request)

    @staticmethod
    def _find_pending(
        session: _Session | None, *, approval: bool
    ) -> _PendingServerRequest | None:
        if session is None:
            return None
        approval_methods = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
        }
        for request in session.pending:
            if (request.method in approval_methods) == approval:
                return request
        return None

    def status(self, chat_id: int) -> CodexStatus:
        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                return CodexStatus(None, None, "notStarted", "", "", 0)
            return CodexStatus(
                thread_id=session.thread_id,
                turn_id=session.turn_id,
                status=session.status,
                last_response=session.last_response,
                last_error=session.last_error,
                pending_requests=len(session.pending),
            )

    def _persist(self, session: _Session) -> None:
        self.storage.save_codex_session(
            chat_id=session.chat_id,
            thread_id=session.thread_id,
            status=session.status,
            last_turn_id=session.turn_id,
            last_response=session.last_response,
            last_error=session.last_error,
        )

    def _session_for_params(self, params: dict[str, Any]) -> _Session | None:
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            return None
        with self._lock:
            chat_id = self._thread_to_chat.get(thread_id)
            return self._sessions.get(chat_id) if chat_id is not None else None

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        if "id" in message:
            self._handle_server_request(message["id"], method, params)
            return

        session = self._session_for_params(params)
        if method == "item/agentMessage/delta" and session is not None:
            delta = params.get("delta")
            if isinstance(delta, str):
                with self._lock:
                    current_size = sum(len(part) for part in session.output)
                    if current_size < 30_000:
                        session.output.append(delta[: 30_000 - current_size])
            return
        if method == "item/completed" and session is not None:
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text")
                if isinstance(text, str) and text and not session.output:
                    with self._lock:
                        session.output.append(text[:30_000])
            return
        if method == "turn/started" and session is not None:
            turn = params.get("turn")
            if isinstance(turn, dict):
                with self._lock:
                    session.turn_id = str(turn.get("id") or session.turn_id or "") or None
                    session.status = "inProgress"
                self._persist(session)
            return
        if method == "turn/completed" and session is not None:
            self._complete_turn(session, params)
            return
        if method == "client/processExited":
            exit_code = params.get("exitCode")
            with self._lock:
                active = [item for item in self._sessions.values() if item.status == "inProgress"]
                for item in active:
                    item.status = "failed"
                    item.turn_id = None
                    item.last_error = f"Codex App Server завершился с кодом {exit_code}"
                    self._persist(item)
            for item in active:
                self._notify(item.chat_id, f"Codex остановился: {item.last_error}")

    def _handle_server_request(
        self, request_id: Any, method: str, params: dict[str, Any]
    ) -> None:
        supported = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
        }
        if method == "mcpServer/elicitation/request":
            self._rpc.respond(request_id, {"action": "decline", "content": None})
            return
        if method not in supported:
            self._rpc.respond_error(request_id, -32601, f"Unsupported server request: {method}")
            return
        session = self._session_for_params(params)
        if session is None:
            self._rpc.respond_error(request_id, -32602, "Unknown Telegram session")
            return
        request = _PendingServerRequest(request_id=request_id, method=method, params=params)
        with self._lock:
            session.pending.append(request)
        self._notify(session.chat_id, self._format_pending_request(request))

    def _complete_turn(self, session: _Session, params: dict[str, Any]) -> None:
        turn = params.get("turn")
        turn = turn if isinstance(turn, dict) else {}
        status = str(turn.get("status") or "completed")
        error = turn.get("error")
        error_message = error.get("message", "") if isinstance(error, dict) else ""
        with self._lock:
            response = "".join(session.output).strip()
            session.last_response = response
            session.last_error = str(error_message)
            session.status = status
            session.turn_id = None
            session.output.clear()
            session.pending.clear()
        self._persist(session)
        if status == "completed":
            text = "Codex завершил задачу."
            if response:
                text += "\n\n" + response
        elif status == "interrupted":
            text = "Задача Codex остановлена."
            if response:
                text += "\n\nПромежуточный ответ:\n" + response
        else:
            text = f"Задача Codex завершилась со статусом {status}."
            if error_message:
                text += "\n" + str(error_message)
        self._notify(session.chat_id, text)

    @staticmethod
    def _format_pending_request(request: _PendingServerRequest) -> str:
        params = request.params
        reason = params.get("reason")
        if request.method == "item/commandExecution/requestApproval":
            command = params.get("command")
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            network = params.get("networkApprovalContext")
            details = f"Команда: {command or '(не указана)'}"
            if isinstance(network, dict):
                details = (
                    "Сетевой доступ: "
                    f"{network.get('protocol', '')}://{network.get('host', 'неизвестный адрес')}"
                )
            title = "Codex запрашивает подтверждение команды."
        elif request.method == "item/fileChange/requestApproval":
            details = f"Область изменений: {params.get('grantRoot') or 'рабочая папка'}"
            title = "Codex запрашивает подтверждение изменения файлов."
        elif request.method == "item/permissions/requestApproval":
            details = "Разрешения: " + json.dumps(
                params.get("permissions", {}), ensure_ascii=False
            )
            title = "Codex запрашивает дополнительные разрешения."
        else:
            questions = params.get("questions", [])
            lines = []
            for index, question in enumerate(questions if isinstance(questions, list) else [], 1):
                if isinstance(question, dict):
                    lines.append(f"{index}. {question.get('question', '')}")
                    options = question.get("options")
                    if isinstance(options, list):
                        labels = [str(item.get("label")) for item in options if isinstance(item, dict)]
                        if labels:
                            lines.append("Варианты: " + ", ".join(labels))
            return (
                "Codex ожидает ответ:\n"
                + "\n".join(lines)
                + "\n\nОтветьте: /codex_answer ответ"
                + ("\nДля нескольких вопросов разделяйте ответы символом |" if len(lines) > 1 else "")
            )
        text = f"{title}\n{details}"
        if reason:
            text += f"\nПричина: {reason}"
        return text[:3000] + "\n\n/codex_approve — разрешить один раз\n/codex_decline — отклонить"

    def _notify(self, chat_id: int, text: str) -> None:
        try:
            self.notify_user(chat_id, text)
        except Exception:
            LOG.exception("Не удалось отправить уведомление Codex в Telegram")

    def close(self) -> None:
        self._rpc.close()
