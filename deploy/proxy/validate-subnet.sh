#!/bin/sh
# SteamHangar vault-proxy -- strict IPv4 CIDR check for VAULT_EGRESS_SUBNET
# (WP DEPLOY-FIX-2, ADR-0011 addendum 2026-10-01).
#
# Sourced by docker-entrypoint.sh (same pattern as validate-hostname.sh), and
# by api/tests/test_eg1_egress_lock.py through a real `sh` subprocess, so the
# test pins the shipped logic rather than a Python copy of it.
#
# validate_egress_subnet VALUE
#   exit 0  VALUE is a dotted quad (four decimal octets 0-255, no leading
#           zeros, no sign, no whitespace) followed by "/" and a decimal
#           prefix length 8-30 (no leading zero), with every host bit
#           zero (a network address: 172.30.239.0/24, never
#           172.30.239.5/24). Nothing else.
#   exit 1  anything else, including the empty string.
# Prints nothing. The value is used verbatim on success: the check only
# accepts strings that are already canonical, so there is nothing to
# normalize.
#
# Why so strict: the value becomes tinyproxy's client `Allow` line. A
# mis-parsed or wildcard-ish value must refuse to start (fail closed), never
# silently widen who may use the proxy. Prefix 8-30: shorter than /8 is not a
# sensible single bridge network, and /31-/32 leave no room for the gateway
# plus two containers.

validate_egress_subnet() {
    _vs_value=$1
    case $_vs_value in
        '' | *[!0-9./]*) return 1 ;;
    esac
    _vs_ip=${_vs_value%/*}
    _vs_prefix=${_vs_value#*/}
    # Exactly one "/": with none, both expansions return the whole value; with
    # two or more, the recombination below differs from the input.
    [ "$_vs_ip/$_vs_prefix" = "$_vs_value" ] || return 1
    case $_vs_prefix in
        '' | *[!0-9]* | 0* | ???*) return 1 ;;
    esac
    [ "$_vs_prefix" -ge 8 ] && [ "$_vs_prefix" -le 30 ] || return 1

    _vs_rest=$_vs_ip
    _vs_count=0
    _vs_int=0
    while :; do
        _vs_octet=${_vs_rest%%.*}
        case $_vs_octet in
            '' | *[!0-9]* | 0?* | ????*) return 1 ;;
        esac
        [ "$_vs_octet" -le 255 ] || return 1
        _vs_count=$((_vs_count + 1))
        [ "$_vs_count" -le 4 ] || return 1
        # Safe in $(( )): leading zeros are rejected above, so no octet is
        # ever read as octal.
        _vs_int=$((_vs_int * 256 + _vs_octet))
        case $_vs_rest in
            *.*) _vs_rest=${_vs_rest#*.} ;;
            *) break ;;
        esac
    done
    [ "$_vs_count" -eq 4 ] || return 1
    # Host bits must be zero: the address modulo the block size (2^(32-n)).
    # Docker and tinyproxy could each read a value with host bits set in
    # their own way; only a network address means the same thing to both.
    [ $((_vs_int % (1 << (32 - _vs_prefix)))) -eq 0 ] || return 1
    return 0
}
