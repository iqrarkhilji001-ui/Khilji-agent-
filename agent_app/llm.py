"""Gemini and legacy Anthropic HTTP clients; no SDK required."""
from __future__ import annotations
import time, uuid
from typing import Callable, Optional
import requests
from .config import Config
from .redact import redact

class LLMError(Exception):
    """Raised when a model call fails. Never includes the API key."""

class GeminiClient:
    RETRY_STATUSES = {429, 500, 502, 503, 504}
    def __init__(self, cfg: Config, sleep: Callable[[float], None] = time.sleep, max_retries: int = 3):
        self.cfg, self._sleep, self._max_retries = cfg, sleep, max_retries

    @staticmethod
    def _schema(value):
        if not isinstance(value, dict): return {"type": "OBJECT"}
        typ = str(value.get("type", "object")).upper()
        out = {"type": typ}
        if "description" in value: out["description"] = value["description"]
        if "properties" in value:
            out["properties"] = {k: GeminiClient._schema(v) for k,v in value["properties"].items()}
        if "required" in value: out["required"] = value["required"]
        if "items" in value: out["items"] = GeminiClient._schema(value["items"])
        if "enum" in value: out["enum"] = value["enum"]
        return out

    def _contents(self, messages):
        contents=[]
        function_names={}
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            blocks = msg.get("content", "")
            if isinstance(blocks, str): blocks=[{"type":"text","text":blocks}]
            parts=[]
            for b in blocks or []:
                if not isinstance(b,dict): continue
                kind=b.get("type")
                if kind == "text" and b.get("text"):
                    parts.append({"text":b["text"]})
                elif kind == "tool_use":
                    function_names[b.get("id")] = b.get("name","tool")
                    parts.append({"functionCall":{"name":b.get("name",""),"args":b.get("input") or {}}})
                elif kind == "tool_result":
                    name=function_names.get(b.get("tool_use_id"))
                    if name:
                        parts.append({"functionResponse":{"name":name,"response":{"result":str(b.get("content",""))}}})
            if parts: contents.append({"role":role,"parts":parts})
        return contents

    def create(self, system: str, messages: list, tools: Optional[list]=None, max_tokens: Optional[int]=None) -> dict:
        if not self.cfg.api_key: raise LLMError("GEMINI_API_KEY is not configured.")
        body={"systemInstruction":{"parts":[{"text":system}]},
              "contents":self._contents(messages),
              "generationConfig":{"maxOutputTokens":max_tokens or self.cfg.max_tokens}}
        if tools:
            decl=[]
            for t in tools:
                fn=t.get("name")
                if fn: decl.append({"name":fn,"description":t.get("description",""),
                                    "parameters":self._schema(t.get("input_schema",{}))})
            if decl: body["tools"]=[{"functionDeclarations":decl}]
        url=f"{self.cfg.api_base}/models/{self.cfg.model}:generateContent"
        last="unknown error"
        for attempt in range(self._max_retries+1):
            try:
                resp=requests.post(url,headers={"x-goog-api-key":self.cfg.api_key,"content-type":"application/json"},
                                   json=body,timeout=self.cfg.request_timeout)
            except requests.RequestException as exc:
                last=f"network error: {type(exc).__name__}"
                if attempt<self._max_retries: self._sleep(min(2**attempt,8)); continue
                raise LLMError(last) from None
            if resp.status_code==200:
                try: data=resp.json()
                except ValueError: raise LLMError("model API returned invalid JSON") from None
                try:
                    candidate=data["candidates"][0]; parts=candidate["content"]["parts"]
                    content=[]; has_calls=False
                    for p in parts:
                        if isinstance(p.get("text"),str) and p["text"]:
                            content.append({"type":"text","text":p["text"]})
                        fc=p.get("functionCall")
                        if isinstance(fc,dict) and fc.get("name"):
                            has_calls=True
                            content.append({"type":"tool_use","id":"gemini_"+uuid.uuid4().hex,
                                            "name":fc["name"],"input":fc.get("args") or {}})
                    um=data.get("usageMetadata") or {}
                    return {"content":content,"stop_reason":"tool_use" if has_calls else "end_turn",
                            "model":self.cfg.model,"usage":{"input_tokens":um.get("promptTokenCount",0),
                            "output_tokens":um.get("candidatesTokenCount",0)}}
                except (KeyError,IndexError,TypeError):
                    raise LLMError("model API returned an unexpected response shape") from None
            detail=""
            try: detail=str(resp.json().get("error",{}).get("message",""))[:250]
            except Exception: pass
            last=f"model API error {resp.status_code}"+(f": {detail}" if detail else "")
            if resp.status_code in self.RETRY_STATUSES and attempt<self._max_retries:
                self._sleep(min(2**attempt,8)); continue
            raise LLMError(redact(last,self.cfg.secrets_list()))
        raise LLMError(redact(last,self.cfg.secrets_list()))

class AnthropicClient:
    """Legacy adapter retained for existing installations and tests."""
    RETRY_STATUSES={429,500,502,503,504,529}
    def __init__(self,cfg:Config,sleep:Callable[[float],None]=time.sleep,max_retries:int=3):
        self.cfg,self._sleep,self._max_retries=cfg,sleep,max_retries
    def create(self,system:str,messages:list,tools:Optional[list]=None,max_tokens:Optional[int]=None)->dict:
        if not self.cfg.api_key: raise LLMError("API key is not configured.")
        body={"model":self.cfg.model,"max_tokens":max_tokens or self.cfg.max_tokens,"system":system,"messages":messages}
        if tools: body["tools"]=tools
        headers={"x-api-key":self.cfg.api_key,"anthropic-version":self.cfg.anthropic_version,"content-type":"application/json"}
        last="unknown error"
        for attempt in range(self._max_retries+1):
            try: resp=requests.post(f"{self.cfg.api_base}/v1/messages",headers=headers,json=body,timeout=self.cfg.request_timeout)
            except requests.RequestException as exc:
                last=f"network error: {type(exc).__name__}"
                if attempt<self._max_retries: self._sleep(min(2**attempt,8)); continue
                raise LLMError(last) from None
            if resp.status_code==200:
                try: return resp.json()
                except ValueError: raise LLMError("model API returned invalid JSON") from None
            try: detail=str(resp.json().get("error",{}).get("message",""))[:250]
            except Exception: detail=""
            last=f"model API error {resp.status_code}"+(f": {detail}" if detail else "")
            if resp.status_code in self.RETRY_STATUSES and attempt<self._max_retries: self._sleep(min(2**attempt,8)); continue
            raise LLMError(redact(last,self.cfg.secrets_list()))
        raise LLMError(redact(last,self.cfg.secrets_list()))
