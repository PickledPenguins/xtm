# bash completion for xtm
#
# Install with:  ./install.sh --completion
# or by hand:    cp xtm-completion.bash ~/.local/share/bash-completion/completions/xtm
#
# Completes actions and options, profile names for the options that take one,
# and session names for the options that take one. Names are read from xtm
# itself, so completion always reflects the config file actually in use.

_xtm_profiles() {
    # --quiet keeps informational logging off stderr; profile names are
    # printed to stdout one per line, with the effective one marked by a
    # trailing " *" which is stripped here. Profiles bound to other machines
    # are omitted, matching what the tool will actually accept.
    xtm --list-profiles --quiet 2>/dev/null | sed 's/ \*$//'
}

_xtm_sessions() {
    # Slot names and resolved session names from the current profile, plus
    # any running tmux session, so that --close, --detach and --focus all
    # complete either spelling and can reach a stray too.
    {
        xtm --list --json --quiet 2>/dev/null |
            sed -n 's/.*"\(name\|session\)": "\([^"]*\)".*/\2/p'
        tmux list-sessions -F '#{session_name}' 2>/dev/null
    } | sort -u
}

_xtm() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    local actions="--list --open --reset --reset-all --close --close-all
        --focus --update-profile --new-profile --set --delete-session
        --list-profiles --current-profile --validate --edit --delete-profile
        --copy-profile --rename-profile --make-global --detach"
    local options="--profile --auto --all --verbose --on-switch --capture-new
        --detach-mode --config --state-dir --json --dry-run --yes --verify
        --no-frame-compensation --debug --quiet --log-level --log-file
        --help --version"

    case "$prev" in
        -p|--profile|--delete-profile|--copy-profile|--rename-profile|--make-global)
            COMPREPLY=( $(compgen -W "$(_xtm_profiles)" -- "$cur") )
            return 0 ;;
        -o|--open|-k|--close|-f|--focus|-s|--set|-D|--delete-session|-t|--detach)
            COMPREPLY=( $(compgen -W "$(_xtm_sessions)" -- "$cur") )
            return 0 ;;
        -n|--new-profile)
            # A new profile's name is arbitrary, so there is nothing to
            # suggest; offering existing names would be actively wrong,
            # since an existing name is rejected.
            COMPREPLY=()
            return 0 ;;
        -c|--config|-L|--log-file)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0 ;;
        --state-dir)
            COMPREPLY=( $(compgen -d -- "$cur") )
            return 0 ;;
        --log-level)
            COMPREPLY=( $(compgen -W "debug info warning error critical" -- "$cur") )
            return 0 ;;
        --on-switch)
            COMPREPLY=( $(compgen -W "leave detach kill" -- "$cur") )
            return 0 ;;
    esac

    # The second argument of --copy-profile and --rename-profile is a new
    # name, so it is left uncompleted the same way --new-profile is.
    if [ "$COMP_CWORD" -ge 2 ]; then
        case "${COMP_WORDS[COMP_CWORD-2]}" in
            --copy-profile|--rename-profile)
                COMPREPLY=()
                return 0 ;;
        esac
    fi

    # A bare word is the positional profile name, so profiles are offered
    # alongside the flags rather than only after --profile.
    COMPREPLY=( $(compgen -W "$actions $options $(_xtm_profiles)" -- "$cur") )
    return 0
}

complete -F _xtm xtm
