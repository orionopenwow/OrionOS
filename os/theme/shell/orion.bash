# OrionOS interactive bash: the "Event Horizon" prompt and the long-command star (Art Update §7).
# Sourced from /etc/bash.bashrc (os/image/art-boot.sh adds the line). Your own ~/.bashrc can override anything.
#
#   ◉ ~/projects/openwow  main ✚2  ⏱ 2.1s
#   › _
#
# Starship draws it when installed (config: /usr/share/orion/shell/starship.toml, unless you have your own
# ~/.config/starship.toml); otherwise the small prompt below does the same.
# A command that runs longer than ORION_LONG_COMMAND seconds (default 30) and finishes while you're looking at
# another window sends a notification and a small star to Orion's core. ORION_LONG_COMMAND=0 turns it off.

case $- in *i*) ;; *) return ;; esac
[ -n "${ORION_SHELL_LOADED:-}" ] && return
ORION_SHELL_LOADED=1

ORION_LONG_COMMAND="${ORION_LONG_COMMAND:-30}"
_orion_api="${ORION_API:-http://127.0.0.1:9120}"
# Orion's API needs this run's key (review B1). It's read from the user's private token file and handed to curl on
# file descriptor 3, so it never shows up in the process list.
_orion_token() {
  local f
  for f in "${ORION_TOKEN_FILE:-}" "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/orion/daemon.token" \
           "${ORION_DATA_DIR:-$HOME/.orion}/daemon.token"; do
    if [ -n "$f" ] && [ -r "$f" ]; then cat "$f"; return 0; fi
  done
  return 0
}
_orion_curl() {
  command curl -H @/dev/fd/3 "$@" 3<<EOF
Authorization: Bearer $(_orion_token)
EOF
}

# ---------------------------------------------------------------- timing every command
_orion_t0=""
_orion_cmd=""
_orion_dur=""
_orion_preexec() {
  # DEBUG runs before every simple command, PROMPT_COMMAND included: only the first one after a prompt counts.
  [ -n "${_orion_in_prompt:-}" ] && return
  [ -n "$_orion_t0" ] && return
  _orion_t0="${EPOCHREALTIME:-$SECONDS}"
  _orion_cmd="$BASH_COMMAND"
}
trap '_orion_preexec' DEBUG

_orion_fmt_dur() {   # milliseconds -> 2.1s / 1m04s / 1h02m
  local ms=$1
  if [ "$ms" -lt 60000 ]; then printf '%d.%ds' $((ms / 1000)) $(((ms % 1000) / 100))
  elif [ "$ms" -lt 3600000 ]; then printf '%dm%02ds' $((ms / 60000)) $(((ms % 60000) / 1000))
  else printf '%dh%02dm' $((ms / 3600000)) $(((ms % 3600000) / 60000)); fi
}

# Is this terminal the focused window? (Sway only; anywhere else we can't tell, so we stay quiet.)
_orion_focused() {
  [ -n "${SWAYSOCK:-}" ] || return 0
  command -v swaymsg >/dev/null && command -v jq >/dev/null || return 0
  local fp p=$$ _
  fp="$(swaymsg -t get_tree 2>/dev/null | jq -r '.. | objects | select(.focused? == true) | .pid // empty' 2>/dev/null | head -n1)"
  [ -n "$fp" ] || return 0
  for _ in 1 2 3 4 5 6 7 8; do
    [ "$p" = "$fp" ] && return 0
    p="$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)"
    [ -n "$p" ] && [ "$p" -gt 1 ] || return 1
  done
  return 1
}

_orion_long_done() {   # $1 exit status, $2 milliseconds, $3 the command
  local status=$1 ms=$2 cmd=$3 dur title
  dur="$(_orion_fmt_dur "$ms")"
  cmd="${cmd:0:120}"
  if [ "$status" -eq 0 ]; then title="Done in $dur"; else title="Finished with an error ($status) after $dur"; fi
  if command -v notify-send >/dev/null; then
    local icon=/usr/share/orion/brand/mark.png
    [ -f "$icon" ] || icon=utilities-terminal
    notify-send -a "Orion" -i "$icon" "$title" "$cmd" 2>/dev/null &
  fi
  # A small star for the core (the daemon may not be running: then nothing happens).
  if command -v curl >/dev/null; then
    local body
    body="$(printf '{"source":"terminal","ok":%s,"seconds":%d,"title":"%s"}' \
      "$([ "$status" -eq 0 ] && echo true || echo false)" $((ms / 1000)) \
      "$(printf '%s' "$cmd" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\000-\037')")"
    _orion_curl -fsS -m 2 -X POST -H 'content-type: application/json' --data-binary "$body" "$_orion_api/events/star" >/dev/null 2>&1 &
  fi
  disown -a 2>/dev/null
}

# ---------------------------------------------------------------- after every command
_orion_precmd() {
  local status=$?
  _orion_in_prompt=1
  _orion_dur=""
  if [ -n "$_orion_t0" ]; then
    local now ms
    if [ -n "${EPOCHREALTIME:-}" ]; then
      now="$EPOCHREALTIME"
      ms=$(( (${now%.*} - ${_orion_t0%.*}) * 1000 + 10#${now#*.} / 1000 - 10#${_orion_t0#*.} / 1000 ))
    else
      ms=$(( (SECONDS - ${_orion_t0%.*}) * 1000 ))
    fi
    [ "$ms" -ge 500 ] && _orion_dur="$(_orion_fmt_dur "$ms")"
    if [ "${ORION_LONG_COMMAND:-0}" -gt 0 ] && [ "$ms" -ge $((ORION_LONG_COMMAND * 1000)) ] && ! _orion_focused; then
      _orion_long_done "$status" "$ms" "$_orion_cmd"
    fi
  fi
  _orion_status=$status
  _orion_t0=""
  return $status
}
# The last thing PROMPT_COMMAND runs: from here on, the DEBUG trap times your next command.
_orion_prompt_end() { _orion_in_prompt=""; }

# ---------------------------------------------------------------- the prompt
if command -v starship >/dev/null 2>&1; then
  [ -f "$HOME/.config/starship.toml" ] || export STARSHIP_CONFIG=/usr/share/orion/shell/starship.toml
  eval "$(starship init bash)"
  # starship sets its own PROMPT_COMMAND (and keeps our DEBUG trap); ours goes around it
  PROMPT_COMMAND="_orion_precmd;${PROMPT_COMMAND:+$PROMPT_COMMAND;}_orion_prompt_end"
else
  _orion_c() { printf '\001\033[38;2;%sm\002' "$1"; }
  _o_violet="$(_orion_c '157;133;255')"; _o_ring="$(_orion_c '124;92;255')"; _o_cyan="$(_orion_c '0;229;255')"
  _o_ember="$(_orion_c '255;198;107')"; _o_dim="$(_orion_c '122;130;176')"; _o_rose="$(_orion_c '255;92;138')"
  _o_reset=$'\001\033[0m\002'

  # Anything that goes into PS1 is escaped, so a folder or branch called $(...) is just text.
  _orion_esc() { local v=${1//\\/\\\\}; v=${v//\$/\\\$}; v=${v//\`/\\\`}; printf '%s' "$v"; }
  _orion_git() {
    command -v git >/dev/null || return
    local b n
    b="$(git symbolic-ref --short -q HEAD 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)" || return
    [ -n "$b" ] || return
    printf ' %s%s' "$_o_violet" "$(_orion_esc "$b")"
    n="$(timeout 0.4 git status --porcelain=v1 2>/dev/null | wc -l)"
    [ "${n:-0}" -gt 0 ] && printf ' %s✚%s' "$_o_ember" "$n"
  }
  _orion_ps1() {
    local line
    line="${_o_ring}◉ ${_o_cyan}\\w$(_orion_git)"
    [ -n "$_orion_dur" ] && line+="  ${_o_dim}⏱ ${_orion_dur}"
    [ "${_orion_status:-0}" -ne 0 ] && line+="  ${_o_rose}✗ ${_orion_status}"
    PS1="\\n${line}${_o_reset}\\n${_o_violet}›${_o_reset} "
  }
  PROMPT_COMMAND="_orion_precmd; _orion_ps1${PROMPT_COMMAND:+; $PROMPT_COMMAND}; _orion_prompt_end"
  PS2="${_o_dim}…${_o_reset} "
fi
