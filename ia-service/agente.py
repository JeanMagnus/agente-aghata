from typing_extensions import Annotated, TypedDict
from typing import Optional, Sequence, Literal
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode
from langchain_core.runnables import RunnableConfig

import os

# CONFIGURAÇÕES

load_dotenv()

class Settings:
    AZURE_OPENAI_BASE_URL = os.getenv("AZURE_OPENAI_BASE_URL")
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    DATABASE_VECTOR = os.getenv("DATABASE_VECTOR")

settings = Settings()


# MODELO

model = ChatOpenAI(
    model="gpt-5-mini",
    base_url=settings.AZURE_OPENAI_BASE_URL,
    api_key=settings.AZURE_OPENAI_API_KEY,
    max_retries=3,
    temperature=0,
    tags=["resposta_final"],
    model_kwargs={"stream_options": {"include_usage": True}}
)

# ESTADO

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]

# TOOLS

model_with_tools = ToolNode(
    model=model,
    tools=[
        tool(
            name="search",
            description="Use esta ferramenta para buscar informações na internet.",
            func=lambda query: f"Resultados da busca para '{query}'"
        ),
        tool(
            name="calculator",
            description="Use esta ferramenta para realizar cálculos matemáticos.",
            func=lambda expression: eval(expression)
        )
    ]
)
# UTILS

# NODES
async def agent(state: AgentState, config: RunnableConfig) -> AgentState:
    print("[DEBUG] INICIANDO AGENTE... ")
    messages = state.get("messages", [])

    conversa_id = state.get("conversa_id") or config.get("configurable", {}).get("thread_id", "1")
    usuario_id = state.get("usuario_id") or config.get("configurable", {}).get("user_id", "anonymous")

    GLOBAL_SYSTEM_PROMPT = f"""

    """


    msgs_to_send = [
        SystemMessage(content=GLOBAL_SYSTEM_PROMPT),
        *messages
    ]

    try:
        response = await model_with_tools.ainvoke(msgs_to_send, config=config)
        usage = getattr(response, "usage_metadata", None)
        if usage:
            input_tokens = usage.get('input_tokens', 0)
            output_tokens = usage.get('output_tokens', 0)
            total_tokens = usage.get('total_tokens', 0)

            input_details = usage.get('input_token_details', {})
            cached_tokens = input_details.get('cache_read', 0)
            
            uncached_tokens = input_tokens - cached_tokens

            print(f"[METRICS] Tokens:")
            print(f"  - Entrada total: {input_tokens}")
            print(f"    ↳ Lidos do cache: {cached_tokens}")
            print(f"    ↳ Processados agora: {uncached_tokens}")
            print(f"  - Saída: {output_tokens}")
            print(f"  - Total da chamada: {total_tokens}")

        return {"messages": [response]}
    except Exception as e:
        print(f"[ERRO] Falha ao invocar o modelo de IA: {str(e)}")
        raise RuntimeError(
            "Não foi possível gerar uma resposta do assistente no momento. Tente novamente em instantes."
        ) from e
# GRAFO



