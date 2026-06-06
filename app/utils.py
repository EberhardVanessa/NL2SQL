import logging
from functools import wraps

from app.graph.agent_state import AgentState

logger = logging.getLogger(__name__)

def log_node(node_name: str, node_func):
    @wraps(node_func)
    def wrapper(state: AgentState) -> AgentState:
        logger.info("Entering node: %s", node_name)

        result = node_func(state)

        logger.info(
            "Leaving node: %s | returned keys: %s",
            node_name,
            list(result.keys()) if isinstance(result, dict) else type(result),
        )

        return result

    return wrapper


def log_router(router_name: str, router_func):
    @wraps(router_func)
    def wrapper(state: AgentState) -> str:
        logger.info("Evaluating router: %s", router_name)

        route = router_func(state)

        logger.info("Router %s selected route: %s", router_name, route)

        return route

    return wrapper
