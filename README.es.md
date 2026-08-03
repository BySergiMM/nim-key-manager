<div align="center">

# NIM Key Manager

**Un servidor MCP para las API Keys de _tu propia_ cuenta de NVIDIA Build/NIM.**

Le pides una key a Claude y te la da: cifrada en reposo, rotable, y con cada acceso auditado.

> La documentación completa está en inglés: [`README.md`](README.md).

</div>

## Instalación

**Linux / macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.ps1 | iex
```

El instalador detecta Claude Code y **te pide permiso** para registrar el servidor (hace
copia de seguridad del fichero antes de tocarlo). Dices que sí, reinicias Claude Code y
ya está.

¿Ya lo tenías instalado, o dijiste que no?

```bash
nimkm mcp setup
```

Eso es todo: no hay entorno que instalar, ni base de datos que aprovisionar, ni fichero
de configuración que escribir, ni servidor que mantener encendido.

## Y ya puedes pedirlo

> **«lista mis keys de NVIDIA»**
> **«dame una key NIM para este script»**
> **«registra esta key que acabo de crear en build.nvidia.com»**
> **«rota la key prod-1, la nueva es esta»**
> **«¿qué keys me caducan este mes?»**
> **«¿quién ha usado mis keys esta semana?»**

Claude elige la herramienta, el servidor comprueba los permisos y escribe la auditoría.
Son 19 herramientas: alta, validación, rotación, revocación, dispensado (LRU), proyectos,
estadísticas y registro de auditoría.

## Otros clientes MCP

`nimkm mcp setup` conoce Claude Code (ámbito de usuario y de proyecto) y Claude Desktop.
Para cualquier otro cliente, imprime la declaración y pégala:

```bash
nimkm mcp setup --print
```

| Opción | Efecto |
|---|---|
| `--scope user` | Claude Code, todos tus proyectos (`~/.claude.json`) |
| `--scope project` | solo este repositorio (`./.mcp.json`, se puede versionar) |
| `--scope desktop` | Claude Desktop |
| `--yes` | sin preguntar |
| `--print` | imprime el JSON y no toca nada |

`nimkm mcp status` dice dónde está registrado y `nimkm mcp remove` lo deshace. **Antes de
cada escritura se guarda una copia con marca de tiempo.**

## El dashboard (opcional)

```bash
nimkm web            # http://127.0.0.1:8000
```

Interfaz web y API REST para lo que un chat lleva mal: revisar la auditoría, gestionar
cuentas o conectar una aplicación a `/api/v1/keys/dispense`. Está apagado salvo que lo
arranques; el servidor MCP no lo necesita.

## Comandos

```bash
nimkm mcp setup           # conectar con tu cliente MCP (el único que necesitas)
nimkm mcp status          # dónde está registrado, y con qué nombre
nimkm mcp serve           # el servidor en sí -- lo lanza tu cliente, no tú
nimkm doctor              # diagnóstico completo, con el comando que arregla cada cosa
nimkm web                 # dashboard + API REST
nimkm admin reset-password --email tu@correo.com
nimkm update              # actualizar
nimkm uninstall --purge   # borrarlo todo
```

## Acceso remoto (avanzado)

Todo lo anterior es local: tu cliente lanza el servidor como proceso hijo y no se abre
ningún puerto. Si quieres que Claude en la web o en el móvil llegue a la **misma**
instancia, despliégala y usa el conector HTTP con OAuth:

```bash
docker run -d -p 8000:8000 -v nimkm:/data ghcr.io/bysergimm/nim-key-manager
nimkm mcp oauth      # OAuth de GitHub/Google para el endpoint /mcp
```

O un clic en Render para tener PostgreSQL gestionado y una URL HTTPS. Guía completa:
[`docs/connector.md`](docs/connector.md).

> **Términos de NVIDIA.** NVIDIA Build **no ofrece API pública** para crear o rotar keys,
> y las keys **no se pueden compartir ni redistribuir** a terceros. Este proyecto sirve
> para que **gestiones tus propias keys en tu propia máquina**: la única llamada saliente
> es la validación oficial de solo lectura
> (`GET https://integrate.api.nvidia.com/v1/models`). No automatiza ni hace scraping del
> portal de NVIDIA y **no** es un servicio para repartir tus keys a otras personas.

## Dónde se guarda todo

| | Ruta |
|---|---|
| **Linux** | `~/.local/share/nim-key-manager` |
| **macOS** | `~/Library/Application Support/nim-key-manager` |
| **Windows** | `%LOCALAPPDATA%\nim-key-manager` |

Dentro: `config.env` (secretos, permisos 600), `data/nimkm.db` (tus keys cifradas),
`runtime/` (el entorno autocontenido) y `bin/nimkm` (lo que ejecuta tu cliente MCP).
`NIMKM_HOME` lo cambia. Para mudarte de máquina, copia la carpeta entera: los datos no
sirven de nada sin `ENCRYPTION_MASTER_KEY`.

## Problemas frecuentes

Empieza siempre por `nimkm doctor`.

| Síntoma | Solución |
|---|---|
| Claude no ve las herramientas | Reinicia el cliente: los servidores MCP se leen al arrancar. Luego `nimkm mcp status`. |
| `nimkm: command not found` | El PATH solo cambia en terminales nuevas. Abre otra o `source ~/.profile`. |
| `irm … \| iex` falla en Windows | `Set-ExecutionPolicy -Scope Process Bypass` antes de ejecutarlo. |
| El cliente marca el servidor como fallido | Ejecuta `nimkm mcp serve` a mano: debe quedarse esperando en silencio. Los errores salen por stderr. |
| Responde otra instalación | `nimkm mcp status` avisa si la entrada apunta a otro sitio; `nimkm mcp setup` la corrige. |
| «not valid JSON … refusing to overwrite» | El fichero de configuración de tu cliente está corrupto. Arréglalo o muévelo: la herramienta nunca reescribe un fichero que no puede parsear. |
| `port 8000 is already in use` | Solo afecta a `nimkm web`: `nimkm web --port 8001`. |
| El contenedor pierde los datos | Faltaba el volumen: `-v nimkm:/data`. |

`NIMKM_DEBUG=1 nimkm <comando>` muestra la traza completa.

## Preguntas rápidas

**¿Qué necesito instalado?** Nada: el instalador trae un entorno autocontenido.

**¿Está corriendo todo el rato?** No. Tu cliente MCP lo arranca cuando lo necesita y lo
para al cerrarse.

**¿Es seguro por stdio sin login?** El transporte *es* la frontera: el servidor corre como
hijo de tu propio cliente, con tus permisos, y solo se le llega por esa tubería. El caso
que sí necesita OAuth es el acceso remoto, y lo tiene.

**¿Dónde acaban mis keys?** En una base de datos local, cifradas con AES-256-GCM usando un
secreto generado en tu máquina durante la instalación.

## Más

Arquitectura, seguridad, despliegue y el rediseño de la instalación:
[`README.md`](README.md), la carpeta [`docs/`](docs/) e
[`INSTALLATION_REDESIGN.md`](INSTALLATION_REDESIGN.md). Licencia MIT.
