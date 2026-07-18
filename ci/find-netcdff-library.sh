#!/bin/sh
set -eu

if command -v nf-config >/dev/null 2>&1; then
    for token in $(nf-config --flibs); do
        case "$token" in
            -L*)
                library_dir=${token#-L}
                if [ -e "${library_dir}/libnetcdff.so" ]; then
                    readlink -f "${library_dir}/libnetcdff.so"
                    exit 0
                fi
                ;;
        esac
    done
fi

library_path=$(find /usr/local/lib /usr/lib /lib \
    -type f -o -type l 2>/dev/null \
    | grep '/libnetcdff\.so$' \
    | head -n 1 || true)

if [ -n "$library_path" ]; then
    readlink -f "$library_path"
    exit 0
fi

echo "Unable to locate libnetcdff.so" >&2
exit 1
