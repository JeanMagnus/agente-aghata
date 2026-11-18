from time import perf_counter

from typing_extensions import Annotated, TypedDict
from typing import Optional, Sequence, Literal
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode
from langchain_core.runnables import RunnableConfig

import json, os

# CONFIGURAÇÕES

load_dotenv()

class Settings:
    AZURE_OPENAI_BASE_URL = os.getenv("AZURE_OPENAI_BASE_URL")
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    DATABASE_VECTOR = os.getenv("DATABASE_VECTOR")
    GLOBAL_SYSTEM_PROMPT = os.getenv("GLOBAL_SYSTEM_PROMPT", "").replace("\\n", "\n")
    EXPRESSOES = json.loads(os.getenv("EXPRESSOES", "[]"))

settings = Settings()


# MODELO

model = ChatOpenAI(
    model="gpt-4.1-mini",
    base_url=settings.AZURE_OPENAI_BASE_URL,
    api_key=settings.AZURE_OPENAI_API_KEY,
    max_retries=3,
    temperature=0,
    # reasoning_effort="minimal",
    tags=["resposta_final"],
    stream_usage=True
)

# ESTADO

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]

# TOOLS

@tool
def expressoes() -> str:
    """
    Retorna uma expressão e sua função é encaixar essa expressão de forma engraçada e natural na conversa, como se fosse uma piada ou meme.
    """
    import random
    expressoes_aghata = settings.EXPRESSOES
    return random.choice(expressoes_aghata)

tools = [expressoes]
tool_node = ToolNode(tools=tools)

model_with_tools = model.bind_tools(tools=tools, parallel_tool_calls=True)


# UTILS
def get_current_tools(last_msg):
    """
    Exibe a query de todas as chamadas de ferramenta presentes na mensagem.
    """

    if not hasattr(last_msg, 'tool_calls'):
        return
    
    for tool_call in last_msg.tool_calls:
        tool_name = tool_call.get("name")
        tool_args = tool_call.get("args", {})
        print(f"\n[DEBUG] FERRAMENTA: {tool_name}")

        if tool_name == "sql_db_query":
            query = tool_args.get("query", "")
            print(f"[DEBUG] QUERY: {query}\n")
# NODES

async def prune_history(state: AgentState, config: RunnableConfig):
    start = perf_counter()
    print("INICIANDO PRUNE HISTORY...")
    messages = state.get("messages", [])

    LIMIT_HISTORY = 8
    human_count = 0
    msgs_to_remove = []

    
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            msgs_to_remove.append(RemoveMessage(id=msg.id))
            continue
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            msgs_to_remove.append(RemoveMessage(id=msg.id))
            continue
        if isinstance(msg, HumanMessage):
            human_count += 1
            if human_count > LIMIT_HISTORY:
                msgs_to_remove.append(RemoveMessage(id=msg.id))
            continue
        if human_count > LIMIT_HISTORY:
            msgs_to_remove.append(RemoveMessage(id=msg.id))
        
    end = perf_counter()
    print(f"[DEBUG] Latência do nó 'prune_history': {end - start:.2f}s")
    if msgs_to_remove:
        print(f"[DEBUG] Histórico de mensagens excedeu o limite de {LIMIT_HISTORY}. Removendo {len(msgs_to_remove)} mensagens antigas.")
        return {"messages": msgs_to_remove}
    
async def agent(state: AgentState, config: RunnableConfig) -> AgentState:
    print("[DEBUG] INICIANDO AGENTE... ")
    messages = state.get("messages", [])

    conversa_id = state.get("conversa_id") or config.get("configurable", {}).get("thread_id", "1")
    usuario_id = state.get("usuario_id") or config.get("configurable", {}).get("user_id", "anonymous")
    user_name = state.get("user_name") or config.get("configurable", {}).get("user_name", "default")

    last_human_message = next((msg for msg in reversed(messages) if isinstance(msg, HumanMessage)), None)
    user_text = last_human_message.content if last_human_message else ""

    contexto_documentos = "Nenhum trecho de documento encontrado na base para esta consulta."

    GLOBAL_SYSTEM_PROMPT = settings.GLOBAL_SYSTEM_PROMPT
    DYNAMIC_SYSTEM_PROMPT = f""" 
        Informações pertinentes sobre a conversa:
        - nome do usuário: {user_name}

        Contexto de documentos:
        {contexto_documentos}
    """
    msgs_to_send = [
        SystemMessage(content=GLOBAL_SYSTEM_PROMPT+DYNAMIC_SYSTEM_PROMPT),
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

            output_details = usage.get('output_token_details', {})
            reasoning_tokens = output_details.get('reasoning', 0)

            uncached_tokens = input_tokens - cached_tokens

            print(f"[METRICS] Tokens:")
            print(f"  - Entrada total: {input_tokens}")
            print(f"    ↳ Lidos do cache: {cached_tokens}")
            print(f"    ↳ Processados agora: {uncached_tokens}")
            print(f"  - Saída total: {output_tokens}")
            print(f"    ↳ Raciocínio (reasoning): {reasoning_tokens}")
            print(f"    ↳ Texto/resposta: {output_tokens - reasoning_tokens}")
            print(f"  - Total da chamada: {total_tokens}")

        return {"messages": [response]}
    except Exception as e:
        print(f"[ERRO] Falha ao invocar o modelo de IA: {str(e)}")
        raise RuntimeError(
            "Não foi possível gerar uma resposta do assistente no momento. Tente novamente em instantes."
        ) from e

def should_continue(state: AgentState):
    messages = state.get("messages", [])
    if not messages:
        return END
    last_msg = messages[-1]

    if getattr(last_msg, "tool_calls", None):
        print("EXECUTANDO NÓ DE FERRAMENTAS...")
        get_current_tools(last_msg)
        return "tools"
    
    return END


# GRAFO

workflow = StateGraph(AgentState)
workflow.add_node("agent", agent)
workflow.add_node("tools", tool_node)
workflow.add_node("prune_history", prune_history)
workflow.add_edge(START, "prune_history")
workflow.add_edge("prune_history", "agent")

workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "tools": "tools",
        END: END,
    }
)

workflow.add_edge("tools", "agent")


