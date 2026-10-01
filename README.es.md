# NIM Key Manager (resumen en español)

> La documentación principal está en inglés: [`README.md`](README.md).

Gestor **self-host y listo para producción** de las API Keys de **tu propia** cuenta de NVIDIA Build/NIM: almacenamiento cifrado, rotación asistida, detección de expiraciones, estadísticas, proyectos, RBAC, auditoría, dashboard web y **conector para Claude (MCP)**. Despliega tu propia instancia en minutos; todo se configura por variables de entorno.

> **No afiliado con NVIDIA.** Es un proyecto independiente de código abierto: no está afiliado, respaldado ni patrocinado por NVIDIA Corporation. "NVIDIA", "NVIDIA Build" y "NIM" se usan solo para indicar con qué servicio funciona el software; los nombres de productos y empresas pertenecen a sus respectivos dueños.

> **Términos de NVIDIA.** NVIDIA Build **no ofrece API pública** para crear/rotar keys, y las keys **no se pueden compartir ni redistribuir** a terceros. Por eso este proyecto está pensado para que **tú gestiones tus propias keys en tu propia instancia**: la única llamada saliente es la **validación** oficial de solo lectura (`GET https://integrate.api.nvidia.com/v1/models`). No es un servicio para repartir tus keys a otras personas.

## Despliega el tuyo

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/BySergiMM/nim-key-manager)

1. Pulsa el botón (o **Use this template**) y en [Render](https://render.com) elige **New → Blueprint**. `render.yaml` aprovisiona solo la base de datos PostgreSQL, el servicio Docker y los secretos.
2. Introduce `FIRST_ADMIN_EMAIL` y `FIRST_ADMIN_PASSWORD` (tu admin inicial; son obligatorias: ninguna petición HTTP puede crear al primer administrador).
3. Cada push a `main` ejecuta CI y Render lo redespliega cuando todos los checks pasan (un commit que falla no se despliega). Las migraciones corren al arrancar.

## Conector para Claude (MCP)

El servicio expone un servidor MCP en `‹BASE›/mcp` para añadirlo a **Claude como custom connector** (OAuth 2.1 con GitHub/Google). Es opcional: hasta que definas las credenciales OAuth, `/mcp` queda apagado y el resto del servicio funciona con normalidad. Guía completa: [`docs/connector.md`](docs/connector.md).

1. Crea una **OAuth App** en GitHub con callback `‹BASE›/auth/callback`.
2. En Render define `MCP_GITHUB_CLIENT_ID`, `MCP_GITHUB_CLIENT_SECRET` y `MCP_ALLOWED_IDENTITIES` (tu id numérico de GitHub y/o tu email, no el login: `curl -s https://api.github.com/users/TU-LOGIN | jq .id`; los usuarios que crea el conector son `viewer` salvo que fijes `MCP_DEFAULT_ROLE`).
3. En Claude: **Settings → Connectors → Add custom connector** → URL `‹BASE›/mcp` → **Connect**.

## Más

Características, arquitectura, roles, seguridad y desarrollo: consulta el [`README.md`](README.md) en inglés y la carpeta [`docs/`](docs/). Licencia MIT.
