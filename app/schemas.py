from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class CreateSessionReq(BaseModel):
    title: Optional[str] = None


class RenameReq(BaseModel):
    title: str


class ChatReq(BaseModel):
    session_id: str
    message: str
    lang: Optional[str] = None
    report_context: Optional[Dict[str, Any]] = None
    report_contexts: Optional[List[Dict[str, Any]]] = None


class ChatStopReq(BaseModel):
    session_id: str


class FactReq(BaseModel):
    fact: str


class ConfigUpdateReq(BaseModel):
    openai_base_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    use_built_in: Optional[bool] = None
    temperature: Optional[float] = None
    max_iterations: Optional[int] = None
    system_prompt_extra: Optional[str] = None
    reference_content: Optional[str] = None


class ConnTestReq(BaseModel):
    openai_base_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    use_built_in: Optional[bool] = None


class AnalyzeReq(BaseModel):
    extra_question: Optional[str] = None
    lang: Optional[str] = None


class SaveConclusionReq(BaseModel):
    conclusion: str


class FeedbackReq(BaseModel):
    target_type: str                       # 'gc' | 'jstack' | 'chat'
    target_id: str                         # report_id 或 message_id
    verdict: str                           # 'useful' | 'useless' | 'wrong'
    session_id: Optional[str] = None
    comment: Optional[str] = ""