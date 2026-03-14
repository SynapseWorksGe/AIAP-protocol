# AIAP Meeting Protocol Service

Сервис для автоматической расшифровки аудиозаписей встреч с генерацией саммари, списка задач и PDF-отчётов.

## Архитектура

```
Audio File → API → S3 → Yandex STT → Claude Sonnet → TXT + PDF → S3
```

**Стек:**
- **API**: FastAPI + Uvicorn
- **STT**: Yandex SpeechKit (long-running recognition)
- **AI**: Claude Sonnet (саммари, задачи, редактура расшифровки)
- **PDF**: fpdf2 + DejaVu Sans (Unicode/Кириллица)
- **Storage**: S3 (Yandex Object Storage или совместимое)

## API Endpoints

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/v1/meetings/transcribe` | Загрузить аудио для обработки |
| `GET` | `/api/v1/meetings/status/{job_id}` | Статус и результаты задачи |
| `GET` | `/api/v1/meetings/jobs` | Список всех задач |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

## Установка (Docker Compose — рекомендуется)

Подходит для VPS с несколькими микросервисами. Nginx работает как reverse proxy.

```bash
# 1. Подготовить VPS (Docker, UFW, swap) — один раз
sudo bash deploy/bootstrap-vps.sh

# 2. Склонировать проект
cd /opt/services
git clone <repo-url> aiap-protocol
cd aiap-protocol/deploy

# 3. Настроить credentials
cp envs/aiap-protocol.env.example envs/aiap-protocol.env
nano envs/aiap-protocol.env

# 4. Запустить всё
docker compose up -d --build
```

Сервис будет доступен по `http://<IP>/aiap/`

### Добавить новый микросервис

1. Добавить секцию в `deploy/docker-compose.yml`
2. Добавить `location` в `deploy/nginx/default.conf`
3. Создать `.env` файл в `deploy/envs/`
4. `docker compose up -d --build`

### Управление (Docker)

```bash
cd /opt/services/aiap-protocol/deploy

docker compose up -d --build   # Собрать и запустить
docker compose ps              # Статус контейнеров
docker compose logs -f aiap    # Логи сервиса
docker compose restart nginx   # Перезапустить nginx
docker compose down            # Остановить всё
```

## Установка (systemd — альтернатива)

```bash
git clone <repo-url> /tmp/aiap-protocol
cd /tmp/aiap-protocol
sudo bash scripts/install.sh
sudo nano /opt/aiap-protocol/.env
sudo systemctl start aiap-protocol
```

## Конфигурация (.env)

| Переменная | Описание |
|-----------|----------|
| `YANDEX_API_KEY` | API-ключ Yandex Cloud |
| `YANDEX_FOLDER_ID` | ID каталога Yandex Cloud |
| `ANTHROPIC_API_KEY` | API-ключ Anthropic (Claude) |
| `S3_ENDPOINT_URL` | URL S3 хранилища |
| `S3_ACCESS_KEY` | Access key для S3 |
| `S3_SECRET_KEY` | Secret key для S3 |
| `S3_BUCKET_NAME` | Имя бакета S3 |
| `APP_HOST` | Хост (по умолчанию `0.0.0.0`) |
| `APP_PORT` | Порт (по умолчанию `8000`) |
| `MAX_AUDIO_SIZE_MB` | Макс. размер аудиофайла в МБ |

## Использование

### Загрузить аудио для обработки

```bash
curl -X POST http://localhost:8000/api/v1/meetings/transcribe \
  -F "file=@meeting.ogg" \
  -F "language=ru-RU"
```

Ответ:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Задача создана, обработка начата"
}
```

### Проверить статус

```bash
curl http://localhost:8000/api/v1/meetings/status/550e8400-e29b-41d4-a716-446655440000
```

Ответ (после завершения):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "transcript_url": "https://storage.yandexcloud.net/bucket/meetings/.../transcript.txt",
  "summary_txt_url": "https://storage.yandexcloud.net/bucket/meetings/.../summary.txt",
  "summary_pdf_url": "https://storage.yandexcloud.net/bucket/meetings/.../meeting_report.pdf",
  "tasks_url": "https://storage.yandexcloud.net/bucket/meetings/.../tasks.txt"
}
```

## Структура деплоя

```
deploy/
├── docker-compose.yml              # Все сервисы + Nginx
├── bootstrap-vps.sh                # Подготовка VPS (Docker, UFW, swap)
├── nginx/
│   └── default.conf                # Маршрутизация по путям (/aiap/, /service2/, ...)
└── envs/
    ├── aiap-protocol.env.example   # Шаблон
    └── aiap-protocol.env           # Реальные credentials (в .gitignore)
```

## Поддерживаемые форматы аудио

`.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`, `.opus`, `.webm`
