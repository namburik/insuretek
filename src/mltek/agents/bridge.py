"""LangGraph bridge — single import point for all LangGraph primitives.

App code imports from here so that LangGraph version changes or
library swaps only require edits in this one file.
"""

# Graph construction
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langchain.agents.middleware import ToolCallLimitMiddleware,AgentMiddleware

# Runtime control
from langgraph.types import Command, interrupt

from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from langchain_core.tools import BaseTool, tool
from langgraph.errors import GraphInterrupt, GraphRecursionError
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

try:
    from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
except ImportError:
    ChatGoogleGenerativeAI = None
    GoogleGenerativeAIEmbeddings = None

try:
    from langchain_aws import ChatBedrockConverse, BedrockEmbeddings
except ImportError:
    ChatBedrockConverse = None
    BedrockEmbeddings = None

# LangChain message helpers
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ChatMessage, ToolMessage

# Checkpoint persistence
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

# Config type — LangGraph re-exports this from langchain_core
from langgraph.types import RunnableConfig



def make_thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def get_thread_id(config: RunnableConfig) -> str:
    return config["configurable"]["thread_id"]


async def resume_graph(graph, payload: dict, config: RunnableConfig) -> dict:
    return await graph.ainvoke(Command(resume=payload), config=config)


__all__ = [
    # Graph construction
    "END",
    "START",
    "StateGraph",
    "CompiledStateGraph",
    # Runtime control
    "Command",
    "interrupt",
    # Checkpoint persistence
    "BaseCheckpointSaver",
    "Checkpoint",
    "CheckpointMetadata",
    "CheckpointTuple",
    "ChannelVersions",
    "JsonPlusSerializer",
    # Config
    "RunnableConfig",
    # LangChain messages
    "BaseMessage",
    "HumanMessage",
    "AIMessage",
    "SystemMessage",
    "ChatMessage",
    "ToolMessage",
    # LangChain runtime helpers
    "create_agent",
    "ChatOllama",
    "ChatGoogleGenerativeAI",
    "GoogleGenerativeAIEmbeddings",
    "ChatBedrockConverse",
    "BedrockEmbeddings",
    "BaseTool",
    "tool",
    "traceable",
    # Helpers
    "make_thread_config",
    "get_thread_id",
    "resume_graph",
    "AgentMiddleware",
    "ToolCallLimitMiddleware",
    "get_current_run_tree",
    "GraphInterrupt",
    "GraphRecursionError",
]
