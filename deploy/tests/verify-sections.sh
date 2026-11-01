# shellcheck shell=sh
# SteamHangar WP VERIFY-FAST-1 -- section selection and timing for
# deploy/tests/verify-stack.sh. Sourced (POSIX sh), never run on its own.
#
# VERIFY_SECTIONS is a comma list of section ids as verify-stack.sh numbers
# them, ranges and presets:
#
#     full                     every section (the default when unset or empty)
#     lean                     1,3,8,5a-5d,6a-6e (no CDN or internet step)
#     1 .. 11                  a whole section
#     4-settings 3.5-runner 4a 5a 5a2 6i-core ...
#                              one step; step ids exist for sections 4, 5, 6
#     5a-5d  1-3               a range in script order, within one section
#                              (steps) or across sections (section numbers)
#     lean,5e                  presets and ids combine as a union
#
# Unknown ids, backwards ranges and ranges across step groups fail before
# anything starts. Dependencies are added, not refused: 5f and 5g need 5e
# (the chunk it fetched), 9 needs 3 (its bind dir and env files are made in
# 3p). Two derived needs are not ids: the image builds of section 2 run when
# any section from 2 on that uses an image is selected, and the stack start
# of section 4 runs once when any step of 4, 5, 6 or section 10 is selected.
#
# Every name this file defines starts with vs_ / VS_.

# Leaves in script order. Section 2, 3 and 7-11 are leaves themselves;
# sections 4, 5 and 6 are groups of steps.
VS_GROUP_4="4-settings 3.5-runner 4a 4b 4c 4d"
VS_GROUP_5="5a 5a2 5b 5c 5d 5e 5f 5g 5h 5i 5j"
VS_GROUP_6="6a 6b 6c 6d 6e 6f 6g 6h 6i 6i-core 6s 6v 6w 6x 6j 6k 6l"
VS_SECTIONS="1 2 3 4 5 6 7 8 9 10 11"
VS_LEAVES="1 2 3 $VS_GROUP_4 $VS_GROUP_5 $VS_GROUP_6 7 8 9 10 11"

VS_PRESET_LEAN="1,3,8,5a-5d,6a-6e"

# Leaf dependencies: "<leaf>:<needed leaf>".
VS_DEPS="5f:5e 5g:5e 9:3"

# vs_in <word> <list...>: 0 if <word> is one of the list's words.
vs_in() {
    vs_w=$1
    shift
    case " $* " in *" $vs_w "*) return 0 ;; esac
    return 1
}

# vs_group <section>: the leaves of one section.
vs_group() {
    case "$1" in
        4) printf '%s\n' "$VS_GROUP_4" ;;
        5) printf '%s\n' "$VS_GROUP_5" ;;
        6) printf '%s\n' "$VS_GROUP_6" ;;
        *) printf '%s\n' "$1" ;;
    esac
}

# vs_section_of <leaf>: the section a leaf belongs to.
vs_section_of() {
    if vs_in "$1" $VS_GROUP_4; then echo 4
    elif vs_in "$1" $VS_GROUP_5; then echo 5
    elif vs_in "$1" $VS_GROUP_6; then echo 6
    else echo "$1"
    fi
}

# vs_slice <from> <to> <list...>: the words of list from <from> to <to>.
vs_slice() {
    vs_from=$1 vs_to=$2 vs_on=0 vs_out=
    shift 2
    for vs_x in "$@"; do
        [ "$vs_x" = "$vs_from" ] && vs_on=1
        [ "$vs_on" = 1 ] && vs_out="$vs_out $vs_x"
        if [ "$vs_x" = "$vs_to" ]; then
            [ "$vs_on" = 1 ] && { printf '%s\n' "${vs_out# }"; return 0; }
            return 1   # <to> before <from>: a backwards range
        fi
    done
    return 1
}

vs_error() {
    printf 'verify-stack: VERIFY_SECTIONS=%s: %s\n' "$VS_SPEC" "$*" >&2
    printf 'verify-stack: valid: presets full, lean; sections %s; steps %s; %s; %s; ranges like 5a-5d or 1-3\n' \
        "1-11" "$VS_GROUP_4" "$VS_GROUP_5" "$VS_GROUP_6" >&2
}

# vs_expand_token <token>: the leaves one token stands for, or 1 (message
# already printed).
vs_expand_token() {
    vs_t=$1
    case "$vs_t" in
        full) printf '%s\n' "$VS_LEAVES"; return 0 ;;
        # subshell: vs_expand_list's loop variables must not clobber the
        # caller's (a preset inside a list, e.g. lean,5e)
        lean) (vs_expand_list "$VS_PRESET_LEAN"); return $? ;;
    esac
    if vs_in "$vs_t" $VS_SECTIONS; then vs_group "$vs_t"; return 0; fi
    if vs_in "$vs_t" $VS_LEAVES; then printf '%s\n' "$vs_t"; return 0; fi
    # A range: split at the "-" after a known id whose rest is a known id
    # (ids may contain "-" themselves: 4-settings, 3.5-runner, 6i-core).
    for vs_a in $VS_SECTIONS $VS_LEAVES; do
        case "$vs_t" in "$vs_a"-?*) ;; *) continue ;; esac
        vs_b=${vs_t#"$vs_a"-}
        if vs_in "$vs_a" $VS_SECTIONS && vs_in "$vs_b" $VS_SECTIONS; then
            if ! vs_secs=$(vs_slice "$vs_a" "$vs_b" $VS_SECTIONS); then
                vs_error "range '$vs_t' runs backwards"
                return 1
            fi
            for vs_s in $vs_secs; do vs_group "$vs_s"; done | tr '\n' ' '
            echo
            return 0
        fi
        if vs_in "$vs_b" $VS_LEAVES && ! vs_in "$vs_a" $VS_SECTIONS && ! vs_in "$vs_b" $VS_SECTIONS; then
            vs_ga=$(vs_section_of "$vs_a")
            if [ "$vs_ga" != "$(vs_section_of "$vs_b")" ]; then
                vs_error "range '$vs_t' crosses sections; step ranges stay inside one section (use section numbers, e.g. 4-6)"
                return 1
            fi
            vs_members=$(vs_group "$vs_ga")
            # shellcheck disable=SC2086 # word lists by design
            if ! vs_slice "$vs_a" "$vs_b" $vs_members; then
                vs_error "range '$vs_t' runs backwards"
                return 1
            fi
            return 0
        fi
        if vs_in "$vs_b" $VS_SECTIONS $VS_LEAVES; then
            vs_error "range '$vs_t' mixes a section number and a step id; use either '4-6' or '5a-5d'"
            return 1
        fi
    done
    case "$vs_t" in
        [4-6][!0-9]*)
            vs_error "unknown step id '$vs_t' in section ${vs_t%%[!0-9]*}" ;;
        [1-9]*[!0-9]*)
            vs_error "unknown section id '$vs_t' (step ids exist for sections 4, 5 and 6 only; select the whole section, e.g. '${vs_t%%[!0-9]*}')" ;;
        *)
            vs_error "unknown section id or preset '$vs_t'" ;;
    esac
    return 1
}

# vs_expand_list <comma list>: all leaves of all tokens (unsorted, may repeat).
vs_expand_list() {
    vs_rest=$1,
    while [ -n "$vs_rest" ]; do
        vs_tok=${vs_rest%%,*}
        vs_rest=${vs_rest#*,}
        # trim blanks around the token
        vs_tok=$(printf '%s' "$vs_tok" | tr -d ' \t')
        [ -n "$vs_tok" ] || continue
        vs_expand_token "$vs_tok" || return 1
    done
}

# vs_resolve <spec>: sets VS_SPEC, VS_SELECTED (leaves in script order),
# VS_ADDED ("<leaf>:<by>" pairs added as dependencies), VS_NEED_IMAGES and
# VS_NEED_STACK (yes/no). Returns 2 with a message on stderr on bad input.
vs_resolve() {
    VS_SPEC=$1
    [ -n "$VS_SPEC" ] || VS_SPEC=full
    if ! vs_raw=$(vs_expand_list "$VS_SPEC"); then
        return 2
    fi
    vs_raw=$(printf '%s\n' "$vs_raw" | tr '\n' ' ')
    if [ -z "$(printf '%s' "$vs_raw" | tr -d ' ')" ]; then
        vs_error "selects nothing"
        return 2
    fi
    VS_ADDED=
    for vs_d in $VS_DEPS; do
        vs_by=${vs_d%%:*}
        vs_need=${vs_d#*:}
        if vs_in "$vs_by" $vs_raw && ! vs_in "$vs_need" $vs_raw; then
            vs_raw="$vs_raw $vs_need"
            VS_ADDED="$VS_ADDED $vs_need:$vs_by"
        fi
    done
    VS_ADDED=${VS_ADDED# }
    VS_SELECTED=
    for vs_l in $VS_LEAVES; do
        vs_in "$vs_l" $vs_raw && VS_SELECTED="$VS_SELECTED $vs_l"
    done
    VS_SELECTED=${VS_SELECTED# }
    VS_NEED_IMAGES=no
    VS_NEED_STACK=no
    for vs_l in $VS_SELECTED; do
        case "$(vs_section_of "$vs_l")" in
            1|3) ;;
            # 7 starts only vault-dns itself, but its 7h check reads
            # $API_URL/v1/about, so it needs the started stack too.
            4|5|6|7|10) VS_NEED_IMAGES=yes; VS_NEED_STACK=yes ;;
            *) VS_NEED_IMAGES=yes ;;
        esac
    done
    return 0
}

# vs_want <leaf>: 0 if the leaf is selected.
vs_want() { vs_in "$1" $VS_SELECTED; }

# vs_want_section <section>: 0 if any leaf of the section is selected.
vs_want_section() {
    for vs_l in $(vs_group "$1"); do
        vs_want "$vs_l" && return 0
    done
    return 1
}

# vs_skip <id> [note]: the one line for a part that does not run.
vs_skip() {
    if [ $# -gt 1 ]; then printf 'skip %s  (%s)\n' "$1" "$2"; else printf 'skip %s\n' "$1"; fi
}

# --- timing --------------------------------------------------------------------
# Lines start with "[time] " so a transcript can be grepped:
#   [time] start <id> <UTC ISO 8601>
#   [time] end <id> <UTC ISO 8601> <seconds>s
#   [time] section <id> <seconds>s      (summary, one per timed part)
#   [time] total <seconds>s
vs_epoch() { date -u '+%s'; }
vs_iso() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
VS_T_START=$(vs_epoch)
VS_TIMES=
VS_T_ID=

# vs_begin <id>: start timing a part (closes an open one first).
vs_begin() {
    [ -n "$VS_T_ID" ] && vs_end
    VS_T_ID=$1
    VS_T0=$(vs_epoch)
    printf '[time] start %s %s\n' "$VS_T_ID" "$(vs_iso)"
}

# vs_end: stop timing the open part, if any.
vs_end() {
    [ -n "$VS_T_ID" ] || return 0
    vs_secs=$(( $(vs_epoch) - VS_T0 ))
    printf '[time] end %s %s %ss\n' "$VS_T_ID" "$(vs_iso)" "$vs_secs"
    VS_TIMES="$VS_TIMES $VS_T_ID:$vs_secs"
    VS_T_ID=
}

# vs_section <section>: 0 and start its timer if any of its leaves is
# selected, else print the skip line and return 1.
vs_section() {
    if vs_want_section "$1"; then vs_begin "$1"; return 0; fi
    vs_skip "$1"
    return 1
}

# vs_step <leaf>: 0 if selected, else print the skip line and return 1.
vs_step() {
    vs_want "$1" && return 0
    vs_skip "$1"
    return 1
}

# vs_report: the per-part summary and the total since this file was sourced.
vs_report() {
    vs_end
    for vs_p in $VS_TIMES; do
        printf '[time] section %s %ss\n' "${vs_p%%:*}" "${vs_p#*:}"
    done
    printf '[time] total %ss\n' "$(( $(vs_epoch) - VS_T_START ))"
}

# vs_plan: what a run with this selection does (VERIFY_PLAN=1), no Docker.
vs_plan() {
    printf 'VERIFY_SECTIONS=%s\n' "$VS_SPEC"
    printf 'run: %s\n' "$VS_SELECTED"
    for vs_a in $VS_ADDED; do
        printf 'add %s  (needed by %s)\n' "${vs_a%%:*}" "${vs_a#*:}"
    done
    printf 'image builds: %s\n' "$VS_NEED_IMAGES"
    printf 'stack start: %s\n' "$VS_NEED_STACK"
    for vs_l in $VS_LEAVES; do
        vs_want "$vs_l" || printf 'skip %s\n' "$vs_l"
    done
}
