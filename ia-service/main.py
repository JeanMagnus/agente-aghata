import asyncio
import json
import secrets
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Header, Request, Form, File, UploadFile, HTTPException, FastAPI, Body, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import create_engine, text
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Literal, Optional, List
import asyncpg, os
from langgraph.checkpoint.memory import InMemorySaver

from agente import workflow, settings


# SCHEMAS P/ FAST API
class ChatInput(BaseModel):
    message: str
    thread_id: str = "default"
    user_id: str = "anonymous"
    doc_exist: Optional[bool] = False
    user_name: str = "default"


class ChatOutput(BaseModel):
    response: str
    thread_id: str
    user_id: str

# ROTAS FAST API
router_agent = APIRouter(tags=['Chatbot'])


@router_agent.post("/chat", response_model=ChatOutput, 
                   description="Endpoint para chat direto com o agente.")
async def chat_endpoint(request: ChatInput, req: Request):
    started_at = perf_counter()
    graph = req.app.state.graph
    pool = req.app.state.pool_vector

    config = {"recursion_limit": 15, "configurable": {"thread_id": request.thread_id, "user_id": request.user_id, "pool": pool}}
    inputs = {"messages": [HumanMessage(content=request.message)]}

    result = await graph.ainvoke(inputs, config=config)
    last_msg = result["messages"][-1].content

    elapsed_seconds = perf_counter() - started_at
    print(f"LATENCY /chat: {elapsed_seconds:.2f}s (thread_id={request.thread_id}, user_id={request.user_id})")

    return ChatOutput(response=last_msg, thread_id=request.thread_id, user_id=request.user_id)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DATABASE_VECTOR:
        app.state.pool_vector = await asyncpg.create_pool(
            dsn=settings.DATABASE_VECTOR,
            min_size=1,
            max_size=10,
        )
    else:
        print("[WARN] DATABASE_VECTOR não configurada; pool_vector iniciado como None.")
        app.state.pool_vector = None

    checkpointer = InMemorySaver()
    app.state.graph = workflow.compile(checkpointer=checkpointer)

    try:
        yield
    finally:
        pool = getattr(app.state, "pool_vector", None)
        if pool is not None:
            await pool.close()


app = FastAPI(
    title="Agente Aghata",
    description="Serviço interno de processamento de IA",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(router_agent)

@app.get("/internal/health", description="Endpoint para verificação de saúde do serviço", tags=["Checagem de funcionamento"])
async def health_check():
    return {
        "status": "online",
        "service": "ia-service"
    }
