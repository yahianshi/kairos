import json
import logging
import re
from datetime import datetime
import aiohttp
from config import Config

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# -----------------------------------------------------------------------
# Tool schemas
# -----------------------------------------------------------------------

SCHEDULE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "schedule_session",
        "description": "Schedule a new event/session in Google Calendar.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Concise, descriptive title without emoji."
                },
                "start_iso": {
                    "type": "string",
                    "description": "ISO 8601 start datetime with timezone offset, e.g. 2026-10-21T10:00:00+03:00."
                },
                "end_iso": {
                    "type": "string",
                    "description": "ISO 8601 end datetime with timezone offset."
                },
                "target_calendar_name": {
                    "type": "string",
                    "enum": [
                        "Personal",
                        "Assistant work",
                        "Quizzez / FInals",
                        "Self Studying",
                        "Uni Classes",
                        "University Studying",
                        "uncertain"
                    ]
                },
                "recurrence": {
                    "type": "string",
                    "description": (
                        "Optional recurrence. Use one of: daily, weekly, monthly, "
                        "weekdays, weekends, mon, tue, wed, thu, fri, sat, sun. "
                        "Omit for a one-off event."
                    )
                },
                "reminder_minutes": {
                    "type": "integer",
                    "description": (
                        "Minutes before event start to set a notification reminder on the calendar event "
                        "(e.g. 10, 15, 30). Defaults to 10 if a reminder is requested."
                    )
                }
            },
            "required": ["title", "start_iso", "end_iso", "target_calendar_name"]
        }
    }
}

DELETE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_session",
        "description": (
            "Delete or remove a calendar event that was previously scheduled by the bot. "
            "Use this when the user says 'delete', 'remove', 'cancel' an event they mention by name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search_title": {
                    "type": "string",
                    "description": "Partial or full title of the event to delete (case-insensitive match)."
                }
            },
            "required": ["search_title"]
        }
    }
}

RESCHEDULE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reschedule_session",
        "description": (
            "Move an existing calendar event to a new time. "
            "Use this when the user says 'reschedule', 'move', 'change the time of' an existing event."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search_title": {
                    "type": "string",
                    "description": "Partial or full title of the event to reschedule (case-insensitive match)."
                },
                "new_start_iso": {
                    "type": "string",
                    "description": "New ISO 8601 start datetime with timezone offset."
                },
                "new_end_iso": {
                    "type": "string",
                    "description": "New ISO 8601 end datetime with timezone offset."
                }
            },
            "required": ["search_title", "new_start_iso", "new_end_iso"]
        }
    }
}

ALL_TOOLS = [SCHEDULE_TOOL_SCHEMA, DELETE_TOOL_SCHEMA, RESCHEDULE_TOOL_SCHEMA]


class LLMService:
    def __init__(self):
        self.api_key = Config.OPENROUTER_API_KEY
        self.models = Config.OPENROUTER_MODELS

    def _build_system_prompt(self) -> str:
        tz = Config.USER_TIMEZONE
        now = datetime.now(tz)
        formatted_now = now.strftime("%A, %Y-%m-%d %H:%M:%S (UTC%z)")
        tz_offset = f"{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}"

        return f"""You are Kairos, an AI scheduler for Google Calendar & Tasks.
Current Time: {formatted_now}
Timezone: {Config.USER_TIMEZONE_STR}

CALENDARS:
- "Personal": errands, gym, medical, leisure
- "Assistant work": grading, office hours, TA tasks
- "Quizzez / FInals": exams, tests, quizzes
- "Self Studying": independent study, coding, non-uni reading
- "Uni Classes": lectures, mandatory labs
- "University Studying": uni homework, assignments, problem sets
- "uncertain": ambiguous intent

RULES:
1. SCHEDULE: Call schedule_session when user wants to add/plan/block time.
   JSON format: {{"title":"...","start_iso":"YYYY-MM-DDTHH:MM:SS{tz_offset}","end_iso":"YYYY-MM-DDTHH:MM:SS{tz_offset}","target_calendar_name":"...","recurrence":"...","reminder_minutes":10}}
   - If user requests a reminder/notification (e.g. "reminder 10m before", "add notification 15 mins before"), set "reminder_minutes" to that integer (default 10).
   Default duration = 1 hour if not specified.

2. DELETE: Call delete_session when user says "delete", "remove", or "cancel" a named event.
   JSON format: {{"search_title":"<partial event name>"}}

3. RESCHEDULE: Call reschedule_session when user says "reschedule", "move", or "change the time of" an event.
   JSON format: {{"search_title":"<partial event name>","new_start_iso":"...","new_end_iso":"..."}}
   IMPORTANT: Never call schedule_session for a reschedule request — always call reschedule_session.

4. For general conversation, questions, or status queries: reply with plain text only (no tool call).
"""

    def _extract_json_from_text(self, text: str) -> tuple[str | None, dict | None]:
        """
        Robustly extracts and validates tool JSON from model text content.
        Returns (tool_name, args_dict) or (None, None).
        """
        if not text:
            return None, None

        candidate = None
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            candidate = code_block.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end+1]

        if candidate:
            try:
                data = json.loads(candidate)
                if not isinstance(data, dict):
                    return None, None

                # Determine tool from keys present
                if all(k in data for k in ["title", "start_iso", "end_iso"]):
                    if "target_calendar_name" not in data:
                        data["target_calendar_name"] = "uncertain"
                    return "schedule_session", data

                if "search_title" in data and "new_start_iso" in data:
                    return "reschedule_session", data

                if "search_title" in data:
                    return "delete_session", data

            except Exception:
                pass
        return None, None

    async def parse_user_request(self, user_message: str, chat_history: list[dict] = None) -> dict:
        """
        Sends request to OpenRouter with fallback models and hybrid parsing.
        Returns a dict with keys: is_tool_call, tool_name, arguments, reply_text.
        """
        if not self.api_key:
            return {
                "is_tool_call": False,
                "tool_name": None,
                "arguments": None,
                "reply_text": "Error: OPENROUTER_API_KEY is not configured."
            }

        messages = [{"role": "system", "content": self._build_system_prompt()}]
        if chat_history:
            messages.extend(chat_history)
        messages.append({"role": "user", "content": user_message})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/yahianshi/kairos",
            "X-Title": "Kairos - AI Executive AutoScheduler",
            "Content-Type": "application/json"
        }

        payload = {
            "messages": messages,
            "tools": ALL_TOOLS,
            "tool_choice": "auto",
            "temperature": 0.1
        }

        if len(self.models) > 1:
            payload["models"] = self.models[:3]
        elif self.models:
            payload["model"] = self.models[0]
        else:
            payload["model"] = "openrouter/free"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    OPENROUTER_API_URL, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"OpenRouter API error (HTTP {resp.status}): {error_text}")
                        return {
                            "is_tool_call": False,
                            "tool_name": None,
                            "arguments": None,
                            "reply_text": f"⚠️ LLM API Error ({resp.status}). Check OpenRouter API key and model quota."
                        }

                    data = await resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        return {
                            "is_tool_call": False,
                            "tool_name": None,
                            "arguments": None,
                            "reply_text": "Received an empty response from OpenRouter."
                        }

                    message = choices[0].get("message", {})
                    used_model = data.get("model", "unknown")
                    logger.info(f"OpenRouter served response using model: {used_model}")

                    # Strategy 1: Native tool_calls
                    tool_calls = message.get("tool_calls", [])
                    if tool_calls:
                        tool_call = tool_calls[0]
                        fn_name = tool_call.get("function", {}).get("name")
                        raw_args = tool_call.get("function", {}).get("arguments", "{}")
                        try:
                            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            logger.error(f"Failed to decode LLM tool arguments: {raw_args}")
                            args = {}

                        return {
                            "is_tool_call": True,
                            "tool_name": fn_name,
                            "arguments": args,
                            "reply_text": message.get("content")
                        }

                    # Strategy 2: Extract JSON from content
                    content = message.get("content", "")
                    tool_name, extracted_args = self._extract_json_from_text(content)
                    if tool_name and extracted_args:
                        logger.info(f"Extracted '{tool_name}' JSON from model content.")
                        return {
                            "is_tool_call": True,
                            "tool_name": tool_name,
                            "arguments": extracted_args,
                            "reply_text": None
                        }

                    # Strategy 3: Conversational text
                    return {
                        "is_tool_call": False,
                        "tool_name": None,
                        "arguments": None,
                        "reply_text": content or "I received your message."
                    }

        except Exception as e:
            logger.error(f"Exception during LLM call: {e}", exc_info=True)
            return {
                "is_tool_call": False,
                "tool_name": None,
                "arguments": None,
                "reply_text": f"⚠️ Error communicating with AI service: {e}"
            }


# Global instance
llm_service_instance = LLMService()
