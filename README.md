# Мониторинг существующего MySQL Replication + Orchestrator

Проект можно запускать с Agent `10.100.0.4` или с другого Ansible-контроллера, имеющего SSH-доступ ко всем четырём серверам. Inventory всегда подключается к Agent по SSH на `10.100.0.4`: запуск playbook на другой машине не должен устанавливать мониторинг на самом контроллере. Проект **не устанавливает MySQL или Orchestrator**, не меняет их конфигурацию, replication topology, Docker Compose, volumes и networks. На DB1-DB3 создаётся отдельный проект `monitoring-exporters`; на Agent создаётся проект `monitoring`. Существующие MySQL-контейнеры и сеть только читаются через `docker ps/inspect`. К существующей сети присоединяется исключительно новый `mysql-exporter`.

## Подготовка

На Ansible-контроллере должны быть Ansible и Python 3. На DB1-DB3 должны уже работать Docker CLI с Compose plugin и Docker daemon. Playbook **не устанавливает и не обновляет Docker на DB1-DB3**. Если Docker отсутствует на выделенном Agent, его можно установить из пакетов Ubuntu только после явного разрешения `monitoring_install_docker_on_agent=true`. Перед установкой роль проверяет отсутствие следов существующих Docker, containerd и Podman; при их наличии останавливается. У пользователя Ansible должен быть SSH-доступ ко всем четырём серверам и право `sudo`. Если `sudo` требует пароль, добавьте к команде `--ask-become-pass`; это пароль sudo DB1-DB3, он не заменяет пароль SSH. Для входа по SSH с паролем дополнительно нужен `--ask-pass`. Проверьте известные SSH host keys заранее. В `inventory/group_vars/all.yml` задан подтверждённый SSH-пользователь `furzik`; там же настраиваются ключ, адреса API Orchestrator, порты и `mysql_container_override`/`mysql_network_override` (можно задать отдельно в `inventory/host_vars/db1.yml` и т.д.). При запуске непосредственно на Agent также должен работать SSH-вход `furzik@10.100.0.4`.

Сначала отдельно проверьте доступ без изменений сервисов:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/access.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Если вход по ключу работает, уберите `--ask-pass`. Пароль SSH, пароль Vault и пароль sudo вводятся в разные приглашения. DB1-DB3 используют пароль sudo из `--ask-become-pass`. Для Agent Ansible отдельно запрашивает пароль sudo пользователя `furzik` без вывода введённых символов и проверяет его на `10.100.0.4` до развёртывания. При желании сохраните его как `vault_agent_become_password` в зашифрованном `inventory/group_vars/vault.yml`; тогда отдельного запроса не будет. Введённый при запросе пароль доступен только в текущем запуске Ansible. Если проверка Agent сообщает, что sudo не принял пароль, выполните на Agent под `furzik` команду `sudo -k && sudo -v` и введите тот же пароль.

Доступ к портам `9100`, `9104`, `9125`, `9090`, `3000` должен быть ограничен доверенной сетью `10.100.0.0/24` существующей сетевой политикой. Compose публикует порты только на адресе соответствующего сервера `10.100.0.x`, но маршрутизируемый частный адрес сам по себе не заменяет firewall. Playbook не меняет firewall. MySQL и Orchestrator API должны быть доступны с соответствующих узлов по существующим частным сетям; открывать их в интернет нельзя.

На **каждом** MySQL вручную создайте отдельную учётную запись exporter с лимитом соединений и документированными правами `mysqld_exporter` (пример, выполните вне Ansible после проверки адреса клиента). Если MySQL-контейнер использует Docker `host` network, exporter также запускается в `host` network: preflight выбирает доступный адрес из `127.0.0.1` и IP узла, а host-часть учётной записи должна разрешать выбранный адрес. В режиме отдельной Docker network используйте адрес exporter в этой сети:

```sql
CREATE USER 'exporter'@'<разрешённый адрес клиента>' IDENTIFIED BY '<секрет>' WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'<разрешённый адрес клиента>';
```

Эти команды **не выполняются проектом**. Для режима отдельной Docker network Docker может назначить exporter другой IP при пересоздании: DBA должен выбрать host-условие, ограниченное адресами этой сети, и проверить его после обновления. Не используйте глобальный `%`. Права основаны на [официальной документации mysqld_exporter](https://github.com/prometheus/mysqld_exporter#required-grants). Exporter выполняет `SHOW GLOBAL STATUS`, `SHOW GLOBAL VARIABLES` и `SHOW REPLICA/SLAVE STATUS`; запись в БД не предусмотрена. Если учётные данные или права неверны, Ansible останавливается после проверки `mysql_up`, не трогая MySQL. Пароль в Vault должен соответствовать созданной записи на всех трёх узлах; для разных паролей задайте переменную в отдельных `host_vars` Vault-файлах.

Создайте Vault:

```bash
cp inventory/group_vars/vault.example.yml inventory/group_vars/vault.yml
$EDITOR inventory/group_vars/vault.yml
ansible-vault encrypt inventory/group_vars/vault.yml
```

Для API с Basic Auth заполните в Vault `vault_orchestrator_api_user` и `vault_orchestrator_api_password`. При HTTP 401/403 используйте учётную запись существующего API Orchestrator; при таймауте проверьте сетевые ACL для Agent `10.100.0.4` к DB1-DB3 по TCP 3000. Этот playbook не меняет firewall БД и Orchestrator. Telegram включается `telegram_enabled: true` в `all.yml`; токен и chat ID задаются `vault_telegram_bot_token` и `vault_telegram_chat_id` в Vault. Grafana открывается по `http://10.100.0.4:3000`; для доступа из недоверенных сетей используйте существующий TLS reverse proxy и сетевые ACL.

### Telegram через VLESS только на Agent

Если Agent не достигает Telegram напрямую, настройте отдельный Xray-контейнер в проекте `monitoring`. HTTP-прокси доступен только внутри Docker-сети; порт на хосте не публикуется. Его правило отправляет `api.telegram.org` через VLESS, а остальные запросы выполняет напрямую. Grafana использует этот внутренний прокси для HTTPS; соединение с Prometheus исключено через `NO_PROXY`. Маршруты хоста, DB-серверы и существующие контейнеры MySQL/Orchestrator не меняются. Конфигурация поддерживает VLESS с `security=tls` и транспортами TCP, XHTTP, WebSocket, gRPC. Для `security=reality` поддерживаются TCP, XHTTP и gRPC: сочетание Reality + WebSocket сам Xray не поддерживает. Не поддерживаемая ссылка вызывает понятную ошибку до запуска прокси.

Быстрый интерактивный запуск: `./scripts/enable_telegram_vless.sh`. Скрипт откроет Vault, включит оба параметра Telegram и запустит `playbooks/telegram.yml`. Этот playbook работает только с Agent и не требует доступности DB1-DB3. Ниже те же шаги вручную.

1. Откройте зашифрованный файл: `ansible-vault edit inventory/group_vars/vault.yml`. Добавьте или заполните три значения, сохраняя кавычки вокруг полной VLESS-ссылки, включая параметры после `?` и имя после `#`:

   ```yaml
   vault_telegram_bot_token: 'ТОКЕН_БОТА'
   vault_telegram_chat_id: 'CHAT_ID'
   vault_telegram_vless_uri: 'vless://UUID@SERVER:PORT?security=reality&type=tcp&sni=HOST&fp=chrome&pbk=PUBLIC_KEY&sid=SHORT_ID#telegram'
   ```

   Вставляйте **свою полную ссылку VLESS** вместо примерной строки. Не публикуйте её в чате и не передавайте через `-e` в командной строке: она может попасть в историю shell и список процессов.

2. В `inventory/group_vars/all.yml` установите `telegram_enabled: true` и `telegram_vless_enabled: true`.
3. Примените Telegram/VLESS только на Agent:

   ```bash
   ansible-playbook -i inventory/hosts.yml playbooks/telegram.yml --ask-vault-pass --ask-pass --ask-become-pass
   ```

Роль проверяет формат VLESS, валидирует конфигурацию Xray и выполняет HTTPS-проверку `api.telegram.org` через прокси до завершения playbook. Для проверки доставки самого уведомления используйте **Test** у Telegram contact point в Grafana: сетевой тест без токена бота не подтверждает отправку сообщения. Ключ VLESS хранится в Ansible Vault и в `/opt/monitoring/agent/telegram-vless/config.json` на Agent с правами только для контейнера Xray.

Если Telegram contact point уже создан, обновляйте правила, маршрутизацию и текст уведомлений только на Agent:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/notifications.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Playbook сначала требует ровно одну Telegram integration с UID `mysql_cluster_telegram` и проверяет новый шаблон в Grafana без отправки уведомления. Затем он обновляет provisioning без добавления нового контакта и снова проверяет число integrations. Каждая реальная alert instance отправляется отдельным сообщением: правило, DB-узел и измеренное значение для CPU, RAM, диска, соединений и задержки. `DatasourceNoData` и `DatasourceError` сообщают только о проблеме получения метрик, без перечисления неподтверждённых аварий. Повторные предупреждения о диске и задержке не пересекаются с критическими порогами. Ссылки Grafana не включаются.

Шаблон формирует строку для каждого firing/resolved события. Для завершения информационного события используется короткое сообщение с названием правила; изменение уровня задержки или заполнения диска не называется восстановлением. Если список событий пуст, тест получает строку «Тест уведомления».

Лёгкие Node/MySQL метрики собираются и дашборды обновляются каждые 10 секунд. Orchestrator API опрашивается каждые 15 секунд. Правила Grafana проверяются каждые 10 секунд; новое уведомление отправляется без дополнительного ожидания группы, а изменение или восстановление — с интервалом 5 секунд. У всех 15 правил выдержка `for` составляет 5 секунд. Реальная доставка после сбоя займёт больше 5 секунд: нужно дождаться сбора метрики, следующей проверки правила и отправки через Telegram. Из-за короткой выдержки краткие сетевые сбои и всплески нагрузки могут давать больше ложных тревог. Для ошибок источника данных действуют отдельные интервалы маршрутизации.

Для применения этих интервалов только на Agent, включая проверку фактически принятой конфигурации Prometheus и дашборда Grafana:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/performance.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Этот запуск не подключается к DB1–DB3 и не управляет их контейнерами. Интервалы сбора увеличивают частоту только лёгких запросов к уже установленным exporters; при проблемах маршрута к DB-узлу графики этого узла останутся без новых данных до восстановления сети.

Если Grafana показывает `webhook response status 400 Bad Request` при тесте Telegram, проверьте реальный ответ Bot API через тот же VLESS-прокси:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/telegram_diagnostics.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Диагностика не меняет contact point и отправляет одно тихое тестовое сообщение. Она выводит статусы `getMe`, `getChat`, `sendMessage` и причину ошибки без токена и Chat ID. Если все три запроса успешны, примените `playbooks/notifications.yml` для установки `parse_mode: None` в существующей Telegram integration, затем повторите тест в Grafana. Пароли и токен не присылайте в чат.

Если сообщение из этой проверки приходит, а кнопка **Test** в Grafana всё ещё возвращает HTTP 400, проверьте отправку через сам процесс Grafana:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/telegram_grafana_diagnostics.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Этот playbook проверяет активный `HTTPS_PROXY`, затем отправляет одно короткое сообщение через тестовый API Grafana с реквизитами из Vault. Contact point и правила не меняются. Если такой тест проходит, отдельно проверяйте сохранённый токен и шаблон контакта; если не проходит, исследуйте путь от контейнера Grafana до Telegram.

Чтобы проверить именно **сохранённый токен** существующей integration с коротким сообщением, запустите тот же playbook с `-e telegram_diagnostic_use_stored_credentials=true`. Успех теста из Vault и ошибка теста сохранённых реквизитов указывают на проблему сохранённого токена или его применения Grafana. Каждый запуск отправляет максимум одно тестовое сообщение.

Для проверки **действующего шаблона** тем же API выполните команду с `-e telegram_diagnostic_use_active_template=true`. Это отправит одно сообщение с текущим шаблоном без изменения contact point.

Если короткий тест с сохранёнными реквизитами проходит, но кнопка **Test** в Grafana возвращает 400, проверьте рендеринг действующего шаблона на одном синтетическом событии. Эта проверка не отправляет сообщение в Telegram и не меняет контакт:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/telegram_template_render.yml --ask-vault-pass --ask-pass
```

Если тест действующего шаблона возвращает `400 bad request data`, это ответ на диагностический API-запрос; он не объясняет ошибку кнопки **Test**. Сразу после неудачного теста в интерфейсе проверьте записи об отправке в журнале Grafana:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/telegram_grafana_logs.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Playbook показывает только время и распознанную причину: токен, Chat ID, URL и исходные строки журнала скрыты. Если Grafana записала лишь HTTP 400 без ответа Telegram, отчёт прямо укажет, что подробность отсутствует; угадывать причину по одному статусу нельзя.

Ошибка `Decryption failed` при запуске Ansible означает, что введённый пароль не расшифровал `vault.yml`; повторите ввод того же пароля, с которым файл редактировался через `ansible-vault edit`. Ошибка `No route to host` для DB1 относится к сети/SSH и блокирует общий `update.yml`; для настройки только Telegram используйте `telegram.yml`. Ни одна из этих ошибок не требует изменения MySQL или Orchestrator.

Если все три запроса API с Agent завершаются таймаутом, сначала проверьте локально на каждом DB-узле доступность его API по `http://10.100.0.x:3000/api/masters` (HTTP 401 означает, что API работает, но нужна Basic Auth). Затем проверьте правила межсетевого экрана. Для UFW узкое **ручное** разрешение доступа только от Agent на каждом DB-узле выглядит так:

```bash
sudo ufw allow from 10.100.0.4 to any port 3000 proto tcp
```

Применяйте его лишь после проверки действующей сетевой политики. Ansible не запускает эту команду. С Agent проверьте `curl --connect-timeout 3 -sS -o /dev/null -w '%{http_code}\n' http://10.100.0.1:3000/api/masters` и аналогично для `.2` и `.3`. HTTP 401/403 после открытия доступа требует указать существующие API credentials в Vault; HTTP 200 позволяет продолжить развёртывание.

Prometheus на Agent опрашивает Node Exporter и MySQL Exporter на DB1-DB3 по TCP 9100 и 9104. Если exporters отвечают локально на DB-узлах, а Prometheus показывает `context deadline exceeded`, проверьте сетевую политику и вручную разрешите доступ **только от Agent** на каждом DB-узле. Для UFW после проверки действующих правил:

```bash
sudo ufw allow from 10.100.0.4 to any port 9100 proto tcp
sudo ufw allow from 10.100.0.4 to any port 9104 proto tcp
```

С Agent проверьте `/metrics` на обоих портах каждого DB-узла. Ansible не изменяет firewall.

## Команды

Полное развёртывание:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/site.yml --ask-vault-pass
```

Для текущего входа по SSH с паролем и sudo на DB1-DB3:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/site.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Если на Agent нет Docker и вы подтвердили, что это выделенный хост без существующего container runtime, разрешите одноразовую установку Docker только на Agent:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/site.yml --ask-vault-pass --ask-pass --ask-become-pass -e monitoring_install_docker_on_agent=true
```

После установки повторные запуски не переустанавливают Docker. Если обнаружены данные старого runtime, установка остановится; не удаляйте их автоматически.

Проверка состояния без изменений:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/check.yml --ask-vault-pass
```

Безопасное обновление **только мониторинга** после изменения фиксированных версий образов или конфигурации:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/update.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Обновление DB exporters идёт последовательно (`serial: 1`). Команды Docker Compose адресованы лишь проектам `monitoring` и `monitoring-exporters` и конкретным новым сервисам. В них нет `down`, `--remove-orphans`, удаления volumes или команд управления существующим кластером.

Если один DB-узел недоступен после failover, обновите только отображение на Agent. Эта команда не подключается к DB1-DB3 и допускает временно недоступные targets:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/failover_view.yml --ask-vault-pass --ask-pass --ask-become-pass
```

Локальные проверки проекта:

```bash
python3 scripts/check_config.py
bash scripts/qa.sh
```

Разбор повторяющихся Telegram уведомлений находится в `NOTIFICATION_AUDIT.md`. Для проверки фактических contact points и маршрутов запущенной Grafana без вывода токена бота:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/notification_audit.yml --ask-vault-pass --ask-pass
```

Если в contact point `telegram` накопились две Telegram integrations для одного чата, удалите только лишнюю. Playbook сохраняет integration с UID `mysql_cluster_telegram`, сверяет оба chat ID с Vault и после удаления проверяет, что осталась одна. При несовпадении адресатов ничего не удаляет:

```bash
ansible-playbook -i inventory/hosts.yml playbooks/telegram_dedupe.yml --ask-vault-pass --ask-pass
```

`qa.sh` выполняет syntax-check playbooks с тестовым файлом `vault.example.yml` вместо настоящего зашифрованного Vault и `ansible-lint`, если пакет установлен. `check_config.py` рендерит шаблоны с тестовыми переменными, разбирает YAML/JSON, проверяет подключение Agent по SSH, загрузку Vault всеми plays, шесть дашбордов, 15 правил и запрещённые мутации в tasks. Эти проверки не заменяют финальную проверку на реальных серверах.

## Что создаётся

| Узел | Контейнеры | Данные |
|---|---|---|
| DB1-DB3 | `monitoring-node-exporter`, `monitoring-mysql-exporter` | `/opt/monitoring/db-exporters` |
| Agent | `monitoring-node-exporter`, `monitoring-orchestrator-exporter`, `monitoring-prometheus`, `monitoring-grafana` | `/opt/monitoring/agent` |

Prometheus опрашивает 9 targets каждые 15 секунд и сохраняет TSDB 15 дней или до 20 GB. Оба лимита меняются в `all.yml`. Grafana хранит данные в bind mount. Дашборды и правила находятся под provisioning; ручные правки provisioned ресурсов в UI не сохраняются. На дашбордах текущий Primary определяется по единственному доступному для записи MySQL (`read_only=0`, `mysql_up=1`, target UP); при нуле или нескольких кандидатах показывается «НЕТ ДАННЫХ». Топология поступает из `/api/cluster/{hint}`. После failover повторный запуск Ansible не нужен. API exporter сравнивает `/api/masters` всех доступных Orchestrator и не считает пустой или противоречивый ответ достоверным Primary. Недоступные и несовместимые endpoints показываются как Unknown/Error.

Шесть дашбордов имеют общую навигацию, разделы состояния и истории, цветовые пороги и выбор DB-узлов. В панелях ресурсов Infrastructure и Monitoring Health отображаются только DB1-DB3. Предупреждения по CPU, RAM и диску также относятся только к DB1-DB3. Node Exporter на Agent остаётся техническим target Prometheus для проверки самого мониторинга, но Agent не входит в показатели серверов БД.

Если Orchestrator сообщает hostnames вместо IP, exporter сопоставляет короткие имена `db1`-`db3`; при других именах настройте inventory/`host_vars` и адаптируйте отображение перед запуском. `orchestrator_cluster_hint` позволяет выбрать кластер, когда API показывает несколько кластеров. Для этого окружения проверяется ровно одна Primary среди DB1-DB3.

Итоговый validation запрашивает Prometheus, все 9 targets, `mysql_up` трёх узлов, версию, `read_only`, текущую Primary, топологию, API Orchestrator, datasource Grafana, шесть дашбордов и 15 правил. Он печатает отчёт и завершает playbook с ошибкой при неполном результате. **Проект не проводит failover и не останавливает MySQL для тестов.**

## Ограничения и диагностика

- Доступность `mysql_up=1` и наличие репликационных метрик зависят от заранее выданных учётных данных и версии MySQL. `SHOW REPLICA STATUS` на Primary закономерно не даёт строк.
- `orchestrator_last_failover_timestamp_seconds` зависит от того, отдаёт ли установленная версия историю через `/api/audit-recovery`. Если endpoint недоступен, `orchestrator_endpoint_success{endpoint="audit_recovery"}=0` и время последнего failover отсутствует.
- Telegram выключен по умолчанию. Без него правила видны в Grafana; для внешней доставки включите Telegram и проверьте доставку обычным тестовым уведомлением Grafana без воздействия на БД.
- Пароль администратора из Vault используется при **первом** создании Grafana. Позднейшая смена Vault сама по себе не меняет пароль уже существующего пользователя в БД Grafana; смену выполняйте штатным интерфейсом Grafana.
- Если preflight обнаружил более одного кандидата MySQL или более одной подходящей Docker network, задайте точные `mysql_container_override` и `mysql_network_override`. Проект не выбирает цель наугад.

Для диагностики: `docker logs monitoring-mysql-exporter`, `docker logs monitoring-orchestrator-exporter`, `docker logs monitoring-grafana`, Prometheus `/targets` и панель Monitoring Health.
# ansible-grafanaForMysqlCluster
