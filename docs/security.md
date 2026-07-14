# Seguridad

## Cifrado en reposo

- Cada API key se cifra con **AES-256-GCM** (nonce aleatorio de 96 bits por operación, autenticado).
- La clave de datos se deriva del secreto maestro `ENCRYPTION_MASTER_KEY` mediante **HKDF-SHA256** (salt e info fijos versionados). El secreto maestro lo genera y custodia el secret manager de la plataforma (Render `generateValue`); nunca aparece en el repositorio.
- Formato de almacenamiento: `enc$v1$<nonce_b64>$<ciphertext_b64>` — el prefijo de versión permite rotar el esquema criptográfico con re-cifrado incremental.
- **Huella SHA-256** de la key para deduplicación e identificación sin descifrar.
- **Hint** (`…XXXX`, últimos 4 caracteres) como único dato visible en UI/logs.

## Autenticación y autorización

- Contraseñas con **argon2id** (pwdlib "recommended").
- **JWT** firmados (HS256, secreto del secret manager) con `type` explícito: access (30 min) y refresh (7 días). Un refresh token no sirve como access ni viceversa.
- **RBAC** jerárquico: `viewer < manager < admin`. La matriz completa está en el README. Guardas de invariantes: no se puede eliminar/degradar al último admin activo.
- Bootstrap seguro: el primer usuario (por variable de entorno o primer registro) es admin; después el registro queda restringido a admins.

## Superficie de exposición de las keys

La key en claro solo existe:
1. En memoria durante registro/validación/dispensación.
2. En la respuesta de `GET /api/v1/keys/dispense` (endpoint con rol `manager`+, rate-limited y auditado).

Nunca se registra en logs (logging estructurado sin cuerpos de petición), nunca se devuelve en listados y nunca sale por `/metrics`.

## Otras medidas

- **Rate limiting** por IP (slowapi): global, login y dispensación con límites independientes.
- **Auditoría inmutable**: actor, acción, recurso, IP y detalle de cada operación sensible (login, registro/rotación/revocación/dispensación de keys, cambios de usuarios y proyectos).
- **Cabecera `X-Request-ID`** y logging JSON correlacionado por petición.
- Contenedor **non-root**, imagen slim multi-stage, sin secretos en la imagen.
- CORS configurable; `--proxy-headers` para IPs reales tras el proxy de Render.
- Validación estricta de entrada con Pydantic (longitudes, formatos, prefijo `nvapi-`).

## Cumplimiento del servicio NVIDIA

- Única llamada saliente: `GET {NVIDIA_VALIDATION_URL}` (por defecto `https://integrate.api.nvidia.com/v1/models`), autenticada con la propia key — el mecanismo oficial y de solo lectura para comprobar validez.
- Sin scraping del portal, sin automatización de creación/rotación (no existe API oficial), sin compartición de keys entre cuentas: el sistema gestiona exclusivamente keys de **tu propia cuenta**.

## Recomendaciones operativas

- Rota `ENCRYPTION_MASTER_KEY` solo con procedimiento de re-cifrado (exportar → re-cifrar → importar).
- Usa contraseñas de 12+ caracteres y activa 2FA en GitHub/Render.
- Revisa la auditoría (`GET /api/v1/audit`) periódicamente.
- Configura keys con expiración en NVIDIA cuando sea posible y registra `expires_at` aquí para recibir el aviso `expiring_soon`.
