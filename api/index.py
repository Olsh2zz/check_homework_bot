# Файл: api/index.py
# Назначение: точка входа для Vercel. Он ищет в этом файле переменную app
# и передаёт ей все запросы.

from bot.web import app

__all__ = ["app"]
