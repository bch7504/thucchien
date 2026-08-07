from langchain_core.messages import ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from psycopg_pool import AsyncConnectionPool

from src.agents.nodes.planner_node import planner_node
from src.agents.state import AgentState
from src.agents.tools import ALL_TOOLS
from src.config import get_settings

# Tools whose own output is already the final answer - no confirmation needed and no benefit
# from a second LLM pass to "relay" it. Routing straight to END after these avoids that second
# pass, which some models handle poorly (observed: hallucinating a bogus repeat tool-call instead
# of plain text). Calendar/reminder tools still go back through planner - their raw output isn't
# user-facing prose, and human-in-the-loop confirmation flows need that turn.
TERMINAL_TOOLS = {"summarize_conversation", "extract_tasks"}


def route_after_planner(state: AgentState) -> str:
    """Route to tool execution, or end the run if the planner errored or has a final reply."""
    if state.get("error"):
        return END
    return tools_condition(state)


def route_after_tools(state: AgentState) -> str:
    """End immediately after a terminal tool (its output is the final answer); otherwise loop
    back to the planner so it can phrase a reply or decide on further tool calls."""
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and last.name in TERMINAL_TOOLS:
        return END
    return "planner"


def build_graph(checkpointer):
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_after_planner, {"tools": "tools", END: END})
    graph.add_conditional_edges("tools", route_after_tools, {"planner": "planner", END: END})

    return graph.compile(checkpointer=checkpointer)


_settings = get_settings()

# `AsyncPostgresSaver` must be constructed inside a *running* event loop (it calls
# asyncio.get_running_loop() in __init__), which isn't available yet at module-import time -
# so it's built later, from init_checkpointer() during FastAPI's lifespan. `agent` stays None
# until then; no /chat call can succeed before init_checkpointer() has been awaited once.
checkpointer, checkpointer_pool, agent = None, None, None


async def init_checkpointer() -> None:
    """Build the Postgres checkpointer/pool and compile `agent` with it. Must be awaited once,
    inside the event loop that will go on to serve requests, before any /chat call."""
    global checkpointer, checkpointer_pool, agent

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    scheme, _, rest = _settings.database_url.partition("://")
    conninfo = f"{scheme.split('+')[0]}://{rest}"
    pool = AsyncConnectionPool(conninfo=conninfo, max_size=10, open=False, kwargs={"autocommit": True})
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()

    checkpointer_pool = pool
    checkpointer = saver
    agent = build_graph(checkpointer)


async def close_checkpointer() -> None:
    if checkpointer_pool is not None:
        await checkpointer_pool.close()
