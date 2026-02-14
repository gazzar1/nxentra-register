import json
from types import SimpleNamespace

from scratchpad.voice_parser import VoiceParserService


class StubResponse:
    def __init__(self, payload):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        self.usage = SimpleNamespace(prompt_tokens=12, completion_tokens=34)


class StubCompletions:
    def __init__(self, payload):
        self._payload = payload

    def create(self, *args, **kwargs):
        return StubResponse(self._payload)


class StubChat:
    def __init__(self, payload):
        self.completions = StubCompletions(payload)


class StubClient:
    def __init__(self, payload):
        self.chat = StubChat(payload)


def make_service(payload):
    service = VoiceParserService()
    service._client = StubClient(payload)
    service.build_tenant_context = lambda company: {
        "accounts": [
            {"code": "EXP001", "name": "Office Supplies", "name_ar": "لوازم مكتب", "type": "EXPENSE"},
            {"code": "CASH", "name": "Cash", "name_ar": "نقد", "type": "ASSET"},
        ],
        "dimensions": [],
        "currency": "USD",
        "today": "2024-01-10",
    }
    return service


def test_parse_transcript_returns_dimensions_and_confidence():
    payload = {
        "transactions": [
            {
                "transaction_date": "2024-01-10",
                "amount": 100.5,
                "description": "Office supplies",
                "description_ar": "لوازم مكتب",
                "debit_account_code": "EXP001",
                "credit_account_code": "CASH",
                "notes": "",
                "dimensions": {},
                "confidence": {"overall": 0.9, "amount": 0.95, "accounts": 0.8},
                "questions": [],
            }
        ],
        "parse_notes": None,
    }

    service = make_service(payload)
    result = service.parse_transcript(
        transcript="Paid 100.5 for office supplies from cash",
        company=None,
        language="en",
    )

    assert result.success is True
    assert len(result.transactions) == 1
    tx = result.transactions[0]
    assert tx.dimensions == {}
    assert tx.confidence["overall"] == 0.9
    assert result.usage.parse_input_tokens == 12
    assert result.usage.parse_output_tokens == 34


def test_parse_response_normalizes_confidence():
    service = VoiceParserService()
    response = {
        "transactions": [
            {
                "transaction_date": None,
                "amount": None,
                "description": None,
                "description_ar": None,
                "debit_account_code": None,
                "credit_account_code": None,
                "notes": None,
                "dimensions": {},
                "confidence": 0.6,
                "questions": [],
            },
            {
                "transaction_date": None,
                "amount": None,
                "description": None,
                "description_ar": None,
                "debit_account_code": None,
                "credit_account_code": None,
                "notes": None,
                "dimensions": {},
                "confidence": {},
                "questions": [],
            },
        ],
        "parse_notes": None,
    }

    txs = service._parse_response(response, transcript="test")
    assert txs[0].confidence["overall"] == 0.6
    assert "overall" in txs[1].confidence
