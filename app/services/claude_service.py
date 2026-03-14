"""Claude Sonnet integration for meeting analysis."""

import logging

import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — профессиональный ассистент для анализа протоколов встреч.
Тебе будет предоставлена расшифровка аудиозаписи встречи.
Твоя задача — создать структурированный анализ встречи на русском языке."""

SUMMARY_PROMPT = """Проанализируй расшифровку встречи и создай подробное саммари.

Структура саммари:
1. **Название встречи** (определи по контексту)
2. **Дата и участники** (если упоминаются)
3. **Основные темы обсуждения** — кратко перечисли ключевые темы
4. **Подробное саммари** — детальное описание обсуждения по каждой теме
5. **Ключевые решения** — принятые решения
6. **Открытые вопросы** — вопросы, оставшиеся без ответа

Расшифровка встречи:
{transcript}"""

TASKS_PROMPT = """Проанализируй расшифровку встречи и составь список задач.

Для каждой задачи укажи:
- **Задача**: описание
- **Ответственный**: кто должен выполнить (если упоминается)
- **Срок**: если упоминается дедлайн
- **Приоритет**: Высокий / Средний / Низкий

Формат: пронумерованный список.

Расшифровка встречи:
{transcript}"""

TRANSCRIPT_CLEANUP_PROMPT = """Отредактируй расшифровку встречи для удобства чтения.

Правила:
- Исправь очевидные ошибки распознавания речи
- Раздели по спикерам, если возможно определить смену говорящего
- Добавь пунктуацию и абзацы
- Сохрани весь смысл и содержание оригинала
- НЕ добавляй информацию, которой нет в оригинале

Оригинальная расшифровка:
{transcript}"""


class ClaudeService:
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = "claude-sonnet-4-20250514"

    def _call(self, user_prompt: str, max_tokens: int = 8192) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def create_summary(self, transcript: str) -> str:
        """Generate a meeting summary."""
        logger.info("Generating meeting summary with Claude...")
        return self._call(SUMMARY_PROMPT.format(transcript=transcript))

    def create_tasks(self, transcript: str) -> str:
        """Extract action items / tasks from the transcript."""
        logger.info("Extracting tasks with Claude...")
        return self._call(TASKS_PROMPT.format(transcript=transcript))

    def cleanup_transcript(self, transcript: str) -> str:
        """Clean up and format the raw transcript."""
        logger.info("Cleaning up transcript with Claude...")
        return self._call(TRANSCRIPT_CLEANUP_PROMPT.format(transcript=transcript))

    def full_analysis(self, transcript: str) -> dict[str, str]:
        """Run all analyses and return results."""
        cleaned_transcript = self.cleanup_transcript(transcript)
        summary = self.create_summary(cleaned_transcript)
        tasks = self.create_tasks(cleaned_transcript)

        return {
            "transcript": cleaned_transcript,
            "summary": summary,
            "tasks": tasks,
        }


claude_service = ClaudeService()
