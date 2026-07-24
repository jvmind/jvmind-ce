"""SSE streaming adapter that translates LangGraph messages-mode output to the legacy SSE event format."""
from __future__ import annotations

import json
import logging
import queue
import threading

from typing import Any, Callable, Dict, Generator, List, Optional

_logger = logging.getLogger(__name__)

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from .state import MAX_HISTORY_MESSAGES


class SSEAdapter:
    def __init__(self, graph, memory, stream_mode: str = "messages") -> None:
        self.graph = graph
        self.memory = memory
        self.stream_mode = stream_mode

    def run_stream(
        self,
        session_id: str,
        user_input: str,
        llm_input: Optional[str] = None,
        lang: str = "",
        initial_messages: Optional[List[BaseMessage]] = None,
        history_messages: Optional[List[Dict[str, str]]] = None,
        system_prompt: str = "",
        system_prompt_extra: str = "",
        max_iterations: int = 10,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        text_input = llm_input if llm_input is not None else user_input
        try:
            self.memory.append_message(session_id, "user", user_input)
        except Exception:
            pass
        yield {"type": "user", "content": user_input}

        if initial_messages is not None:
            initial_msgs = list(initial_messages)
        else:
            history_messages = history_messages or []
            initial_msgs = self._build_initial_messages(
                system_prompt, history_messages, text_input,
                session_id=session_id,
            )

        progress_queue: "queue.Queue" = queue.Queue(maxsize=1000)
        state_in: Dict[str, Any] = {
            "messages": initial_msgs,
            "session_id": session_id,
            "user_id": "",
            "lang": lang,
            "max_iterations": max_iterations,
            "iteration": 0,
            "scratchpad": "",
            "system_prompt": system_prompt,
            "system_prompt_extra": system_prompt_extra,
            "progress_queue": progress_queue,
            "finalize_structured": False,
            "diagnostic_attachments": {},
        }

        seen_tool_calls: Dict[str, Dict[str, Any]] = {}
        index_to_id: Dict[int, str] = {}
        final_content_buf: List[str] = []
        token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        error_occurred = False
        client_disconnected = False
        message_id: Optional[int] = None

        stop_drain = threading.Event()
        graph_done = threading.Event()
        chunk_queue: "queue.Queue" = queue.Queue(maxsize=1000)
        graph_error: Optional[BaseException] = None
        recursion_limit = max(max_iterations * 5 + 15, 50)

        def _drive_graph():
            nonlocal graph_error
            try:
                for item in self.graph.stream(
                    state_in,
                    {"recursion_limit": recursion_limit},
                    stream_mode=self.stream_mode,
                ):
                    chunk_queue.put(item)
                    if stop_drain.is_set():
                        break
            except BaseException as e:
                graph_error = e
            finally:
                chunk_queue.put(None)
                graph_done.set()

        def _progress_event(ev):
            return {
                "type": "step.progress",
                "tool": ev.tool,
                "tool_call_id": ev.tool_call_id,
                "phase": ev.phase,
                "pct": ev.pct,
                "msg": ev.msg,
            }

        def _drain_progress_events():
            while True:
                try:
                    ev = progress_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    yield _progress_event(ev)
                except (GeneratorExit, Exception):
                    client_disconnected = True
                    return

        try:
            graph_thread = threading.Thread(
                target=_drive_graph, name="lg-graph", daemon=True,
            )
            graph_thread.start()

            def _process_chunk(msg_chunk, metadata):
                nonlocal client_disconnected
                um = getattr(msg_chunk, "usage_metadata", None)
                if um and isinstance(um, dict):
                    token_usage["input_tokens"] += um.get("input_tokens", 0) or 0
                    token_usage["output_tokens"] += um.get("output_tokens", 0) or 0
                    token_usage["total_tokens"] += um.get("total_tokens", 0) or 0
                def _emit_tool_start(tid):
                    info = seen_tool_calls.get(tid)
                    if not info or info.get("_emitted_start"):
                        return None
                    if not (info["name"] or info["args_buf"]):
                        return None
                    info["_emitted_start"] = True
                    return {
                        "type": "tool_start",
                        "tool_call_id": tid,
                        "name": info["name"],
                        "args": info["args_buf"],
                    }

                if isinstance(msg_chunk, AIMessageChunk):
                    ak = getattr(msg_chunk, "additional_kwargs", {}) or {}
                    rc = ak.get("reasoning_content")
                    if rc:
                        try:
                            yield {"type": "token", "content": rc, "phase": "reason"}
                        except (GeneratorExit, Exception):
                            client_disconnected = True
                            return
                    tcc = getattr(msg_chunk, "tool_call_chunks", None) or []
                    for tc in tcc:
                        if not isinstance(tc, dict):
                            continue
                        tid = tc.get("id")
                        idx = tc.get("index")
                        if not tid:
                            # Continuation chunk without id: look up by index.
                            if idx is not None and idx in index_to_id:
                                tid = index_to_id[idx]
                            else:
                                continue
                        else:
                            if idx is not None:
                                index_to_id[idx] = tid
                        if tid not in seen_tool_calls:
                            seen_tool_calls[tid] = {"name": "", "args_buf": "", "index": idx if idx is not None else 0}
                        nm = tc.get("name")
                        if nm:
                            seen_tool_calls[tid]["name"] += nm
                        args_part = tc.get("args")
                        if args_part:
                            seen_tool_calls[tid]["args_buf"] += args_part
                    # Emit tool_start the first time we see this tid with non-empty name OR non-empty args.
                    # Tolerates providers that emit name + args in separate chunks.
                    for tid, info in seen_tool_calls.items():
                        ev = _emit_tool_start(tid)
                        if ev is None:
                            continue
                        try:
                            yield ev
                        except (GeneratorExit, Exception):
                            client_disconnected = True
                            return
                    content = getattr(msg_chunk, "content", "")
                    if content and isinstance(content, str):
                        final_content_buf.append(content)
                        try:
                            yield {"type": "token", "content": content, "phase": "final"}
                        except (GeneratorExit, Exception):
                            client_disconnected = True
                            return
                elif isinstance(msg_chunk, ToolMessage):
                    tid = getattr(msg_chunk, "tool_call_id", "")
                    tool_name = seen_tool_calls.get(tid, {}).get("name", "") or getattr(msg_chunk, "name", "")
                    args_buf = seen_tool_calls.get(tid, {}).get("args_buf", "")
                    tool_input = self._extract_tool_input(args_buf)
                    obs = str(getattr(msg_chunk, "content", ""))
                    tool_status = getattr(msg_chunk, "status", "") or ""
                    status = (
                        "error"
                        if obs.startswith("[Tool Error]")
                        or obs.startswith("[Error]")
                        or obs.startswith("ToolError(")
                        or "ToolError(" in obs
                        or tool_status == "error"
                        else "ok"
                    )
                    # tool_end: emits BEFORE the legacy step so frontend can flip card to done first.
                    try:
                        yield {
                            "type": "tool_end",
                            "tool_call_id": tid,
                            "name": tool_name,
                            "args": args_buf,
                            "observation": obs,
                            "status": status,
                        }
                    except (GeneratorExit, Exception):
                        client_disconnected = True
                        return
                    try:
                        yield {
                            "type": "step",
                            "step": {
                                "thought": "",
                                "action": tool_name,
                                "action_input": tool_input,
                                "observation": obs,
                                "final_answer": None,
                                "tool_call_id": tid,
                            },
                        }
                        if tool_name == "remember":
                            yield {"type": "fact_added", "content": tool_input}
                    except (GeneratorExit, Exception):
                        client_disconnected = True
                        return
                    if tool_name != "remember":
                        try:
                            obs_trunc = obs if len(obs) <= 1200 else obs[:1200] + "..."
                            self.memory.set_context_fact(
                                session_id,
                                "last_tool",
                                f"Last tool observation: tool={tool_name}, input={tool_input}, observation={obs_trunc}",
                            )
                        except Exception:
                            pass
                elif isinstance(msg_chunk, AIMessage):
                    tool_calls = getattr(msg_chunk, "tool_calls", None) or []
                    if tool_calls:
                        for tc in tool_calls:
                            if not isinstance(tc, dict):
                                continue
                            tid = tc.get("id")
                            if not tid:
                                continue
                            if tid not in seen_tool_calls:
                                args_json = json.dumps(tc.get("args", {}), ensure_ascii=False)
                                seen_tool_calls[tid] = {
                                    "name": tc.get("name", ""),
                                    "args_buf": args_json,
                                    "index": 0,
                                }
                        # Emit tool_start for any unseen, non-empty entries (text-mode fallback path).
                        for tid in list(seen_tool_calls.keys()):
                            ev = _emit_tool_start(tid)
                            if ev is None:
                                continue
                            try:
                                yield ev
                            except (GeneratorExit, Exception):
                                client_disconnected = True
                                return
                    else:
                        content = getattr(msg_chunk, "content", "")
                        if content and isinstance(content, str) and not final_content_buf:
                            final_content_buf.append(content)
                            try:
                                yield {"type": "token", "content": content, "phase": "final"}
                            except (GeneratorExit, Exception):
                                client_disconnected = True
                                return

            while True:
                if should_stop and should_stop():
                    stop_drain.set()
                    break
                for ev in _drain_progress_events():
                    try:
                        yield ev
                    except (GeneratorExit, Exception):
                        client_disconnected = True
                        stop_drain.set()
                        break
                    if client_disconnected:
                        break
                if client_disconnected:
                    stop_drain.set()
                    break
                try:
                    item = chunk_queue.get(timeout=0.1)
                except queue.Empty:
                    if graph_done.is_set() and chunk_queue.empty():
                        break
                    continue
                if item is None:
                    break
                msg_chunk, metadata = item
                for ev in _process_chunk(msg_chunk, metadata):
                    try:
                        yield ev
                    except (GeneratorExit, Exception):
                        client_disconnected = True
                        stop_drain.set()
                        break
                if client_disconnected:
                    stop_drain.set()
                    break
                if should_stop and should_stop():
                    stop_drain.set()
                    break

            graph_thread.join(timeout=5.0)
            if graph_thread.is_alive():
                _logger.warning(
                    "graph worker did not finish within 5s; daemon thread will be reaped at process exit"
                )
                stop_drain.set()


            for ev in _drain_progress_events():
                try:
                    yield ev
                except (GeneratorExit, Exception):
                    break

            if graph_error is not None:
                raise graph_error
        except Exception as e:
            error_occurred = True
            try:
                yield {"type": "error", "content": f"{type(e).__name__}: {e}"}
            except (GeneratorExit, Exception):
                pass
        finally:
            stop_drain.set()

        final_text = "".join(final_content_buf).strip()
        if not error_occurred:
            if not final_text and should_stop and should_stop():
                final_text = "(Generation stopped by user.) / (生成已被用户停止。)"
            if final_text:
                try:
                    message_id = self.memory.append_message(session_id, "assistant", final_text)
                except Exception:
                    pass
                if not client_disconnected:
                    try:
                        yield {
                            "type": "final",
                            "content": final_text,
                            "message_id": message_id,
                            "tokens": token_usage,
                            "diagnosis": self._read_last_diagnosis(session_id),
                        }
                    except (GeneratorExit, Exception):
                        client_disconnected = True
        if not client_disconnected:
            try:
                yield {"type": "done", "message_id": message_id, "tokens": token_usage}
            except (GeneratorExit, Exception):
                pass

    def _read_last_diagnosis(self, session_id: str) -> Optional[dict]:
        """Read last Diagnosis payload from memory, or None.

        Task 12's ``finalize_structured_node`` writes the Pydantic Diagnosis model
        to memory via ``set_context_fact(session_id, "last_diagnosis", json.dumps(...))``.
        This helper reads it back for the final SSE event. The ``state_in`` dict
        passed into LangGraph is not mutated by graph reducers, so the diagnosis
        must travel through a side channel.
        """
        if self.memory is None:
            return None
        getter = getattr(self.memory, "get_context_fact", None)
        if getter is None:
            return None
        try:
            raw = getter(session_id, "last_diagnosis")
        except Exception:
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _build_initial_messages(
        self,
        system_prompt: str,
        history: List[Dict[str, str]],
        text_input: str,
        *,
        session_id: str = "",
    ) -> List[BaseMessage]:
        # Embed the persisted summary into the main system prompt string
        # before creating the SystemMessage. Keeping it inside the head's
        # SystemMessage.content (rather than as a sibling SystemMessage) means
        # `_trim_history` cannot accidentally drop it: it always slices the
        # tail, and the head SystemMessage is the only message preserved
        # verbatim. See `_trim_history` and `_prepare_messages` in
        # ``react_agent.graph.nodes``.
        from ..summarizer import inject_summary_into_prompt
        system_prompt = inject_summary_into_prompt(
            system_prompt, session_id, self.memory,
        )
        msgs: List[BaseMessage] = []
        if system_prompt:
            msgs.append(SystemMessage(content=system_prompt))
        truncated = history[-MAX_HISTORY_MESSAGES:]
        # Replace last user message if there is one and llm_input differs
        replaced_last_user = False
        converted: List[BaseMessage] = []
        for i, h in enumerate(truncated):
            role = h.get("role", "")
            content = h.get("content", "")
            if role == "user":
                if i == len(truncated) - 1:
                    converted.append(HumanMessage(content=text_input))
                    replaced_last_user = True
                else:
                    converted.append(HumanMessage(content=content))
            elif role == "assistant":
                converted.append(AIMessage(content=content))
            elif role == "system":
                converted.append(SystemMessage(content=content))
        if not replaced_last_user:
            converted.append(HumanMessage(content=text_input))
        msgs.extend(converted)
        return msgs

    @staticmethod
    def _extract_tool_input(args_buf: str) -> str:
        if not args_buf:
            return ""
        try:
            data = json.loads(args_buf)
            if isinstance(data, dict):
                if "lg_text_arg" in data:
                    return str(data.get("lg_text_arg", ""))
                if "fact" in data:
                    return str(data.get("fact", ""))
                if "input" in data and len(data) == 1:
                    return str(data.get("input", ""))
                vals = [str(v) for v in data.values() if v is not None and str(v) != ""]
                return ",".join(vals)
            return str(data)
        except Exception:
            return str(args_buf)
