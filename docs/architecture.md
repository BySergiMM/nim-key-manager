# Arquitectura

## Visión general

NIM Key Manager sigue **Clean Architecture pragmática** con cuatro capas y dependencias apuntando hacia dentro:

```
        ┌─────────────────────────────────────────┐
        │ api/ (FastAPI) · dashboard/ · tasks/    │  ← Interfaz / entrega
        ├─────────────────────────────────────────┤
        │ application/services + interfaces       │  ← Casos de uso y puertos
        ├─────────────────────────────────────────┤
        │ domain/ (enums, excepciones)             │  ← Reglas y vocabulario
        ├─────────────────────────────────────────┤
        │ infrastructure/ (SQLAlchemy, NVIDIA)     │  ← Adaptadores
        └─────────────────────────────────────────┘
```

- **domain**: vocabulario del negocio (roles, estados de key, acciones de auditoría) y excepciones. Sin dependencias externas.
- **application**: los casos de uso (`KeyService`, `AuthService`, `ProjectService`, `UserService`, `StatsService`, `AuditService`). Acceden a la persistencia a través de repositorios y a NVIDIA a través del puerto `KeyValidator` (Protocol), lo que permite sustituirlo por un fake en tests.
- **infrastructure**: adaptadores concretos — repositorios SQLAlchemy 2 async y `NvidiaKeyValidator` (httpx).
- **api**: FastAPI traduce HTTP ⇄ casos de uso. Las excepciones de dominio se mapean a códigos HTTP en un único lugar (`main.py`), por lo que los routers no contienen manejo de errores.

### Decisiones relevantes

1. **Entidades = modelos ORM.** En lugar de duplicar cada entidad como dataclass + modelo + mapeadores, los servicios operan sobre los modelos SQLAlchemy y los routers los convierten a schemas Pydantic. Se mantiene la dirección de dependencias (la API nunca consulta la BD directamente; los servicios nunca importan FastAPI) con la mitad de código. Es el equilibrio estándar para servicios de este tamaño.
2. **Rotación asistida, no automática.** NVIDIA Build no expone API oficial de creación/rotación de keys; automatizar el portal violaría los ToS. `POST /keys/{id}/rotate` hace el swap atómico y auditado una vez que el operador pega la key nueva. El puerto `KeyValidator` deja el punto de extensión listo si NVIDIA publica una API oficial de gestión.
3. **Dispensación LRU.** `GET /keys/dispense` selecciona la key activa no expirada menos recientemente usada (`last_used_at NULLS FIRST, usage_count`), lo que reparte el uso entre keys y respeta los rate limits del free tier de NVIDIA.
4. **Fingerprint SHA-256** de cada key para detectar duplicados sin necesidad de descifrar el inventario.
5. **Migraciones Alembic como fuente de verdad** en producción (`AUTO_CREATE_TABLES=false`); `create_all` solo se usa en tests/desarrollo.
6. **Factory pattern** (`create_app()`): la app se construye por proceso (uvicorn `--factory`), lo que facilita tests aislados y evita estado global.

## Modelo de datos

```
users 1─────* api_keys *─────1 projects
                 │  └── rotated_from_id (linaje de rotación, self-FK)
                 └────* usage_records
audit_logs (append-only, sin FKs para sobrevivir a borrados)
```

## Flujos principales

**Registro de key**: valida formato `nvapi-` → (opcional) validación remota contra NVIDIA → cifrado AES-GCM → fingerprint → auditoría → commit.

**Dispensación**: selección LRU → descifrado → actualización de uso → `usage_record` → auditoría. Única respuesta de la API que contiene la key en claro.

**Expiración**: job horario (APScheduler) + endpoint de mantenimiento marcan `expired` las keys vencidas; `expiring_soon` avisa con `EXPIRY_WARNING_DAYS` de antelación.

**Sweep de validación**: cada `VALIDATION_INTERVAL_HOURS` se valida cada key activa contra NVIDIA; las rechazadas pasan a `invalid` (y se recuperan a `active` si vuelven a validar).
