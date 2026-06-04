import logging

from models.messages import AgentMessage

logger = logging.getLogger(__name__)


class MessageBus:
    def __init__(self):
        self.messages: list[AgentMessage] = []
        self.handlers: dict[str, callable] = {}

    def register(self, agent_name: str, handler) -> None:
        self.handlers[agent_name] = handler
        logger.debug("MessageBus: registered handler for '%s'", agent_name)

    def send(self, message: AgentMessage) -> None:
        self.messages.append(message)
        logger.debug(
            "MSG [%s -> %s] %s: %s",
            message.sender,
            message.receiver,
            message.message_type,
            message.payload,
        )

    def dispatch(self) -> None:
        for msg in self.messages:
            if msg.receiver in self.handlers:
                self.handlers[msg.receiver](msg)
            else:
                logger.warning(
                    "MessageBus: no handler for '%s' (msg from %s)",
                    msg.receiver,
                    msg.sender,
                )
        self.messages.clear()

    def dispatch_all(self, max_rounds: int = 10) -> None:
        for _ in range(max_rounds):
            if not self.messages:
                break
            self.dispatch()
