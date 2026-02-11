# scratchpad/voice_parser.py
"""
Voice input parsing service for Nxentra Scratchpad.

IMPORTANT: This module follows strict separation of concerns:
- Audio → Text: OpenAI ASR (transcription only)
- Text → Fields: GPT-4o (suggestions only)
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

Flow:
    Audio → gpt-4o-transcribe (JSON) → Extract transcript → Store immediately
                                                ↓
                                    GPT-4o + JSON Schema → Suggestions
                                                ↓
                                         Discard audio
"""

import json
import logging
import random
import time
from dataclasses import dataclass
from decimal import Decimal
from datetime import date
from typing import Optional, List, Dict, Any

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
    if 'timeout' in error_str or 'timed out' in error_str:
        return True

    # Check for connection errors
    if 'connection' in error_str and ('reset' in error_str or 'refused' in error_str):
        return True

    # Check for OpenAI API errors with status codes
    if hasattr(error, 'status_code'):
        return error.status_code in TRANSIENT_STATUS_CODES

    # Check for rate limit errors
    if 'rate limit' in error_str or 'rate_limit' in error_str:
        return True

    # Check for server errors
    if '502' in error_str or '503' in error_str or '504' in error_str:
        return True

    return False


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
    delay = base_delay * (2 ** attempt)
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
                    "confidence": {"type": "number"},
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": [
                    "transaction_date", "amount", "description", "description_ar",
                    "debit_account_code", "credit_account_code", "notes",
                    "confidence", "questions"
                ],
                "additionalProperties": False
            }
        },
        "parse_notes": {"type": ["string", "null"]}
    },
    "required": ["transactions", "parse_notes"],
    "additionalProperties": False
}


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ParsedTransaction:
    """Structured output from voice parsing - SUGGESTIONS only, not truth."""
    transaction_date: Optional[date]
    description: str
    description_ar: str
    amount: Optional[Decimal]
    debit_account_code: Optional[str]
    credit_account_code: Optional[str]
    dimensions: Dict[str, str]
    notes: str
    confidence: Dict[str, float]
    raw_transcript: str
    questions: List[str]


@dataclass
class VoiceUsageInfo:
    """Token and cost tracking for a voice parsing operation."""
    audio_seconds: Optional[Decimal] = None
    transcript_chars: int = 0
    asr_model: str = "whisper-1"
    parse_model: str = "gpt-4o"
    parse_input_tokens: int = 0
    parse_output_tokens: int = 0


@dataclass
class VoiceParseResult:
    """Result from the voice parsing service."""
    success: bool
    transcript: str
    transactions: List[ParsedTransaction]
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    usage: Optional[VoiceUsageInfo] = None


# =============================================================================
# Exceptions
# =============================================================================

class VoiceFeatureDisabledError(Exception):
    """Raised when voice feature is not enabled for tenant."""
    pass


class VoiceQuotaExceededError(Exception):
    """Raised when tenant has exceeded voice usage quota."""
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

    Uses:
    - gpt-4o-transcribe for speech-to-text (returns JSON, extract transcript)
    - gpt-4o with JSON Schema for structured parsing

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
                api_key = getattr(settings, 'OPENAI_API_KEY', None)
                if not api_key:
                    raise ValueError(
                        "OPENAI_API_KEY not configured. "
                        "Set OPENAI_API_KEY environment variable."
                    )
                self._client = OpenAI(api_key=api_key)
            except ImportError:
                raise ImportError(
                    "OpenAI package not installed. Run: pip install openai"
                )
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
        global_enabled = getattr(settings, 'VOICE_PARSING_ENABLED', True)
        if not global_enabled:
            raise VoiceFeatureDisabledError(
                "Voice parsing is disabled globally (VOICE_PARSING_ENABLED=False)"
            )

        # Check tenant-level setting
        tenant_enabled = getattr(company, 'voice_enabled', False)
        if not tenant_enabled:
            raise VoiceFeatureDisabledError(
                "Voice feature not enabled for this tenant (voice_enabled=False)"
            )

        # Check API key is configured (fail fast for voice-enabled tenants)
        api_key = getattr(settings, 'OPENAI_API_KEY', '').strip()
        if not api_key:
            raise VoiceProviderNotConfiguredError(
                "Voice is enabled but OPENAI_API_KEY is not configured. "
                "Contact administrator."
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
        voice_quota = getattr(company, 'voice_quota', None)
        voice_rows_used = getattr(company, 'voice_rows_used', 0)

        # Null quota = not configured = error (no unlimited allowed)
        if voice_quota is None:
            raise VoiceQuotaNotConfiguredError(
                "Voice quota not configured for this tenant. "
                "Contact administrator to set voice_quota."
            )

        if voice_rows_used >= voice_quota:
            raise VoiceQuotaExceededError(
                f"Voice quota exceeded ({voice_rows_used}/{voice_quota})"
            )

    def increment_usage(self, company) -> None:
        """
        Increment voice usage counter after successful transcript storage.

        IMPORTANT: Only call this ONCE per successful transcription.
        Do NOT call during retries to avoid duplicate counting.
        """
        if hasattr(company, 'voice_rows_used'):
            from django.db.models import F
            type(company).objects.filter(pk=company.pk).update(
                voice_rows_used=F('voice_rows_used') + 1
            )

    def transcribe_audio(
        self,
        audio_file,
        language: str = "en",
    ) -> str:
        """
        Transcribe audio file using OpenAI gpt-4o-transcribe.

        This is ASR ONLY - no parsing, no accounting logic.

        AUDIO POLICY: Audio is discarded after this call returns.
        Only the transcript text is returned; audio is never stored.

        Args:
            audio_file: File-like object containing audio data (Django UploadedFile or similar)
            language: Language code (e.g., 'en', 'ar')

        Returns:
            Transcribed text (raw transcript)

        Raises:
            Exception: If transcription fails after retries
        """
        last_error = None

        # Convert Django's InMemoryUploadedFile to a format OpenAI accepts
        # OpenAI expects: bytes, io.IOBase, PathLike, or tuple (filename, content, content_type)
        file_name = getattr(audio_file, 'name', 'audio.webm')
        content_type = getattr(audio_file, 'content_type', 'audio/webm')

        # Read file content as bytes
        audio_file.seek(0)
        file_content = audio_file.read()

        # Create tuple format that OpenAI accepts
        file_tuple = (file_name, file_content, content_type)

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                # Use whisper-1 model which is more widely supported
                # gpt-4o-transcribe has limited availability and format support
                response = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=file_tuple,
                    language=language,
                )

                # whisper-1 returns text directly
                transcript = response.text.strip()
                return transcript

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Transcription attempt {attempt + 1} failed: {e}"
                )

                # Only retry transient errors
                if not is_transient_error(e):
                    logger.error(f"Non-transient error, not retrying: {e}")
                    raise

                if attempt < self.MAX_RETRIES:
                    delay = get_retry_delay(attempt)
                    logger.info(f"Retrying in {delay:.2f}s...")
                    time.sleep(delay)
                    # No need to reset file position - we already have bytes in file_tuple

        logger.error(f"Transcription failed after {self.MAX_RETRIES + 1} attempts")
        raise last_error

    def build_tenant_context(self, company) -> Dict[str, Any]:
        """Build context dictionary from tenant's accounting setup."""
        from accounting.models import Account, AnalysisDimension

        accounts = Account.objects.filter(
            company=company,
            status=Account.Status.ACTIVE,
            is_header=False,
        ).values('code', 'name', 'name_ar', 'account_type')

        account_list = [
            {
                "code": acc['code'],
                "name": acc['name'],
                "name_ar": acc['name_ar'] or acc['name'],
                "type": acc['account_type'],
            }
            for acc in accounts[:200]
        ]

        dimensions = AnalysisDimension.objects.filter(
            company=company,
            is_active=True,
        ).prefetch_related('values')

        dimension_list = []
        for dim in dimensions[:10]:
            values = list(
                dim.values.filter(is_active=True).values('code', 'name')[:50]
            )
            dimension_list.append({
                "code": dim.code,
                "name": dim.name,
                "values": values,
            })

        return {
            "accounts": account_list,
            "dimensions": dimension_list,
            "currency": getattr(company, 'default_currency', 'USD'),
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

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4o",
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
                        }
                    },
                    temperature=0.1,
                    max_tokens=2000,
                )

                raw_response = json.loads(response.choices[0].message.content)
                transactions = self._parse_response(raw_response, transcript)

                # Capture token usage from response
                usage_info = VoiceUsageInfo(
                    transcript_chars=len(transcript),
                    parse_model="gpt-4o",
                    parse_input_tokens=getattr(response.usage, 'prompt_tokens', 0) if response.usage else 0,
                    parse_output_tokens=getattr(response.usage, 'completion_tokens', 0) if response.usage else 0,
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

    def _build_system_prompt(self, context: Dict[str, Any], language: str) -> str:
        """Build the system prompt with tenant context."""
        accounts_json = json.dumps(context['accounts'], indent=2, ensure_ascii=False)
        dimensions_json = json.dumps(context['dimensions'], indent=2, ensure_ascii=False)

        return f"""You are an accounting assistant. Your task is to SUGGEST transaction field values from voice transcripts.

IMPORTANT RULES:
1. You only SUGGEST - you never decide. The user confirms everything.
2. Use ONLY account codes from the provided list. If not found, set to null.
3. Use ONLY dimension codes and values from the provided list.
4. If anything is unclear, set confidence lower and add questions.
5. Never guess. If unsure, set field to null and ask for clarification.
6. Today's date is {context['today']}.

AVAILABLE ACCOUNTS (use exact codes only):
{accounts_json}

AVAILABLE ANALYSIS DIMENSIONS:
{dimensions_json}

CURRENCY: {context['currency']}

For each field, provide a confidence score (0.0 to 1.0):
- 1.0 = Explicitly stated in transcript
- 0.7-0.9 = Strongly implied
- 0.5-0.7 = Inferred with some uncertainty
- <0.5 = Guessing (avoid this - set to null instead)

If the transcript mentions something not in the available accounts/dimensions, add it to the questions array asking for clarification."""

    def _build_user_prompt(self, transcript: str, language: str) -> str:
        """Build the user prompt with the transcript."""
        lang_name = "Arabic" if language == "ar" else "English"
        return f"""Parse this {lang_name} voice transcript into transaction suggestion(s):

TRANSCRIPT:
{transcript}

Extract all transactions mentioned. For each one, suggest field values with confidence scores. Add questions for anything unclear."""

    def _parse_response(
        self,
        response: Dict[str, Any],
        transcript: str,
    ) -> List[ParsedTransaction]:
        """Parse the GPT response into ParsedTransaction objects."""
        transactions = []

        for tx in response.get('transactions', []):
            tx_date = None
            if tx.get('transaction_date'):
                try:
                    tx_date = date.fromisoformat(tx['transaction_date'])
                except ValueError:
                    pass

            amount = None
            if tx.get('amount') is not None:
                try:
                    amount = Decimal(str(tx['amount']))
                except (ValueError, TypeError):
                    pass

            confidence = tx.get('confidence', {})
            if isinstance(confidence, (int, float)):
                confidence = {"overall": float(confidence)}

            transactions.append(ParsedTransaction(
                transaction_date=tx_date,
                description=tx.get('description') or '',
                description_ar=tx.get('description_ar') or '',
                amount=amount,
                debit_account_code=tx.get('debit_account_code'),
                credit_account_code=tx.get('credit_account_code'),
                dimensions=tx.get('dimensions') or {},
                notes=tx.get('notes') or '',
                confidence=confidence,
                raw_transcript=transcript,
                questions=tx.get('questions') or [],
            ))

        return transactions

    def log_usage(
        self,
        company,
        user,
        result: VoiceParseResult,
        audio_seconds: Optional[Decimal] = None,
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
        asr_cost = VoiceUsageEvent.calculate_asr_cost(
            audio_seconds or Decimal("0")
        )
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
            asr_input_tokens=0,  # Whisper doesn't report tokens
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
        audio_seconds: Optional[Decimal] = None,
        user=None,
    ) -> VoiceParseResult:
        """
        Complete flow: transcribe audio and parse into transaction suggestions.

        Flow:
        1. Check feature enabled (global AND tenant flags)
        2. Check quota (must be numeric, no unlimited)
        3. Transcribe audio (ASR only) - audio discarded after
        4. Increment usage counter (ONCE, after successful transcript)
        5. Parse transcript into suggestions

        AUDIO POLICY: Audio file is discarded after transcription.
        Only transcript is persisted. No audio storage.

        Args:
            audio_file: File-like object (discarded after transcription)
            company: Company model instance
            language: Language code

        Returns:
            VoiceParseResult with transcript and suggested transactions
        """
        # Step 1: Check feature gating (global AND tenant)
        self.check_feature_enabled(company)

        # Step 2: Check quota (must be numeric)
        self.check_quota(company)

        transcript = ""

        result = None

        try:
            # Step 3: Transcribe (audio discarded after this)
            transcript = self.transcribe_audio(audio_file, language)
            logger.info(f"Transcribed audio: {transcript[:100]}...")

            # Step 4: Increment usage ONCE after successful transcript
            # (before parsing, so transcript counts even if parsing fails)
            self.increment_usage(company)

            # Step 5: Parse transcript into suggestions
            result = self.parse_transcript(transcript, company, language)

            # Add audio_seconds to usage info
            if result.usage:
                result.usage.audio_seconds = audio_seconds

            # Step 6: Log usage event (if user provided)
            if user:
                self.log_usage(
                    company=company,
                    user=user,
                    result=result,
                    audio_seconds=audio_seconds,
                )

            return result

        except (VoiceFeatureDisabledError, VoiceQuotaExceededError, VoiceQuotaNotConfiguredError):
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
