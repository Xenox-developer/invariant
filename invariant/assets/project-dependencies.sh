# Login shells reset PATH in /etc/profile. Restore project package commands,
# keeping immutable system executables first. This file is part of the image.
case ":$PATH:" in
    *:/workspace/.invariant-deps/python/bin:*) ;;
    *) PATH="$PATH:/workspace/.invariant-deps/python/bin" ;;
esac
export PATH
