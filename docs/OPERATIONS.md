# Эксплуатация

## Данные и резервное копирование

База находится в `data/story-runner.sqlite3`. SQLite работает в WAL-режиме, поэтому для корректной резервной копии лучше остановить контейнер:

```bash
docker compose stop
cp data/story-runner.sqlite3 data/story-runner.backup.sqlite3
docker compose start
```

Восстановление выполняется заменой файла базы при остановленном контейнере.

## Обновление

```bash
docker compose down
docker compose up --build -d
```

Миграции применяются автоматически перед запуском Gunicorn.

## Будущее размещение рядом с другим Django-проектом

Story Runner остаётся отдельным процессом и слушает собственный внутренний порт. Reverse proxy направляет выбранный домен или путь на Story Runner, а существующий Django-проект продолжает работать на своём адресе. Перед этим нужно:

1. назначить отдельное имя хоста или URL-префикс;
2. настроить TLS и reverse proxy;
3. задать `DEBUG=0`, безопасные секреты, `ALLOWED_HOSTS` и `CSRF_TRUSTED_ORIGINS`;
4. ограничить прямой доступ к внутреннему порту;
5. настроить регулярное резервное копирование каталога `data`.

Для текущего размещения используется `story.zhizhka.ru`. Контейнер публикует
порт `8001` только на loopback-интерфейсе VPS и одновременно подключается к
общей Docker-сети `zhizhka-web`. Внешний nginx обращается к нему по имени
`story-runner:8000`. Сеть объявлена как `external: true`: Compose не создаёт её
и не считает частью жизненного цикла Story Runner. Подготовьте её один раз:

```bash
docker network inspect zhizhka-web >/dev/null 2>&1 || docker network create zhizhka-web
```

Production-переменные:

```dotenv
DEBUG=0
SECRET_KEY=<случайный секрет>
ADMIN_PASSWORD=<отдельный пароль панели>
ALLOWED_HOSTS=story.zhizhka.ru,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://story.zhizhka.ru
```

Проверка после обновления:

```bash
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8001/healthz/
docker compose config | grep -A8 'zhizhka-web'
```

## GitHub Pages

GitHub Pages публикует статические HTML-, CSS- и JavaScript-файлы. Story Runner нельзя разместить там целиком: сервису нужны Django, SQLite, серверные POST-запросы, транзакции и общая база данных. GitHub Pages можно использовать только для отдельной статической витрины.

Для рабочего сервиса нужен хост с постоянным Python-процессом и диском. В текущей схеме проще всего запустить Docker-контейнер на существующем сервере и выделить ему поддомен, например `story.zhizhka.ru`.

## Превью ссылок

Каждая публичная страница прогона отдаёт Open Graph- и Twitter Card-метаданны, а также PNG-карточку 1200 × 630. Для работы превью адрес должен быть пубичным, доступным без авторизации и работать по HTTPS. `localhost` и закрытый внутренний адрес Telegram открыть не сможет.
