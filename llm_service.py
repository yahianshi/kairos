import json
import logging
import re
from datetime import datetime
import aiohttp
from config import Config

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

SCHEDULE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "schedule_session",
        "description": "Schedule a study, work, or personal session into Google Calendar and Google Tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Concise, descriptive title without emoji prefixes."
                },
                "start_iso": {
                    "type": "string",
                    "description": "ISO 8601 start datetime with timezone offset, e.g. YYYY-MM-DDTHH:MM:SS+03:00."
                },
                "end_iso": {
                    "type": "string",
                    "description": "ISO 8601 end datetime with timezone offset, e.g. YYYY-MM-DDTHH:MM:SS+03:00."
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
                }
            },
            "required": ["title", "start_iso", "end_iso", "target_calendar_name"]
        }
    }
}

class LLMService:
    def __init__(self):
        self.api_key = Config.OPENROUTER_API_KEY
        self.models = Config.OPENROUTER_MODELS

    def _build_system_prompt(self) -> str:
        tz = Config.USER_TIMEZONE
        now = datetime.now(tz)
        formatted_now = now.strftime("%A, %Y-%m-%d %H:%M:%S (UTC%z)")

        return f"""You are Kairos, an AI auto-scheduler for Google Calendar & Google Tasks.
Current Time: {formatted_now}
Timezone: {Config.USER_TIMEZONE_STR}

CALENDARS:
- "Personal": errands, gym, medical, leisure, meals
- "Assistant work": grading, office hours, teaching assistant tasks
- "Quizzez / FInals": exams, tests, quizzes, finals prep
- "Self Studying": independent coding, tech books, non-university study
- "Uni Classes": lectures, scheduled labs, mandatory classes
- "University Studying": university homework, course assignments, problem sets
- "uncertain": when intent is ambiguous

SCHEDULING RULE:
When the user asks to schedule, plan, or block time, output ONLY this exact JSON format:
```json
{{
  "title": "<title without emoji>",
  "start_iso": "YYYY-MM-DDTHH:MM:SS{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}",
  "end_iso": "YYYY-MM-DDTHH:MM:SS{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}",
  "target_calendar_name": "Personal" | "Assistant work" | "Quizzez / FInals" | "Self Studying" | "Uni Classes" | "University Studying" | "uncertain"
}}
```
- If duration is not specified, assume 1 hour.
- If chatting, asking general questions, or discussing past tasks, reply conversationally with normal text.
"""

    def _extract_json_from_text(self, text: str) -> dict | None:
        """Robustly extracts and validates schedule JSON from model text content."""
        if not text:
            return None

        candidate = None
        # Try ```json ... ``` codeblock
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            candidate = code_block.group(1)
        else:
            # Find outermost { ... }
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end+1]

        if candidate:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    # Verify required keys
                    if all(k in data for k in ["title", "start_iso", "end_iso"]):
                        # Ensure target_calendar_name has a default if missing
                        if "target_calendar_name" not in data:
                            data["target_calendar_name"] = "uncertain"
                        return data
            except Exception:
                pass
        return None

    async def parse_user_request(self, user_message: str, chat_history: list[dict] = None) -> dict:
        """
        Sends request to OpenRouter with fallback models and hybrid parsing
        (native tool_calls + direct JSON extraction).
        """
        if not self.api_key:
            return {
                "is_tool_call": False,
                "tool_name": None,
                "arguments": None,
                "reply_text": "Error: OPENROUTER_API_KEY is not configured."
            }

        messages = [
            {"role": "system", "content": self._build_system_prompt()}
        ]

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
            "tools": [SCHEDULE_TOOL_SCHEMA],
            "tool_choice": "auto",
            "temperature": 0.1
        }

        # Use OpenRouter fallback models array if multiple models configured
        if len(self.models) > 1:
            payload["models"] = self.models[:3]
        elif self.models:
            payload["model"] = self.models[0]
        else:
            payload["model"] = "openrouter/free"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"OpenRouter API error (HTTP {resp.status}): {error_text}")
                        return {
                            "is_tool_call": False,
                            "tool_name": None,
                            "arguments": None,
                            "reply_text": f"⚠️ LLM API Error ({resp.status}). Please check your OpenRouter API key and model quota."
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

                    # Strategy 2: Extract JSON from content (in case model output raw JSON or markdown block)
                    content = message.get("content", "")
                    extracted_args = self._extract_json_from_text(content)
                    if extracted_args:
                        logger.info("Successfully extracted schedule JSON from model message content.")
                        return {
                            "is_tool_call": True,
                            "tool_name": "schedule_session",
                            "arguments": extracted_args,
                            "reply_text": None
                        }

                    # Strategy 3: Standard conversational text
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
                "reply_text": f"⚠️ An error occurred while communicating with the AI service: {e}"
            }

# Global instance
llm_service_instance = LLMService()
