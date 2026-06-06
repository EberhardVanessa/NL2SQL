from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.graph.agent_state import AgentState
from app.graph.none_thinking.none_thinking_graph import add_non_thinking_path
from app.graph.thinking.thinking_graph import add_thinking_path
from .graph_utils import add_entry_nodes

def build_graph():
    builder = StateGraph(AgentState)

    add_entry_nodes(builder)
    add_non_thinking_path(builder)
    add_thinking_path(builder)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory)
