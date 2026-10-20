import json
import logging
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
                    "description": "Descriptive, concise title of the activity (DO NOT include emoji prefixes like ⏳ or ✅)."
                },
                "start_iso": {
                    "type": "string",
                    "description": "Start datetime in ISO 8601 format with local timezone offset, e.g., '2026-10-21T10:00:00+03:00'."
                },
                "end_iso": {
                    "type": "string",
                    "description": "End datetime in ISO 8601 format with local timezone offset, e.g., '2026-10-21T11:30:00+03:00'."
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
                    ],
                    "description": (
                        "Categorized calendar name. Use 'uncertain' if user intent is ambiguous "
                        "or fits multiple categories equally."
                    )
                }
            },
            "required": ["title", "start_iso", "end_iso", "target_calendar_name"]
        }
    }
}

class LLMService:
    def __init__(self):
        self.api_key = Config.OPENROUTER_API_KEY
        self.model = Config.OPENROUTER_MODEL

    def _build_system_prompt(self) -> str:
        tz = Config.USER_TIMEZONE
        now = datetime.now(tz)
        formatted_now = now.strftime("%A, %Y-%m-%d %H:%M:%S %Z (UTC%z)")

        return f"""You are Kairos, an elite AI Executive Assistant and Auto-Scheduler.
Current Temporal Context:
- Exact Current DateTime: {formatted_now}
- Current Day of Week: {now.strftime("%A")}
- Local Timezone: {Config.USER_TIMEZONE_STR}

Your primary responsibility is to analyze user requests, understand their scheduling intent, and call the tool `schedule_session`.

CALENDAR CATEGORY DEFINITIONS:
1. "Personal": Personal life, medical, errands, fitness/gym, relaxation, meals.
2. "Assistant work": Teaching assistantship duties, grading papers, student office hours, assisting professors.
3. "Quizzez / FInals": Preparing for or taking quizzes, exams, midterms, finals.
4. "Self Studying": Independent learning, personal coding projects, technical books, non-university skills.
5. "Uni Classes": Attending scheduled university lectures, recitations, or labs.
6. "University Studying": University coursework, regular homework, problem sets, class readings.
7. "uncertain": If the request lacks clear context to choose between the categories, choose 'uncertain'.

INSTRUCTIONS:
- Whenever the user wants to schedule, study, plan, or block time, CALL THE `schedule_session` TOOL.
- Ensure `start_iso` and `end_iso` include the exact timezone offset matching {Config.USER_TIMEZONE_STR}.
- If duration is not explicitly specified, assume 1 hour.
- If relative times are used ("tomorrow at 3pm", "this Friday at 10am", "in 20 minutes"), calculate the exact target date and time using the current temporal context provided above.
- If the user is having a casual conversation, asking general questions, or clarifying past events, reply naturally without calling tools.
"""

    async def parse_user_request(self, user_message: str, chat_history: list[dict] = None) -> dict:
        """
        Sends the user request to OpenRouter with tool calling enabled.
        Returns a dict:
        {
            "is_tool_call": bool,
            "tool_name": str | None,
            "arguments": dict | None,
            "reply_text": str | None
        }
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
            "HTTP-Referer": "https://github.com/bl33d/kairos",
            "X-Title": "Kairos - AI Executive AutoScheduler",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": [SCHEDULE_TOOL_SCHEMA],
            "tool_choice": "auto",
            "temperature": 0.1
        }

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

                    # No tool call, regular text response
                    return {
                        "is_tool_call": False,
                        "tool_name": None,
                        "arguments": None,
                        "reply_text": message.get("content", "I understood your message.")
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
