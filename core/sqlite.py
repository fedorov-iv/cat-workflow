import re
from functools import lru_cache


@lru_cache(maxsize=256)
def _like_regex(pattern, escape):
    parts = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == escape and i + 1 < len(pattern):
            parts.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        parts.append('.*' if char == '%' else '.' if char == '_' else re.escape(char))
        i += 1
    return re.compile(''.join(parts), re.IGNORECASE | re.DOTALL)


def unicode_like(pattern, value, escape=None):
    if pattern is None or value is None:
        return None
    return _like_regex(pattern, escape).fullmatch(str(value)) is not None


def register_unicode_like(sender, connection, **kwargs):
    # Встроенный LIKE в SQLite регистронезависим только для ASCII: icontains не находит «Глеб» по «гле».
    if connection.vendor == 'sqlite':
        connection.connection.create_function('like', 2, unicode_like)
        connection.connection.create_function('like', 3, unicode_like)
