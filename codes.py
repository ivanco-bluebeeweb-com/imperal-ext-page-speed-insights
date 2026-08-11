"""Структурные коды ошибок.

Та же конвенция, что у SEO Audit Engine (`codes.py` там): код ОБЯЗАТЕЛЬНЫЙ
позиционный аргумент у `shared.error()`, забыть его невозможно без
TypeError на этапе написания кода, не тихая деградация у пользователя.
"""

# Ключ не настроен вообще.
PSI_NO_KEY = "PSI_NO_KEY"

# Пользователь дал ключ, но Google его не принял (validate до сохранения).
PSI_KEY_INVALID = "PSI_KEY_INVALID"

# URL не передан или пустой.
PSI_NO_URL = "PSI_NO_URL"

# URL передан, но не резолвится в валидный http(s)-адрес.
PSI_BAD_URL = "PSI_BAD_URL"

# Google вернул 429 -- документированный дневной/секундный лимит.
PSI_RATE_LIMITED = "PSI_RATE_LIMITED"

# Google вернул 500 -- недокументированный throttling по origin (тоже
# задокументированное поведение API, просто не в статус-коде спецификации).
PSI_THROTTLED = "PSI_THROTTLED"

# Google вернул иную ошибку (4xx/5xx, не 429/500) или сеть недоступна.
PSI_PROVIDER_ERROR = "PSI_PROVIDER_ERROR"

# Ответ Google пришёл, но его форма не совпала ни с одним ожидаемым полем --
# честный отказ вместо тихой потери данных (тот же принцип, что в
# magnific_client.py у Media Studio).
PSI_UNEXPECTED_RESPONSE = "PSI_UNEXPECTED_RESPONSE"

# Проверок ещё не было для этого сайта/URL -- нормальное состояние, не сбой.
PSI_NO_RUNS = "PSI_NO_RUNS"

# Запрошенный снимок/прогон не найден.
PSI_RUN_NOT_FOUND = "PSI_RUN_NOT_FOUND"

# Некорректный ввод настроек (например неизвестная стратегия/категория).
PSI_BAD_INPUT = "PSI_BAD_INPUT"

# Настройки не удалось сохранить.
PSI_STORAGE_FAILED = "PSI_STORAGE_FAILED"
