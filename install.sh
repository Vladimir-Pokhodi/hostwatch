#!/usr/bin/env bash
# hostwatch installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Vladimir-Pokhodi/hostwatch/v1.1.0/install.sh -o install.sh
#   sudo bash install.sh
#
# Installs Docker if it is missing, creates /opt/hostwatch with docker-compose.yml,
# Caddyfile and .env, and starts the containers. Nothing else in the system changes:
# no SSH settings, firewall rules or users.
#
# Answers can be given in the environment instead of prompts: BOT_TOKEN, OWNER_ID,
# HOSTWATCH_DOMAIN, HOSTWATCH_IP. HOSTWATCH_DIR changes the folder, HOSTWATCH_BASE_URL the
# place the files are downloaded from.

set -euo pipefail

REF="v1.1.0"
DIR="${HOSTWATCH_DIR:-/opt/hostwatch}"
BASE_URL="${HOSTWATCH_BASE_URL:-https://raw.githubusercontent.com/Vladimir-Pokhodi/hostwatch/$REF}"
FILES=(docker-compose.yml Caddyfile .env.example)
# The data folder belongs to the user the bot runs as in its container.
CONTAINER_UID=10001
MIN_FREE_GB=6

BOT_NAME=""

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# Prompts work in a terminal, and also with `curl ... | sudo bash` through /dev/tty.
if [ -t 0 ]; then
    TTY=/dev/stdin
elif (exec </dev/tty) 2>/dev/null; then
    TTY=/dev/tty
else
    TTY=""
fi

ask() { # ask <prompt> [default]; the answer goes to stdout
    local answer
    [ -n "$TTY" ] || die "no terminal for questions; set the answers in the environment (see the top of install.sh)"
    if [ -n "${2:-}" ]; then
        printf '%s [%s]: ' "$1" "$2" >&2
    else
        printf '%s: ' "$1" >&2
    fi
    IFS= read -r answer <"$TTY" || die "no answer, the input has ended"
    printf '%s' "${answer:-${2:-}}"
}

check_system() {
    [ "$(id -u)" -eq 0 ] || die "run as root: sudo bash install.sh"
    [ "$(uname -s)" = Linux ] || die "hostwatch runs on Linux"
    case "$(uname -m)" in
        x86_64 | amd64 | aarch64 | arm64) ;;
        *) die "unsupported CPU $(uname -m); images exist for x86_64 and arm64" ;;
    esac
    command -v curl >/dev/null || die "curl is missing: apt-get install -y curl"
    if [ -e "$DIR/.env" ]; then
        die "$DIR/.env exists, hostwatch is already installed here.
Update: cd $DIR && docker compose pull && docker compose up -d"
    fi
    mkdir -p "$DIR"
    local free_kb
    free_kb=$(df -Pk "$DIR" | awk 'NR == 2 {print $4}')
    if [ "${free_kb:-0}" -lt $((MIN_FREE_GB * 1024 * 1024)) ]; then
        warn "less than $MIN_FREE_GB GB free on the disk of $DIR; the images take about 5 GB"
    fi
    if command -v ss >/dev/null; then
        local port
        for port in 80 443; do
            if ss -Hltn "sport = :$port" | grep -q .; then
                die "port $port is taken by another program. hostwatch needs 80 and 443 for the
login page; install it on a server where they are free (README: why a separate VPS)"
            fi
        done
    fi
}

install_docker() {
    if ! command -v docker >/dev/null; then
        say "Installing Docker with the official script from get.docker.com..."
        local script
        script=$(mktemp)
        if ! { curl -fsSL https://get.docker.com -o "$script" && sh "$script"; }; then
            rm -f "$script"
            command -v apt-get >/dev/null || die "Docker install failed; install Docker Engine by hand and run install.sh again"
            warn "get.docker.com failed; trying the docker.io package of the distribution"
            apt-get update
            apt-get install -y docker.io
        fi
        rm -f "$script"
    fi
    if ! docker compose version >/dev/null 2>&1; then
        say "Installing the Docker Compose plugin..."
        command -v apt-get >/dev/null || die "docker compose is missing; install the Compose plugin and run install.sh again"
        apt-get install -y docker-compose-plugin || apt-get install -y docker-compose-v2 ||
            die "could not install docker compose"
    fi
    if ! docker info >/dev/null 2>&1; then
        command -v systemctl >/dev/null && systemctl enable --now docker
        docker info >/dev/null 2>&1 || die "Docker is installed but not running"
    fi
    say "Docker: $(docker --version)"
}

download_files() {
    local name
    for name in "${FILES[@]}"; do
        curl -fsSL "$BASE_URL/$name" -o "$DIR/$name.part" || die "cannot download $BASE_URL/$name"
        mv "$DIR/$name.part" "$DIR/$name"
    done
}

# The token goes to curl through stdin, so it does not show up in the process list.
telegram() { # telegram <method>; prints the answer
    printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$BOT_TOKEN" "$1" |
        curl -sS --max-time 15 -K -
}

token_works() { # 0: works or cannot be checked now; 1: Telegram rejects it
    local answer
    if ! answer=$(telegram getMe 2>/dev/null); then
        warn "api.telegram.org is unreachable, the token is not checked. If Telegram is blocked
from this server, put a proxy into TG_PROXY in $DIR/.env and run: docker compose up -d"
        return 0
    fi
    case "$answer" in
        *'"ok":true'*)
            BOT_NAME=$(printf '%s' "$answer" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
            say "The token works: @$BOT_NAME"
            return 0
            ;;
    esac
    return 1
}

read_token() {
    local from_env=${BOT_TOKEN:+yes}
    while true; do
        if [ -z "${BOT_TOKEN:-}" ]; then
            BOT_TOKEN=$(ask "Bot token from @BotFather")
        fi
        if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
            [ -z "$from_env" ] || die "BOT_TOKEN does not look like a token from @BotFather"
            say "This does not look like a bot token. It has the form 123456789:AAE..."
        elif token_works; then
            return
        else
            [ -z "$from_env" ] || die "Telegram does not accept BOT_TOKEN"
            say "Telegram does not accept this token. Copy it from @BotFather once more."
        fi
        BOT_TOKEN=""
    done
}

find_owner_id() { # the sender of the latest message to the bot
    local answer
    answer=$(telegram getUpdates 2>/dev/null) || return 1
    printf '%s' "$answer" | grep -o '"from":{"id":[0-9]*' | tail -n 1 | grep -o '[0-9]*$'
}

read_owner_id() {
    local found
    while [[ ! "${OWNER_ID:-}" =~ ^[0-9]+$ ]]; do
        if [ -n "${OWNER_ID:-}" ] && [ -z "$TTY" ]; then
            die "OWNER_ID must be your numeric Telegram user ID"
        fi
        OWNER_ID=$(ask "Your Telegram user ID, digits (empty: find it from a message to the bot)")
        if [ -z "$OWNER_ID" ] && [ -n "$BOT_NAME" ]; then
            ask "Send any message to @$BOT_NAME in Telegram, then press Enter" >/dev/null
            if found=$(find_owner_id) && [ -n "$found" ]; then
                say "The message came from user ID $found."
                case "$(ask "Is that you? (y/n)" y)" in
                    y | Y | yes) OWNER_ID=$found ;;
                esac
            else
                say "No message found. Try again or type the ID (the bot @userinfobot tells it)."
            fi
        fi
    done
}

read_address() {
    if [ -z "${HOSTWATCH_DOMAIN+set}" ] && [ -n "$TTY" ]; then
        HOSTWATCH_DOMAIN=$(ask "Domain for the login page and the Mini App, if it points to this server (empty: none)")
    fi
    HOSTWATCH_DOMAIN=${HOSTWATCH_DOMAIN:-}
    if [ -z "${HOSTWATCH_IP:-}" ]; then
        local detected
        detected=$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)
        if [ -n "$TTY" ]; then
            HOSTWATCH_IP=$(ask "Public IPv4 address of this server" "$detected")
        else
            HOSTWATCH_IP=$detected
        fi
    fi
    if [ -n "$HOSTWATCH_IP" ] && [[ ! "$HOSTWATCH_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
        die "HOSTWATCH_IP must be an IPv4 address, got '$HOSTWATCH_IP'"
    fi
    [ -n "$HOSTWATCH_IP$HOSTWATCH_DOMAIN" ] || die "set HOSTWATCH_IP or HOSTWATCH_DOMAIN"
}

write_env() {
    HOSTWATCH_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
    export BOT_TOKEN OWNER_ID HOSTWATCH_KEY HOSTWATCH_IP HOSTWATCH_DOMAIN
    (
        umask 077
        awk '
            /^(BOT_TOKEN|OWNER_ID|HOSTWATCH_KEY|HOSTWATCH_IP|HOSTWATCH_DOMAIN)=/ {
                name = substr($0, 1, index($0, "=") - 1)
                print name "=" ENVIRON[name]
                next
            }
            { print }
        ' "$DIR/.env.example" >"$DIR/.env"
    )
    chmod 600 "$DIR/.env"
    mkdir -p "$DIR/data"
    chown "$CONTAINER_UID:$CONTAINER_UID" "$DIR/data"
    chmod 700 "$DIR/data"
}

show_key() {
    say ""
    say "=================================================================="
    say "HOSTWATCH_KEY=$HOSTWATCH_KEY"
    say "=================================================================="
    say "Save this key in your password manager now. It encrypts the hoster"
    say "keys and sessions in the database and in its backups. The key is"
    say "also in $DIR/.env, but a backup without it cannot be read."
    if [ -n "$TTY" ]; then
        ask "Press Enter when the key is saved" >/dev/null
    fi
    say ""
}

start() {
    say "Starting the containers. The first start downloads about 1.5 GB of images..."
    cd "$DIR"
    docker compose up -d
    say "Waiting for the bot to connect to Telegram..."
    local logs
    for _ in $(seq 1 45); do
        logs=$(docker compose logs --no-color hostwatch 2>&1 || true)
        case "$logs" in
            *"connected to Telegram"*)
                say ""
                say "hostwatch is running in $DIR."
                say "Open https://t.me/${BOT_NAME:-your_bot} and send /start."
                say "Logs: cd $DIR && docker compose logs -f hostwatch"
                return 0
                ;;
            *"does not accept BOT_TOKEN"*)
                die "Telegram does not accept BOT_TOKEN. Fix it in $DIR/.env and run:
cd $DIR && docker compose up -d"
                ;;
        esac
        sleep 2
    done
    die "the bot has not connected to Telegram in 90 seconds. See:
cd $DIR && docker compose logs hostwatch"
}

main() {
    check_system
    # Questions first: installing Docker and downloading take a few minutes.
    read_token
    read_owner_id
    read_address
    install_docker
    download_files
    write_env
    show_key
    start
}

main "$@"
