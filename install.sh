#!/bin/sh
# NIM Key Manager installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/BySergiMM/nim-key-manager/main/install.sh | sh
#
# What it does, in order:
#   1. detects the operating system and architecture
#   2. downloads a self-contained runtime into the private install home
#      (nothing global is touched, no system package is modified)
#   3. installs the newest release from GitHub Releases (PyPI or the main
#      branch as fallbacks)
#   4. writes a `nimkm` launcher and puts it on PATH
#   5. runs `nimkm init`: generates secrets, creates the database, adds an admin
#   6. offers to register the MCP server with the clients found on this machine
#
# Environment overrides:
#   NIMKM_VERSION=1.2.0        install a specific version instead of the latest
#   NIMKM_HOME=~/nimkm         where everything is installed (see below)
#   NIMKM_BIN_DIR=~/bin        where the launcher is written
#   NIMKM_SOURCE=/path/to/repo install from a local checkout (used by CI)
#   NIMKM_NO_MODIFY_PATH=1     do not touch shell profiles
#   NIMKM_NO_INIT=1            install only; skip configuration
#   NIMKM_NO_MCP_SETUP=1       do not offer to register with MCP clients
#   NIMKM_MCP_SETUP=1          register with detected MCP clients without asking
set -eu

REPO="BySergiMM/nim-key-manager"
PACKAGE="nim-key-manager"
PYTHON_VERSION="${NIMKM_PYTHON_VERSION:-3.12}"
UV_VERSION="${NIMKM_UV_VERSION:-latest}"

# --------------------------------------------------------------------------- #
# output helpers                                                               #
# --------------------------------------------------------------------------- #
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RED=$(printf '\033[31m')
    GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); CYAN=$(printf '\033[36m')
    RESET=$(printf '\033[0m')
else
    BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; CYAN=''; RESET=''
fi

step() { printf '%s::%s %s\n' "$CYAN" "$RESET" "$1"; }
ok()   { printf '%sOK%s  %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '%s!!%s  %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '%sXX%s  %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1; }

# --------------------------------------------------------------------------- #
# platform detection                                                           #
# --------------------------------------------------------------------------- #
detect_target() {
    os=$(uname -s)
    arch=$(uname -m)
    case "$os" in
        Linux)  os_part="unknown-linux-gnu" ;;
        Darwin) os_part="apple-darwin" ;;
        *)      die "unsupported operating system: $os (use the Docker image instead)" ;;
    esac
    case "$arch" in
        x86_64 | amd64)  arch_part="x86_64" ;;
        arm64 | aarch64) arch_part="aarch64" ;;
        *) die "unsupported architecture: $arch (use the Docker image instead)" ;;
    esac
    # Alpine and other musl systems need the musl build of uv.
    if [ "$os" = "Linux" ] && ! ldd /bin/sh 2>/dev/null | grep -q 'GNU C Library\|libc.so.6'; then
        if ldd --version 2>&1 | grep -qi musl; then
            os_part="unknown-linux-musl"
        fi
    fi
    echo "${arch_part}-${os_part}"
}

default_home() {
    case "$(uname -s)" in
        Darwin) echo "$HOME/Library/Application Support/nim-key-manager" ;;
        *)      echo "${XDG_DATA_HOME:-$HOME/.local/share}/nim-key-manager" ;;
    esac
}

download() {
    # download <url> <destination>
    if need curl; then
        curl -fsSL --retry 3 --connect-timeout 20 "$1" -o "$2"
    elif need wget; then
        wget -q --tries=3 --timeout=20 "$1" -O "$2"
    else
        die "neither curl nor wget is available"
    fi
}

fetch() {
    # fetch <url> -> stdout
    if need curl; then
        curl -fsSL --retry 3 --connect-timeout 20 "$1"
    elif need wget; then
        wget -q --tries=3 --timeout=20 "$1" -O -
    else
        die "neither curl nor wget is available"
    fi
}

# --------------------------------------------------------------------------- #
# uv (private, never installed system-wide)                                    #
# --------------------------------------------------------------------------- #
install_uv() {
    uv_bin="$RUNTIME_DIR/bin/uv"
    if [ -x "$uv_bin" ]; then
        echo "$uv_bin"; return 0
    fi
    target=$(detect_target)
    if [ "$UV_VERSION" = "latest" ]; then
        url="https://github.com/astral-sh/uv/releases/latest/download/uv-${target}.tar.gz"
    else
        url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${target}.tar.gz"
    fi
    step "downloading the runtime for ${target}" >&2
    mkdir -p "$RUNTIME_DIR/bin"
    tmp=$(mktemp -d)
    if ! download "$url" "$tmp/uv.tar.gz"; then
        rm -rf "$tmp"
        die "could not download uv from $url"
    fi
    tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
    found=$(find "$tmp" -type f -name uv -perm -u+x 2>/dev/null | head -n 1)
    [ -n "$found" ] || die "unexpected uv archive layout"
    mv "$found" "$uv_bin"
    chmod +x "$uv_bin"
    rm -rf "$tmp"
    echo "$uv_bin"
}

# --------------------------------------------------------------------------- #
# what to install                                                              #
# --------------------------------------------------------------------------- #
resolve_source() {
    if [ -n "${NIMKM_SOURCE:-}" ]; then
        echo "$NIMKM_SOURCE"; return 0
    fi
    if [ -n "${NIMKM_VERSION:-}" ]; then
        version="${NIMKM_VERSION#v}"
        api="https://api.github.com/repos/${REPO}/releases/tags/v${version}"
    else
        api="https://api.github.com/repos/${REPO}/releases/latest"
    fi
    wheel=$(fetch "$api" 2>/dev/null \
        | grep -o '"browser_download_url": *"[^"]*\.whl"' \
        | head -n 1 | sed 's/.*"\(https[^"]*\)"/\1/') || wheel=""
    if [ -n "$wheel" ]; then
        echo "$wheel"; return 0
    fi
    # No published release yet: try PyPI, then fall back to the main branch so
    # the installer works from day one.
    if [ -n "${NIMKM_VERSION:-}" ]; then
        echo "${PACKAGE}==${NIMKM_VERSION#v}"; return 0
    fi
    if fetch "https://pypi.org/pypi/${PACKAGE}/json" >/dev/null 2>&1; then
        echo "$PACKAGE"; return 0
    fi
    echo "https://github.com/${REPO}/archive/refs/heads/main.tar.gz"
}

# --------------------------------------------------------------------------- #
# PATH wiring                                                                  #
# --------------------------------------------------------------------------- #
add_to_path() {
    [ -z "${NIMKM_NO_MODIFY_PATH:-}" ] || return 0
    case ":$PATH:" in *":$BIN_DIR:"*) return 0 ;; esac

    line="export PATH=\"$BIN_DIR:\$PATH\"  # nim-key-manager"
    updated=""
    for profile in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
        [ -f "$profile" ] || continue
        grep -qF 'nim-key-manager' "$profile" 2>/dev/null && continue
        printf '\n%s\n' "$line" >> "$profile"
        updated="$updated $(basename "$profile")"
    done
    fish_config="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
    if [ -f "$fish_config" ] && ! grep -qF 'nim-key-manager' "$fish_config" 2>/dev/null; then
        printf '\nfish_add_path "%s"  # nim-key-manager\n' "$BIN_DIR" >> "$fish_config"
        updated="$updated config.fish"
    fi
    if [ -n "$updated" ]; then
        ok "added $BIN_DIR to PATH in:$updated"
        PATH_CHANGED=1
    else
        warn "could not update a shell profile; add this to yours:"
        printf '    %s\n' "$line"
    fi
}

# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #
NIMKM_HOME="${NIMKM_HOME:-$(default_home)}"
RUNTIME_DIR="$NIMKM_HOME/runtime"
VENV_DIR="$RUNTIME_DIR/venv"
BIN_DIR="${NIMKM_BIN_DIR:-$NIMKM_HOME/bin}"
PATH_CHANGED=0

printf '\n%sNIM Key Manager%s installer\n' "$BOLD" "$RESET"
printf '%s%s%s\n\n' "$DIM" "$NIMKM_HOME" "$RESET"

mkdir -p "$NIMKM_HOME" "$RUNTIME_DIR" "$BIN_DIR"
chmod 700 "$NIMKM_HOME" 2>/dev/null || true

UV=$(install_uv)

step "creating an isolated runtime"
"$UV" venv --python "$PYTHON_VERSION" --quiet "$VENV_DIR" \
    || die "could not create the runtime (try: NIMKM_HOME=~/nimkm sh install.sh)"

SOURCE=$(resolve_source)
case "$SOURCE" in
    *.whl)  step "installing the published release" ;;
    *.tar.gz) step "installing from the main branch (no release published yet)" ;;
    /*|./*) step "installing from $SOURCE" ;;
    *)      step "installing from PyPI" ;;
esac
"$UV" pip install --quiet --python "$VENV_DIR/bin/python" "$SOURCE" \
    || die "installation failed. Re-run with the output above for details."

"$VENV_DIR/bin/nimkm" --version >/dev/null 2>&1 \
    || die "the installed command does not run; report this at https://github.com/$REPO/issues"
VERSION=$("$VENV_DIR/bin/nimkm" --version | awk '{print $2}')
ok "installed nimkm $VERSION"

# Launcher: a three-line shim, so only `nimkm` lands on PATH (no stray python,
# pip or uvicorn executables) and the install location is remembered.
cat > "$BIN_DIR/nimkm" <<EOF
#!/bin/sh
: "\${NIMKM_HOME:=$NIMKM_HOME}"
export NIMKM_HOME
exec "$VENV_DIR/bin/nimkm" "\$@"
EOF
chmod +x "$BIN_DIR/nimkm"
ok "launcher at $BIN_DIR/nimkm"

add_to_path

NIMKM="$VENV_DIR/bin/nimkm"
export NIMKM_HOME

if [ -z "${NIMKM_NO_INIT:-}" ]; then
    printf '\n'
    "$NIMKM" init --no-input \
        || die "configuration failed; run '$BIN_DIR/nimkm doctor' to diagnose"
fi

# Registering with an MCP client edits the user's configuration, so it always
# asks. `curl | sh` leaves no stdin, but the controlling terminal is still there.
mcp_setup_done=0
if [ -z "${NIMKM_NO_MCP_SETUP:-}" ] && [ -z "${NIMKM_NO_INIT:-}" ]; then
    printf '\n'
    if [ -n "${NIMKM_MCP_SETUP:-}" ]; then
        "$NIMKM" mcp setup --yes && mcp_setup_done=1
    elif [ -r /dev/tty ]; then
        "$NIMKM" mcp setup < /dev/tty && mcp_setup_done=1
    fi
fi

printf '\n%s----------------------------------------------------------%s\n' "$DIM" "$RESET"
if [ "$PATH_CHANGED" = "1" ]; then
    printf 'Open a new terminal (or run %ssource ~/.profile%s), then:\n\n' "$BOLD" "$RESET"
else
    printf 'Next:\n\n'
fi
if [ "$mcp_setup_done" = "0" ]; then
    printf '    %snimkm mcp setup%s   connect it to your MCP client\n' "$BOLD" "$RESET"
fi
printf '    %snimkm doctor%s      check the installation\n' "$BOLD" "$RESET"
printf '    %snimkm web%s         administration dashboard (optional)\n' "$BOLD" "$RESET"
printf '    %snimkm --help%s      everything else\n\n' "$BOLD" "$RESET"
