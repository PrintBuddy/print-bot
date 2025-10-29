import functools
from .logger import LOGGER_MANAGER


logger = LOGGER_MANAGER.get_logger(__name__)


class safe_handler:
    """Decorator class (descriptor) to wrap handlers.

    Implemented as a descriptor so it binds correctly when used on
    instance methods and also supports plain functions. It logs the
    update context and catches exceptions to keep the bot alive.
    """

    def __init__(self, func):
        self.func = func
        functools.update_wrapper(self, func)

    def __get__(self, instance, owner):
        # When accessed on an instance, return a partial that injects the instance
        if instance is None:
            return self
        return functools.partial(self.__call__, instance)

    async def __call__(self, *args, **kwargs):
        # Support both bound methods (self, update, context) and functions (update, context)
        # We will call the original function with the same args/kwargs.
        update = kwargs.get("update")
        context = kwargs.get("context")

        if update is None or context is None:
            if len(args) >= 3:
                # bound method: (self, update, context, ...)
                update = args[1]
                context = args[2]
            elif len(args) == 2:
                # function: (update, context)
                update = args[0]
                context = args[1]

        chat_id = None
        cmd = None
        try:
            if update and getattr(update, "effective_chat", None):
                chat = update.effective_chat
                chat_id = getattr(chat, "id", None)
            if context and getattr(context, "args", None) is not None:
                # attempt to capture command text safely
                msg_obj = getattr(update, "message", None)
                cmd = getattr(msg_obj, "text", None) if msg_obj is not None else None

            logger.info("Handling %s for chat_id=%s", self.func.__name__, chat_id)
            return await self.func(*args, **kwargs)
        except Exception:
            logger.exception("Unhandled exception in handler %s for chat_id=%s cmd=%s", self.func.__name__, chat_id, cmd)
            try:
                if update and getattr(update, "effective_message", None):
                    await update.effective_message.reply_text(  # type: ignore
                        "⚠️ An unexpected error occurred. The team has been notified."
                    )
            except Exception:
                logger.exception("Failed to notify user about handler error for chat_id=%s", chat_id)
