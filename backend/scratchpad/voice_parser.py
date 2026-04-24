# scratchpad/voice_parser.py
"""
Voice input parsing service for Nxentra Scratchpad.

IMPORTANT: This module follows strict separation of concerns:
- Audio -> Text: OpenAI Transcriptions API (ASR only)
- Text -> Fields: LLM structured parsing (suggestions only)
- Validation: Nxentra rules engine (truth)
- Commit: Nxentra core (immutable events)

The AI SUGGESTS. Rules DECIDE. User CONFIRMS.

AUDIO RETENTION POLICY:
- Audio is NEVER stored by default
- Only the transcript is persisted
- Audio is discarded immediately after transcription

VOICE ENABLEMENT RULE:
Voice is enabled IFF:
  settings.VOICE_PARSING_ENABLED=True AND company.voice_enabled=True
Both flags must be True. Either being False disables voice.

MODEL CONFIGURATION:
Models are configured via Django settings (env-var overridable):
  settings.VOICE_ASR_MODEL    (default: gpt-4o-mini-transcribe)
  settings.VOICE_PARSE_MODEL  (default: gpt-4o-mini)

Flow:
    Audio -> Transcriptions API (ASR) -> transcript -> Store immediately
                                             |
                              Chat Completions + JSON Schema -> Suggestions
                                             |
                                       Discard audio
"""

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# =============================================================================
# Transient Error Detection (for retry logic)
# =============================================================================

# HTTP status codes that indicate transient failures (safe to retry)
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def is_transient_error(error: Exception) -> bool:
    """
    Check if an error is transient and safe to retry.

    Only retry on:
    - Timeouts
    - Rate limits (429)
    - Server errors (5xx)

    Do NOT retry on:
    - Schema/validation errors
    - Authentication errors
    - Client errors (4xx except 408, 429)
    """
    error_str = str(error).lower()

    # Check for timeout keywords
    if "timeout" in error_str or "timed out" in error_str:
        return True

    # Check for connection errors
    if "connection" in error_str and ("reset" in error_str or "refused" in error_str):
        return True

    # Check for OpenAI API errors with status codes
    if hasattr(error, "status_code"):
        return error.status_code in TRANSIENT_STATUS_CODES

    # Check for rate limit errors
    if "rate limit" in error_str or "rate_limit" in error_str:
        return True

    # Check for server errors
    return bool("502" in error_str or "503" in error_str or "504" in error_str)


def get_retry_delay(attempt: int, base_delay: float = 1.0) -> float:
    """
    Calculate retry delay with exponential backoff and jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds

    Returns:
        Delay in seconds with jitter
    """
    # Exponential backoff: 1s, 2s, 4s...
    delay = base_delay * (2**attempt)
    # Add jitter: ±25%
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return delay + jitter


# =============================================================================
# JSON Schema for Structured Output
# =============================================================================

TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "transaction_date": {"type": ["string", "null"]},
                    "amount": {"type": ["number", "null"]},
                    "description": {"type": ["string", "null"]},
                    "description_ar": {"type": ["string", "null"]},
                    "debit_account_code": {"type": ["string", "null"]},
                    "credit_account_code": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "dimensions": {"type": "object", "additionalProperties": {"type": "string"}},
                    "confidence": {
                        "type": "object",
                        "properties": {
                            "overall": {"type": ["number", "null"]},
                            "date": {"type": ["number", "null"]},
                            "amount": {"type": ["number", "null"]},
                            "accounts": {"type": ["number", "null"]},
                            "dimensions": {"type": ["number", "null"]},
                            "description": {"type": ["number", "null"]},
                        },
                        "required": ["overall", "date", "amount", "accounts", "dimensions", "description"],
                        "additionalProperties": False,
                    },
                    "questions": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "transaction_date",
                    "amount",
                    "description",
                    "description_ar",
                    "debit_account_code",
                    "credit_account_code",
                    "notes",
                    "confidence",
                    "questions",
                ],
                "additionalProperties": False,
            },
        },
        "parse_notes": {"type": ["string", "null"]},
    },
    "required": ["transactions", "parse_notes"],
    "additionalProperties": False,
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ParsedTransaction:
    """Structured output from voice parsing - SUGGESTIONS only, not truth."""

    transaction_date: date | None
    description: str
    description_ar: str
    amount: Decimal | None
    debit_account_code: str | None
    credit_account_code: str | None
    dimensions: dict[str, str]
    notes: str
    confidence: dict[str, float]
    raw_transcript: str
    questions: list[str]


@dataclass
class VoiceUsageInfo:
    """Token and cost tracking for a voice parsing operation."""

    audio_seconds: Decimal | None = None
    transcript_chars: int = 0
    asr_model: str = ""  # populated at runtime from settings.VOICE_ASR_MODEL
    parse_model: str = ""  # populated at runtime from settings.VOICE_PARSE_MODEL
    parse_input_tokens: int = 0
    parse_output_tokens: int = 0

    def __post_init__(self):
        if not self.asr_model:
            self.asr_model = getattr(settings, "VOICE_ASR_MODEL", "gpt-4o-mini-transcribe")
        if not self.parse_model:
            self.parse_model = getattr(settings, "VOICE_PARSE_MODEL", "gpt-4o-mini")


@dataclass
class VoiceParseResult:
    """Result from the voice parsing service."""

    success: bool
    transcript: str
    transactions: list[ParsedTransaction]
    error: str | None = None
    raw_response: dict[str, Any] | None = None
    usage: VoiceUsageInfo | None = None


# =============================================================================
# Exceptions
# =============================================================================


class VoiceFeatureDisabledError(Exception):
    """Raised when voice feature is not enabled globally."""

    pass


class VoiceUserNotAuthorizedError(Exception):
    """Raised when user does not have voice access permission."""

    pass


class VoiceQuotaExceededError(Exception):
    """Raised when user has exceeded voice usage quota."""

    pass


class VoiceQuotaNotConfiguredError(Exception):
    """Raised when voice quota is not configured (null/unlimited not allowed)."""

    pass


class VoiceProviderNotConfiguredError(Exception):
    """Raised when voice is enabled but OpenAI API key is missing."""

    pass


# =============================================================================
# Voice Parser Service
# =============================================================================


class VoiceParserService:
    """
    Service for parsing voice input into structured transaction data.

    Uses (configurable via settings):
    - settings.VOICE_ASR_MODEL for speech-to-text (Transcriptions API)
    - settings.VOICE_PARSE_MODEL for structured parsing (Chat Completions)

    IMPORTANT: This service only SUGGESTS fields. Validation and
    truth determination happen in the Nxentra rules engine.

    AUDIO POLICY: Audio is discarded after transcription. Only transcript
    is returned/stored. Audio files are never persisted.
    """

    MAX_RETRIES = 2

    def __init__(self):
        self._client = None

    @property
    def client(self):
        """Lazy initialization of OpenAI client."""
        if self._client is None:
            try:
                from openai import OpenAI

                api_key = getattr(settings, "OPENAI_API_KEY", None)
                if not api_key:
                    raise ValueError("OPENAI_API_KEY not configured. Set OPENAI_API_KEY environment variable.")
                self._client = OpenAI(api_key=api_key)
            except ImportError:
                raise ImportError("OpenAI package not installed. Run: pip install openai")
        return self._client

    def check_feature_enabled(self, company) -> None:
        """
        Check if voice feature is enabled for this tenant.

        RULE: Voice is enabled IFF ALL conditions are true:
        1. settings.VOICE_PARSING_ENABLED = True (global kill-switch)
        2. company.voice_enabled = True (per-tenant flag)
        3. settings.OPENAI_API_KEY is configured (provider requirement)

        Raises:
            VoiceFeatureDisabledError: If voice is not enabled
            VoiceProviderNotConfiguredError: If API key is missing
        """
        # Check global setting (kill-switch)
        global_enabled = getattr(settings, "VOICE_PARSING_ENABLED", True)
        if not global_enabled:
            raise VoiceFeatureDisabledError("Voice parsing is disabled globally (VOICE_PARSING_ENABLED=False)")

        # Check tenant-level setting
        tenant_enabled = getattr(company, "voice_enabled", False)
        if not tenant_enabled:
            raise VoiceFeatureDisabledError("Voice feature not enabled for this tenant (voice_enabled=False)")

        # Check API key is configured (fail fast for voice-enabled tenants)
        api_key = getattr(settings, "OPENAI_API_KEY", "").strip()
        if not api_key:
            raise VoiceProviderNotConfiguredError(
                "Voice is enabled but OPENAI_API_KEY is not configured. Contact administrator."
            )

    def check_quota(self, company) -> None:
        """
        Check if tenant has remaining voice quota.

        RULE: Null/unlimited quota is NOT allowed in production.
        Every tenant must have a numeric quota configured.

        Raises:
            VoiceQuotaNotConfiguredError: If quota is null/unlimited
            VoiceQuotaExceededError: If quota exceeded
        """
        voice_quota = getattr(company, "voice_quota", None)
        voice_rows_used = getattr(company, "voice_rows_used", 0)

        # Null quota = not configured = error (no unlimited allowed)
        if voice_quota is None:
            raise VoiceQuotaNotConfiguredError(
                "Voice quota not configured for this tenant. Contact administrator to set voice_quota."
            )

        if voice_rows_used >= voice_quota:
            raise VoiceQuotaExceededError(f"Voice quota exceeded ({voice_rows_used}/{voice_quota})")

    def increment_usage(self, company) -> None:
        """
        DEPRECATED: Use increment_user_usage instead.
        Increment voice usage counter at company level.
        """
        if hasattr(company, "voice_rows_used"):
            from django.db.models import F

            type(company).objects.filter(pk=company.pk).update(voice_rows_used=F("voice_rows_used") + 1)

    # =========================================================================
    # User-Level Voice Permission Methods
    # =========================================================================

    def check_global_enabled(self) -> None:
        """
        Check if voice feature is enabled globally.

        Raises:
            VoiceFeatureDisabledError: If voice is disabled globally
            VoiceProviderNotConfiguredError: If API key is missing
        """
        # Check global setting (kill-switch)
        global_enabled = getattr(settings, "VOICE_PARSING_ENABLED", True)
        if not global_enabled:
            raise VoiceFeatureDisabledError("Voice parsing is disabled globally (VOICE_PARSING_ENABLED=False)")

        # Check API key is configured
        api_key = getattr(settings, "OPENAI_API_KEY", "").strip()
        if not api_key:
            raise VoiceProviderNotConfiguredError(
                "Voice is enabled but OPENAI_API_KEY is not configured. Contact administrator."
            )

    def check_user_voice_access(self, membership) -> None:
        """
        Check if user has voice access permission.

        Voice access is granted per-user by admin via membership.voice_enabled flag.

        Args:
            membership: CompanyMembership instance

        Raises:
            VoiceFeatureDisabledError: If voice is disabled globally
            VoiceProviderNotConfiguredError: If API key is missing
            VoiceUserNotAuthorizedError: If user does not have voice permission
        """
        # First check global settings
        self.check_global_enabled()

        # Check user-level permission (granted by admin)
        if not getattr(membership, "voice_enabled", False):
            raise VoiceUserNotAuthorizedError(
                "Voice feature not enabled for your account. Contact your administrator to request access."
            )

    def check_user_quota(self, membership) -> None:
        """
        Check if user has remaining voice quota.

        Args:
            membership: CompanyMembership instance

        Raises:
            VoiceQuotaNotConfiguredError: If quota is null
            VoiceQuotaExceededError: If quota exceeded
        """
        voice_quota = getattr(membership, "voice_quota", None)
        voice_rows_used = getattr(membership, "voice_rows_used", 0)

        # Null quota = not configured = error (no unlimited allowed)
        if voice_quota is None:
            raise VoiceQuotaNotConfiguredError(
                "Voice quota not configured for your account. Contact your administrator to set your voice quota."
            )

        if voice_rows_used >= voice_quota:
            raise VoiceQuotaExceededError(
                f"Voice quota exceeded ({voice_rows_used}/{voice_quota}). "
                "Contact your administrator to request additional quota."
            )

    def increment_user_usage(self, membership) -> None:
        """
        Increment user's voice usage counter after successful transcript storage.

        IMPORTANT: Only call this ONCE per successful transcription.
        Do NOT call during retries to avoid duplicate counting.

        Args:
            membership: CompanyMembership instance
        """
        from django.db.models import F

        from accounts.models import CompanyMembership

        CompanyMembership.objects.filter(pk=membership.pk).update(voice_rows_used=F("voice_rows_used") + 1)

    def get_user_voice_status(self, membership) -> dict:
        """
        Get user's voice feature status and quota information.

        Args:
            membership: CompanyMembership instance

        Returns:
            dict with voice status info
        """
        # Check global settings
        try:
            self.check_global_enabled()
            global_enabled = True
            global_error = None
        except (VoiceFeatureDisabledError, VoiceProviderNotConfiguredError) as e:
            global_enabled = False
            global_error = str(e)

        voice_enabled = getattr(membership, "voice_enabled", False)
        voice_quota = getattr(membership, "voice_quota", None)
        voice_rows_used = getattr(membership, "voice_rows_used", 0)

        return {
            "global_enabled": global_enabled,
            "global_error": global_error,
            "user_enabled": voice_enabled,
            "quota": voice_quota,
            "used": voice_rows_used,
            "remaining": max(0, (voice_quota or 0) - voice_rows_used) if voice_quota else 0,
            "can_use": global_enabled and voice_enabled and voice_quota is not None and voice_rows_used < voice_quota,
        }

    # Formats natively supported by the OpenAI Transcriptions API.
    # No conversion needed for these; the file is uploaded directly.
    TRANSCRIPTION_SUPPORTED_TYPES = {
        "audio/flac",
        "audio/mp3",
        "audio/mpeg",
        "audio/mpga",
        "audio/mp4",
        "audio/m4a",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
    }

    def _convert_audio_to_mp3(self, audio_content: bytes, content_type: str) -> bytes:
        """
        Convert audio to MP3 when the source format is not supported
        by the Transcriptions API.

        Most browser formats (webm, mp4, ogg, wav) are supported natively,
        so this is only a fallback for unusual formats.

        Args:
            audio_content: Raw audio bytes
            content_type: MIME type of the audio

        Returns:
            MP3 audio bytes
        """
        import io

        from pydub import AudioSegment

        format_map = {
            "audio/webm": "webm",
            "audio/mp4": "mp4",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/ogg": "ogg",
            "audio/x-wav": "wav",
        }
        input_format = format_map.get(content_type, "webm")

        logger.info(f"Converting audio from {input_format} to mp3")

        try:
            audio_input = io.BytesIO(audio_content)
            audio = AudioSegment.from_file(audio_input, format=input_format)

            mp3_output = io.BytesIO()
            audio.export(mp3_output, format="mp3", bitrate="128k")
            mp3_output.seek(0)

            return mp3_output.read()
        except Exception as e:
            logger.error(f"Audio conversion failed: {e}")
            raise ValueError(f"Failed to convert audio from {input_format} to mp3: {e}")

    def transcribe_audio(
        self,
        audio_file,
        language: str = "en",
    ) -> str:
        """
        Transcribe audio using the OpenAI Transcriptions API.

        Uses settings.VOICE_ASR_MODEL (default: gpt-4o-mini-transcribe).
        Supported models: gpt-4o-mini-transcribe, gpt-4o-transcribe, whisper-1.

        The Transcriptions API accepts direct file uploads and supports
        flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm natively.

        AUDIO POLICY: Audio is discarded after this call returns.
        Only the transcript text is returned; audio is never stored.

        Args:
            audio_file: File-like object containing audio data (Django UploadedFile)
            language: Language code (e.g., 'en', 'ar')

        Returns:
            Transcribed text (raw transcript in the source language)

        Raises:
            Exception: If transcription fails after retries
        """
        import io

        last_error = None
        asr_model = getattr(settings, "VOICE_ASR_MODEL", "gpt-4o-mini-transcribe")

        # Read file content
        audio_file.seek(0)
        file_content = audio_file.read()

        # Determine content type
        content_type = getattr(audio_file, "content_type", "audio/webm")

        # Convert to mp3 only if format is not natively supported
        if content_type not in self.TRANSCRIPTION_SUPPORTED_TYPES:
            file_content = self._convert_audio_to_mp3(file_content, content_type)
            file_name = "audio.mp3"
        else:
            # Derive file extension from content type for the API
            ext_map = {
                "audio/webm": "webm",
                "audio/mp4": "mp4",
                "audio/mpeg": "mp3",
                "audio/mp3": "mp3",
                "audio/mpga": "mp3",
                "audio/m4a": "m4a",
                "audio/wav": "wav",
                "audio/x-wav": "wav",
                "audio/ogg": "ogg",
                "audio/flac": "flac",
            }
            ext = ext_map.get(content_type, "webm")
            file_name = f"audio.{ext}"

        # Build prompt for Arabic to enforce script fidelity
        prompt = None
        if language == "ar":
            prompt = "هذا تسجيل صوتي باللغة العربية. اكتب النص بالحروف العربية فقط. اكتب الأرقام كأرقام: 1000، 5000."

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                # Use the Transcriptions API (file upload, not base64)
                audio_io = io.BytesIO(file_content)
                audio_io.name = file_name

                kwargs = {
                    "model": asr_model,
                    "file": audio_io,
                    "language": language,
                }
                if prompt:
                    kwargs["prompt"] = prompt

                response = self.client.audio.transcriptions.create(**kwargs)
                transcript = response.text.strip()

                logger.info(f"Transcribed audio with {asr_model}: {transcript[:100]}...")
                return transcript

            except Exception as e:
                last_error = e
                logger.warning(f"Transcription attempt {attempt + 1} failed: {e}")

                if not is_transient_error(e):
                    logger.error(f"Non-transient error, not retrying: {e}")
                    raise

                if attempt < self.MAX_RETRIES:
                    delay = get_retry_delay(attempt)
                    logger.info(f"Retrying in {delay:.2f}s...")
                    time.sleep(delay)

        logger.error(f"Transcription failed after {self.MAX_RETRIES + 1} attempts")
        raise last_error

    def build_tenant_context(self, company) -> dict[str, Any]:
        """Build context dictionary from tenant's accounting setup."""
        from accounting.models import Account, AnalysisDimension

        accounts = Account.objects.filter(
            company=company,
            status=Account.Status.ACTIVE,
            is_header=False,
        ).values("code", "name", "name_ar", "account_type")

        account_list = [
            {
                "code": acc["code"],
                "name": acc["name"],
                "name_ar": acc["name_ar"] or acc["name"],
                "type": acc["account_type"],
            }
            for acc in accounts[:200]
        ]

        dimensions = AnalysisDimension.objects.filter(
            company=company,
            is_active=True,
        ).prefetch_related("values")

        dimension_list = []
        for dim in dimensions[:10]:
            values = list(dim.values.filter(is_active=True).values("code", "name")[:50])
            dimension_list.append(
                {
                    "code": dim.code,
                    "name": dim.name,
                    "values": values,
                }
            )

        return {
            "accounts": account_list,
            "dimensions": dimension_list,
            "currency": getattr(company, "default_currency", "USD"),
            "date_format": "YYYY-MM-DD",
            "today": timezone.now().date().isoformat(),
        }

    def parse_transcript(
        self,
        transcript: str,
        company,
        language: str = "en",
    ) -> VoiceParseResult:
        """
        Parse transcript into structured transaction SUGGESTIONS using GPT-4o.

        IMPORTANT: This returns SUGGESTIONS only. The Nxentra rules engine
        determines truth. Users must confirm before commit.
        """
        if not transcript.strip():
            return VoiceParseResult(
                success=False,
                transcript=transcript,
                transactions=[],
                error="Empty transcript",
            )

        context = self.build_tenant_context(company)
        system_prompt = self._build_system_prompt(context, language)
        user_prompt = self._build_user_prompt(transcript, language)

        last_error = None
        parse_model = getattr(settings, "VOICE_PARSE_MODEL", "gpt-4o-mini")

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=parse_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "transaction_parser",
                            "strict": True,
                            "schema": TRANSACTION_SCHEMA,
                        },
                    },
                    temperature=0.1,
                    max_tokens=2000,
                )

                raw_response = json.loads(response.choices[0].message.content)
                transactions = self._parse_response(raw_response, transcript)

                # Capture token usage from response
                usage_info = VoiceUsageInfo(
                    transcript_chars=len(transcript),
                    parse_model=parse_model,
                    parse_input_tokens=getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                    parse_output_tokens=getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                )

                return VoiceParseResult(
                    success=True,
                    transcript=transcript,
                    transactions=transactions,
                    raw_response=raw_response,
                    usage=usage_info,
                )

            except json.JSONDecodeError as e:
                # Schema errors - don't retry
                logger.error(f"JSON decode error: {e}")
                return VoiceParseResult(
                    success=False,
                    transcript=transcript,
                    transactions=[],
                    error=f"Invalid response format: {e}",
                )
            except Exception as e:
                last_error = e
                logger.warning(f"Parsing attempt {attempt + 1} failed: {e}")

                # Only retry transient errors
                if not is_transient_error(e):
                    logger.error(f"Non-transient error, not retrying: {e}")
                    break

                if attempt < self.MAX_RETRIES:
                    delay = get_retry_delay(attempt)
                    logger.info(f"Retrying in {delay:.2f}s...")
                    time.sleep(delay)

        return VoiceParseResult(
            success=False,
            transcript=transcript,
            transactions=[],
            error=str(last_error),
        )

    def _build_system_prompt(self, context: dict[str, Any], language: str) -> str:
        """Build the system prompt with tenant context."""
        accounts_json = json.dumps(context["accounts"], indent=2, ensure_ascii=False)
        dimensions_json = json.dumps(context["dimensions"], indent=2, ensure_ascii=False)

        return f"""You are an accounting assistant. Your task is to SUGGEST transaction field values from voice transcripts.

IMPORTANT RULES:
1. You only SUGGEST - you never decide. The user confirms everything.
2. Use ONLY account codes from the provided list. If not found, set to null.
3. Use ONLY dimension codes and values from the provided list.
4. If anything is unclear, set confidence lower and add questions.
5. Never guess. If unsure, set field to null and ask for clarification.
6. Today's date is {context["today"]}.

AVAILABLE ACCOUNTS (use exact codes only):
{accounts_json}

AVAILABLE ANALYSIS DIMENSIONS:
{dimensions_json}

CURRENCY: {context["currency"]}

Provide confidence as an object with:
- overall (required)
- date, amount, accounts, dimensions, description (optional)

Confidence scale (0.0 to 1.0):
- 1.0 = Explicitly stated in transcript
- 0.7-0.9 = Strongly implied
- 0.5-0.7 = Inferred with some uncertainty
- <0.5 = Guessing (avoid this - set to null instead)

If no dimensions apply, set dimensions to an empty object.

If the transcript mentions something not in the available accounts/dimensions, add it to the questions array asking for clarification."""

    def _build_user_prompt(self, transcript: str, language: str) -> str:
        """Build the user prompt with the transcript."""
        if language == "ar":
            return f"""??? ??? ???? ?????? ?????? ??? ???????? ??????? ???????:

????:
{transcript}

??????? ????? ?????:
- ???/??? = ??????? (????: ?????? ????: ???/?????)
- ???/?????? = ??????? (????: ???/?????? ????: ?????)
- ???? ?? ???? = ??????? (????: ???????? ????: ??????)
- ??? ????? = ?????? (????: ?????? ????: ??????)
- ????? ???? = ????? ??? ??????

?????? ???? ????????? ????????. ??? ??????? ????? ??? ?????? ?? ????? ?????.
??? ????? ??? ??? ??? ????.

Parse into the JSON schema with description_ar in Arabic."""
        else:
            return f"""Parse this English voice transcript into transaction suggestion(s):

TRANSCRIPT:
{transcript}

Extract all transactions mentioned. For each one, suggest field values with confidence scores. Add questions for anything unclear."""

    def _parse_response(
        self,
        response: dict[str, Any],
        transcript: str,
    ) -> list[ParsedTransaction]:
        """Parse the GPT response into ParsedTransaction objects."""
        transactions = []

        for tx in response.get("transactions", []):
            tx_date = None
            if tx.get("transaction_date"):
                try:
                    tx_date = date.fromisoformat(tx["transaction_date"])
                except ValueError:
                    pass

            amount = None
            if tx.get("amount") is not None:
                try:
                    amount = Decimal(str(tx["amount"]))
                except (ValueError, TypeError):
                    pass

            confidence = tx.get("confidence") or {}
            if isinstance(confidence, int | float):
                confidence = {"overall": float(confidence)}
            elif not isinstance(confidence, dict):
                confidence = {}
            if "overall" not in confidence:
                confidence["overall"] = None

            transactions.append(
                ParsedTransaction(
                    transaction_date=tx_date,
                    description=tx.get("description") or "",
                    description_ar=tx.get("description_ar") or "",
                    amount=amount,
                    debit_account_code=tx.get("debit_account_code"),
                    credit_account_code=tx.get("credit_account_code"),
                    dimensions=tx.get("dimensions") or {},
                    notes=tx.get("notes") or "",
                    confidence=confidence,
                    raw_transcript=transcript,
                    questions=tx.get("questions") or [],
                )
            )

        return transactions

    def log_usage(
        self,
        company,
        user,
        result: VoiceParseResult,
        audio_seconds: Decimal | None = None,
        scratchpad_row=None,
    ) -> None:
        """
        Log voice usage event to the append-only usage table.

        This should be called after every voice parsing attempt,
        successful or not, to track usage for billing and analytics.

        Args:
            company: Company model instance
            user: User model instance
            result: VoiceParseResult from parsing
            audio_seconds: Duration of audio in seconds (from frontend)
            scratchpad_row: Optional ScratchpadRow created from this parse
        """
        from .models import VoiceUsageEvent

        usage = result.usage or VoiceUsageInfo()

        # Calculate costs
        asr_cost = VoiceUsageEvent.calculate_asr_cost(audio_seconds or Decimal("0"))
        parse_cost = VoiceUsageEvent.calculate_parse_cost(
            usage.parse_input_tokens,
            usage.parse_output_tokens,
        )

        VoiceUsageEvent.objects.create(
            company=company,
            user=user,
            scratchpad_row=scratchpad_row,
            audio_seconds=audio_seconds,
            transcript_chars=len(result.transcript) if result.transcript else 0,
            asr_model=usage.asr_model,
            parse_model=usage.parse_model,
            asr_input_tokens=0,  # Transcription API bills per audio-second, not tokens
            parse_input_tokens=usage.parse_input_tokens,
            parse_output_tokens=usage.parse_output_tokens,
            asr_cost_usd=asr_cost,
            parse_cost_usd=parse_cost,
            success=result.success,
            error_message=result.error or "",
            transactions_parsed=len(result.transactions),
        )

    def parse_audio(
        self,
        audio_file,
        company,
        language: str = "en",
        audio_seconds: Decimal | None = None,
        user=None,
        membership=None,
    ) -> VoiceParseResult:
        """
        Complete flow: transcribe audio and parse into transaction suggestions.

        Flow:
        1. Transcribe audio (ASR only) - audio discarded after
        2. Increment usage counter (ONCE, after successful transcript)
        3. Parse transcript into suggestions

        NOTE: Feature gating and quota checks should be done by the caller
        using check_user_voice_access() and check_user_quota() before calling
        this method.

        AUDIO POLICY: Audio file is discarded after transcription.
        Only transcript is persisted. No audio storage.

        Args:
            audio_file: File-like object (discarded after transcription)
            company: Company model instance
            language: Language code
            audio_seconds: Duration of audio in seconds
            user: User instance for logging
            membership: CompanyMembership for user-level usage tracking

        Returns:
            VoiceParseResult with transcript and suggested transactions
        """
        transcript = ""
        result = None

        try:
            # Step 1: Transcribe (audio discarded after this)
            transcript = self.transcribe_audio(audio_file, language)
            logger.info(f"Transcribed audio: {transcript[:100]}...")

            # Step 2: Increment usage ONCE after successful transcript
            # (before parsing, so transcript counts even if parsing fails)
            if membership:
                self.increment_user_usage(membership)
            else:
                # Fallback to company-level for backward compatibility
                self.increment_usage(company)

            # Step 3: Parse transcript into suggestions
            result = self.parse_transcript(transcript, company, language)

            # Add audio_seconds to usage info
            if result.usage:
                result.usage.audio_seconds = audio_seconds

            # Step 4: Log usage event (if user provided)
            if user:
                self.log_usage(
                    company=company,
                    user=user,
                    result=result,
                    audio_seconds=audio_seconds,
                )

            return result

        except (
            VoiceFeatureDisabledError,
            VoiceUserNotAuthorizedError,
            VoiceQuotaExceededError,
            VoiceQuotaNotConfiguredError,
        ):
            raise
        except Exception as e:
            logger.error(f"Voice parsing failed: {e}")
            # Return partial result with transcript if we have it
            result = VoiceParseResult(
                success=False,
                transcript=transcript,
                transactions=[],
                error=str(e),
                usage=VoiceUsageInfo(audio_seconds=audio_seconds),
            )

            # Log failed attempt too (if user provided)
            if user:
                self.log_usage(
                    company=company,
                    user=user,
                    result=result,
                    audio_seconds=audio_seconds,
                )

            return result


# Singleton instance
voice_parser = VoiceParserService()
