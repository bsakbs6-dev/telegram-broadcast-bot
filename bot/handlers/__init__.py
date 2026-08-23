"""
Handlers package
"""
from aiogram import Router
from bot.handlers.start import router as start_router
from bot.handlers.auth import router as auth_router
from bot.handlers.messages import router as messages_router
from bot.handlers.channels import router as channels_router
from bot.handlers.settings import router as settings_router
from bot.handlers.control import router as control_router
from bot.handlers.admin import router as admin_router

main_router = Router()

main_router.include_router(start_router)
main_router.include_router(auth_router)
main_router.include_router(messages_router)
main_router.include_router(channels_router)
main_router.include_router(settings_router)
main_router.include_router(control_router)
main_router.include_router(admin_router)

__all__ = ["main_router"]
