"""Active Recall & Mid-Stream Injection interceptor for token-stream self-querying."""

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

import requests

from eidos.memory.episodic_store import EpisodicStore
from eidos.memory.types import EpisodicSearchResult
from eidos.models.base import ModelResponse, ToolCall

logger = logging.getLogger(__name__)

RECALL_TAG_PATTERN = re.compile(r"<recall>(.*?)</recall>", re.DOTALL | re.IGNORECASE)


class RecallStreamInterceptor:
    """Interceps `<recall>query</recall>` tags during token streaming and splices episodic memory."""

    def __init__(
        self,
        episodic_store: Optional[EpisodicStore] = None,
        max_recalls_per_turn: int = 3,
        min_similarity: float = 0.20,
    ):
        self.episodic_store = episodic_store
        self.max_recalls_per_turn = max_recalls_per_turn
        self.min_similarity = min_similarity

    def lookup_memory(self, query: str) -> Optional[EpisodicSearchResult]:
        """Perform semantic search against episodic vector database for query."""
        if not self.episodic_store:
            return None
        cleaned_query = query.strip()
        if not cleaned_query:
            return None
        try:
            results = self.episodic_store.search(
                query=cleaned_query,
                top_k=1,
                min_similarity=self.min_similarity,
            )
            return results[0] if results else None
        except Exception as e:
            logger.warning("Error querying episodic store for recall '%s': %s", cleaned_query, e)
            return None

    def format_recalled_memory(
        self,
        result: Optional[EpisodicSearchResult],
        query: str,
    ) -> str:
        """Format matching episodic memory into XML block for mid-stream splicing."""
        if result is None:
            return (
                "\n<recalled_memory>\n"
                f'No past experience found matching query: "{query.strip()}".\n'
                "Proceed with deterministic command discovery.\n"
                "</recalled_memory>\nContinuing plan: "
            )

        lines = [f'\n<recalled_memory episode="{result.episode_id}">']
        lines.append(f"- Past Task: {result.task_goal}")
        lines.append(f"- Outcome: {result.outcome}")
        if result.summary:
            lines.append(f"- Resolution Summary: {result.summary}")
        if result.pitfalls_avoided:
            lines.append(f"- Dead Ends / Pitfalls Avoided: {result.pitfalls_avoided}")
        if result.lessons_learned:
            lines.append(f"- Lessons / Gotchas: {result.lessons_learned}")
        if result.key_actions:
            actions_str = " -> ".join(result.key_actions)
            lines.append(f"- Proven Tool Pattern: {actions_str}")
        lines.append("</recalled_memory>\nContinuing plan: ")

        return "\n".join(lines)

    def extract_recall_query(self, text: str) -> Optional[str]:
        """Extract the query string from the first complete `<recall>...</recall>` block."""
        match = RECALL_TAG_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return None

    def execute_chat_with_recall(
        self,
        base_url: str,
        model_name: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 60.0,
        on_recall_callback: Optional[Callable[[str, str], None]] = None,
    ) -> ModelResponse:
        """Stream chat completion, intercepting `<recall>` tags and splicing memories until complete."""
        current_messages = list(messages)
        recall_count = 0
        total_thinking = ""
        total_content = ""
        accumulated_tool_calls: List[ToolCall] = []
        raw_final_data: Dict[str, Any] = {}

        while recall_count < self.max_recalls_per_turn:
            payload: Dict[str, Any] = {
                "model": model_name,
                "messages": current_messages,
                "stream": True,
            }
            if tools:
                payload["tools"] = tools

            interception_triggered = False
            round_content = ""
            round_thinking = ""
            round_tool_calls: List[ToolCall] = []

            resp = None
            try:
                resp = requests.post(
                    f"{base_url.rstrip('/')}/api/chat",
                    json=payload,
                    stream=True,
                    timeout=timeout,
                )
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if not line:
                        continue

                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue

                    raw_final_data = chunk
                    msg = chunk.get("message", {})
                    c_delta = msg.get("content", "") or ""
                    t_delta = msg.get("thinking", "") or ""

                    if c_delta:
                        round_content += c_delta
                    if t_delta:
                        round_thinking += t_delta

                    # Parse streaming tool calls if emitted
                    tc_deltas = msg.get("tool_calls", [])
                    if tc_deltas and isinstance(tc_deltas, list):
                        for t in tc_deltas:
                            fn = t.get("function", {})
                            name = fn.get("name", "")
                            args = fn.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {"raw_input": args}
                            if name:
                                round_tool_calls.append(
                                    ToolCall(name=name, arguments=args, id=t.get("id"))
                                )

                    # Check for recall tag in either content or thinking
                    combined_check = round_content if "<recall>" in round_content else round_thinking
                    if "<recall>" in combined_check and "</recall>" in combined_check:
                        # Full recall tag received!
                        query = self.extract_recall_query(combined_check)
                        if query:
                            logger.info("Active Recall tag intercepted during streaming: '%s'", query)
                            interception_triggered = True

                            # Close HTTP stream immediately
                            resp.close()

                            # Perform vector lookup
                            mem_result = self.lookup_memory(query)
                            splice_text = self.format_recalled_memory(mem_result, query)

                            if on_recall_callback:
                                try:
                                    on_recall_callback(query, splice_text)
                                except Exception as cb_err:
                                    logger.warning("Error in on_recall_callback: %s", cb_err)

                            # Splice the recalled memory right after </recall>
                            if "</recall>" in round_thinking:
                                round_thinking = RECALL_TAG_PATTERN.sub(
                                    rf"<recall>\1</recall>{splice_text}", round_thinking, count=1
                                )
                                total_thinking += round_thinking
                                # Re-inject accumulated thought prefix for assistant continuation
                                assistant_prefix = f"<thought>\n{round_thinking}\n"
                            else:
                                round_content = RECALL_TAG_PATTERN.sub(
                                    rf"<recall>\1</recall>{splice_text}", round_content, count=1
                                )
                                total_content += round_content
                                assistant_prefix = round_content

                            current_messages.append({"role": "assistant", "content": assistant_prefix})
                            recall_count += 1
                            break

                    if chunk.get("done", False):
                        break

            except Exception as exc:
                logger.error("Error during streaming chat with recall: %s", exc)
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
                return ModelResponse(
                    content=f"Error communicating with Ollama: {str(exc)}",
                    model=model_name,
                    raw={"error": str(exc)},
                )
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

            if not interception_triggered:
                # Generation completed naturally without hitting new <recall> tags
                total_content += round_content
                total_thinking += round_thinking
                if round_tool_calls:
                    accumulated_tool_calls.extend(round_tool_calls)
                break

        # Fallback tool extraction from content if none parsed natively
        if not accumulated_tool_calls:
            accumulated_tool_calls = self._extract_json_tool_calls(total_content)

        # Parse <thought> tags if present in total_content
        thought_match = re.search(
            r"<(?:thought|thinking)>(.*?)</(?:thought|thinking)>", total_content, re.DOTALL
        )
        if thought_match:
            total_thinking = thought_match.group(1).strip()
            total_content = re.sub(
                r"<(?:thought|thinking)>.*?</(?:thought|thinking)>", "", total_content, flags=re.DOTALL
            ).strip()

        return ModelResponse(
            content=total_content,
            tool_calls=accumulated_tool_calls,
            thinking=total_thinking,
            model=model_name,
            raw=raw_final_data,
        )

    def execute_openai_chat_with_recall(
        self,
        base_url: str,
        api_key: Optional[str],
        model_name: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 90.0,
        headers: Optional[Dict[str, str]] = None,
        on_recall_callback: Optional[Callable[[str, str], None]] = None,
    ) -> ModelResponse:
        """Stream OpenAI-compatible SSE chat completion, intercepting `<recall>` tags and splicing memories."""
        current_messages = list(messages)
        recall_count = 0
        total_thinking = ""
        total_content = ""
        accumulated_tool_calls: List[ToolCall] = []
        raw_final_data: Dict[str, Any] = {}

        url = f"{base_url.rstrip('/')}/chat/completions"
        req_headers = {"Content-Type": "application/json"}
        if api_key:
            req_headers["Authorization"] = f"Bearer {api_key}"
        if headers:
            req_headers.update(headers)

        while recall_count < self.max_recalls_per_turn:
            payload: Dict[str, Any] = {
                "model": model_name,
                "messages": current_messages,
                "stream": True,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            interception_triggered = False
            round_content = ""
            round_thinking = ""
            tool_call_deltas: Dict[int, Dict[str, Any]] = {}

            resp = None
            try:
                resp = requests.post(
                    url,
                    headers=req_headers,
                    json=payload,
                    stream=True,
                    timeout=timeout,
                )
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if not line:
                        continue

                    line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                    if not line_str.startswith("data:"):
                        continue

                    body = line_str[5:].strip()
                    if not body:
                        continue
                    if body == "[DONE]":
                        break

                    try:
                        chunk = json.loads(body)
                    except Exception:
                        continue

                    raw_final_data = chunk
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})

                    c_delta = delta.get("content") or ""
                    t_delta = delta.get("reasoning_content") or ""

                    if c_delta:
                        round_content += c_delta
                    if t_delta:
                        round_thinking += t_delta

                    tc_deltas = delta.get("tool_calls", [])
                    if tc_deltas and isinstance(tc_deltas, list):
                        for tc in tc_deltas:
                            idx = tc.get("index", 0)
                            if idx not in tool_call_deltas:
                                tool_call_deltas[idx] = {
                                    "id": tc.get("id") or f"call_{idx}",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.get("id"):
                                tool_call_deltas[idx]["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                tool_call_deltas[idx]["name"] = fn["name"]
                            if fn.get("arguments"):
                                tool_call_deltas[idx]["arguments"] += fn["arguments"]

                    combined_check = round_content if "<recall>" in round_content else round_thinking
                    if "<recall>" in combined_check and "</recall>" in combined_check:
                        query = self.extract_recall_query(combined_check)
                        if query:
                            logger.info("Active Recall tag intercepted in OpenAI stream: '%s'", query)
                            interception_triggered = True

                            resp.close()

                            mem_result = self.lookup_memory(query)
                            splice_text = self.format_recalled_memory(mem_result, query)

                            if on_recall_callback:
                                try:
                                    on_recall_callback(query, splice_text)
                                except Exception as cb_err:
                                    logger.warning("Error in on_recall_callback: %s", cb_err)

                            if "</recall>" in round_thinking:
                                round_thinking = RECALL_TAG_PATTERN.sub(
                                    rf"<recall>\1</recall>{splice_text}", round_thinking, count=1
                                )
                                total_thinking += round_thinking
                                assistant_prefix = f"<thought>\n{round_thinking}\n"
                            else:
                                round_content = RECALL_TAG_PATTERN.sub(
                                    rf"<recall>\1</recall>{splice_text}", round_content, count=1
                                )
                                total_content += round_content
                                assistant_prefix = round_content

                            current_messages.append({"role": "assistant", "content": assistant_prefix})
                            recall_count += 1
                            break

            except Exception as exc:
                logger.error("Error during OpenAI streaming chat with recall: %s", exc)
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
                return ModelResponse(
                    content=f"Error communicating with Chutes AI: {str(exc)}",
                    model=model_name,
                    raw={"error": str(exc)},
                )
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

            if not interception_triggered:
                total_content += round_content
                total_thinking += round_thinking
                for idx in sorted(tool_call_deltas.keys()):
                    tc_data = tool_call_deltas[idx]
                    args_str = tc_data["arguments"]
                    try:
                        parsed_args = json.loads(args_str) if args_str else {}
                    except Exception:
                        parsed_args = {"raw_input": args_str}
                    if tc_data["name"]:
                        accumulated_tool_calls.append(
                            ToolCall(
                                name=tc_data["name"],
                                arguments=parsed_args,
                                id=tc_data["id"]
                            )
                        )
                break

        if not accumulated_tool_calls:
            accumulated_tool_calls = self._extract_json_tool_calls(total_content)

        thought_match = re.search(
            r"<(?:thought|thinking)>(.*?)</(?:thought|thinking)>", total_content, re.DOTALL
        )
        if thought_match:
            total_thinking = (total_thinking + "\n" + thought_match.group(1).strip()).strip()
            total_content = re.sub(
                r"<(?:thought|thinking)>.*?</(?:thought|thinking)>", "", total_content, flags=re.DOTALL
            ).strip()

        return ModelResponse(
            content=total_content,
            tool_calls=accumulated_tool_calls,
            thinking=total_thinking,
            model=model_name,
            raw=raw_final_data,
        )

    def _extract_json_tool_calls(self, text: str) -> List[ToolCall]:
        """Fallback tool extraction from markdown codeblocks or JSON snippets."""
        extracted: List[ToolCall] = []

        code_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        for block in code_blocks:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "name" in parsed:
                    args = parsed.get("arguments", parsed.get("parameters", {}))
                    extracted.append(ToolCall(name=parsed["name"], arguments=args))
            except Exception:
                pass

        tag_blocks = re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        for block in tag_blocks:
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "name" in parsed:
                    args = parsed.get("arguments", {})
                    extracted.append(ToolCall(name=parsed["name"], arguments=args))
            except Exception:
                pass

        return extracted
