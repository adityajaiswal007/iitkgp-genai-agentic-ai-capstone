"""Environment configuration. Inference credentials stay in the model adapter."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .contracts import Limits


@dataclass(frozen=True)
class Settings:
    mode: Literal['demo', 'qwen'] = 'demo'
    database_path: Path = Path('data/conversations.sqlite')
    api_key: str = field(default='', repr=False)
    retrieval_base_url: str = ''
    retrieval_api_key: str = field(default='', repr=False)
    max_steps: int = 10
    max_revisions: int = 1
    token_budget: int = 48000
    max_output_tokens: int = 900
    timeout_seconds: float = 15
    query_seconds: float = 120

    def __post_init__(self):
        if self.mode not in ('demo', 'qwen'):
            raise ValueError('AGENT_MODE must be demo or qwen.')
        self.limits()  # Validate at startup, rather than on the first paid request.

    def limits(self):
        return Limits(**{name:getattr(self,name) for name in Limits.__dataclass_fields__})

    @classmethod
    def from_env(cls):
        return cls(
            mode=os.getenv('AGENT_MODE','demo'),
            database_path=Path(os.getenv('AGENT_DATABASE_PATH','data/conversations.sqlite')),
            api_key=os.getenv('AGENT_API_KEY',''),
            retrieval_base_url=os.getenv('RAG_BASE_URL',''),
            retrieval_api_key=os.getenv('RAG_API_KEY',''),
            max_steps=int(os.getenv('AGENT_MAX_STEPS','10')),
            max_revisions=int(os.getenv('AGENT_MAX_REVISIONS','1')),
            token_budget=int(os.getenv('AGENT_TOKEN_BUDGET','48000')),
            max_output_tokens=int(os.getenv('AGENT_MAX_OUTPUT_TOKENS','900')),
            timeout_seconds=float(os.getenv('AGENT_TIMEOUT_SECONDS','15')),
            query_seconds=float(os.getenv('AGENT_QUERY_SECONDS','120')),
        )
