# nomer2

## Быстрый Git workflow (Windows)

Если работаешь только в ветке `main`, используй этот минимальный цикл.

### 1) Получить последние изменения из GitHub
```bat
git checkout main
git pull origin main
```

### 2) Отправить свои изменения в GitHub
```bat
git checkout main
git add -A
git commit -m "update"
git push origin main
```

### 3) Если `nothing to commit` / `Everything up-to-date`
Это значит, что новых локальных изменений нет и GitHub уже синхронизирован.

## Однокнопочный скрипт

В корне проекта есть `sync_main.bat`.

- Запуск: двойной клик по файлу **или** в терминале:
```bat
sync_main.bat
```
- Скрипт делает: `checkout main` → `pull` → `add` → `commit` (если есть изменения) → `push`.
